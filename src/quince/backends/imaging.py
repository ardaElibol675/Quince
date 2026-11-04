"""Image conversion with Pillow.

HEIC goes through pillow-heif, AVIF through Pillow's own plugin, SVG is rendered by
``rsvg-convert`` (SVG → PDF stays vector), and PDF output is built with PyMuPDF so a JPEG is
embedded as it is, without a second lossy encoding.

What every conversion does:

- **EXIF orientation** is applied to the pixels; the orientation tag is then removed, so
  the result looks the same in every viewer.
- **Metadata** (EXIF, including GPS) and the **color profile** are kept when the target can
  store them. Targets without a profile (GIF, BMP) get the pixels converted to sRGB.
- **Transparency** is kept when the target has alpha; otherwise transparent areas become
  white (JPG, BMP), and a note says so.
- **Animation** is kept between GIF and WebP. Other targets get the first frame (the first
  page of a TIFF, except into PDF, which gets every page), and a note says so.
- 16-bit images are scaled to 8 bits for targets without 16-bit support (never clipped),
  CMYK is converted to RGB for targets without CMYK.
- Targets with a size limit (WebP, JPG, GIF) explain it instead of failing obscurely.
"""

from __future__ import annotations

import io
import logging
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from PIL import Image, ImageCms, ImageOps, ImageSequence, UnidentifiedImageError

from quince.backends import process
from quince.core import formats
from quince.core.jobs import JobContext, JobError
from quince.core.routes import Route

log = logging.getLogger(__name__)

WHITE = (255, 255, 255)
EXIF_ORIENTATION = 0x0112
DEFAULT_PDF_DPI = 96.0
PDF_DPI_RANGE = (30.0, 2400.0)
PDF_JPEG_QUALITY = 92
SVG_TIMEOUT_S = 120
# Sources whose pixels are exact: into a PDF they go as PNG, others as JPEG.
LOSSLESS_SOURCES = frozenset({"png", "bmp", "gif", "tiff", "svg"})
ANIMATION_TARGETS = frozenset({"gif", "webp"})
FLATTENED_NOTE = "Transparent areas were filled with white"


@dataclass(frozen=True)
class Target:
    """How Pillow writes one target format, and what the format can hold."""

    pil_format: str
    alpha: bool
    gray: bool = False  # can store "L" as it is
    cmyk: bool = False
    deep: bool = False  # can store 16-bit grayscale ("I;16")
    icc: bool = True
    exif: bool = True
    dpi: bool = False
    max_side: int | None = None
    options: Mapping[str, Any] = field(default_factory=dict)


TARGETS: Mapping[str, Target] = MappingProxyType(
    {
        "jpg": Target(
            "JPEG", alpha=False, gray=True, cmyk=True, dpi=True, max_side=65500,
            options={"quality": 90, "optimize": True},
        ),
        "png": Target("PNG", alpha=True, gray=True, deep=True, dpi=True),
        "webp": Target("WEBP", alpha=True, max_side=16383, options={"quality": 90, "method": 4}),
        "heic": Target("HEIF", alpha=True, options={"quality": 85}),
        "tiff": Target(
            "TIFF", alpha=True, gray=True, cmyk=True, deep=True, dpi=True,
            options={"compression": "tiff_adobe_deflate"},
        ),
        "avif": Target("AVIF", alpha=True, options={"quality": 80}),
        "bmp": Target("BMP", alpha=False, gray=True, icc=False, exif=False, dpi=True),
        "gif": Target("GIF", alpha=True, icc=False, exif=False, max_side=65535),
    }
)  # fmt: skip

_heif_ready = False


def _register_heif() -> None:
    global _heif_ready
    if not _heif_ready:
        import pillow_heif

        pillow_heif.register_heif_opener()
        _heif_ready = True


def _label(format_id: str) -> str:
    return formats.get(format_id).label


# --- reading ------------------------------------------------------------------


def _render_svg(ctx: JobContext, src: Path, out: Path, kind: str) -> None:
    process.run(["rsvg-convert", "-f", kind, "-o", out, src], ctx, timeout_s=SVG_TIMEOUT_S)


def open_image(path: Path, format_id: str) -> Image.Image:
    """Open and decode an image; a short, clear ``JobError`` when that is impossible."""
    if format_id == "heic":
        _register_heif()
    try:
        im = Image.open(path)
        im.load()
    except Image.DecompressionBombError as exc:
        raise JobError(f"{path.name} is too large to convert safely", str(exc)) from exc
    except UnidentifiedImageError as exc:
        raise JobError(f"{path.name} is not a readable {_label(format_id)} image") from exc
    except (OSError, ValueError, SyntaxError) as exc:
        raise JobError(f"Cannot read {path.name}: {exc}", str(exc)) from exc
    return im


# --- pixels -------------------------------------------------------------------


def has_alpha(im: Image.Image) -> bool:
    return im.mode in ("RGBA", "LA", "PA", "RGBa", "La") or (
        im.mode in ("P", "L", "RGB") and "transparency" in im.info
    )


def _to_srgb(im: Image.Image, icc: bytes, mode: str) -> Image.Image:
    """Convert with the embedded profile to sRGB; plain conversion if the profile is bad."""
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        srgb = ImageCms.createProfile("sRGB")
        return ImageCms.profileToProfile(im, source, srgb, outputMode=mode)
    except (ImageCms.PyCMSError, OSError, ValueError) as exc:
        log.info("color profile not used (%s)", exc)
        return im.convert(mode)


def _deep_to_8bit(im: Image.Image) -> Image.Image:
    """Scale 16/32-bit grayscale to 8 bits (``convert("L")`` would clip to white)."""
    if im.mode == "F":
        return im.convert("L")
    wide = im if im.mode == "I" else im.convert("I")
    return wide.point(lambda v: v * (1 / 256)).convert("L")


@dataclass
class Prepared:
    image: Image.Image
    icc: bytes | None
    flattened: bool = False


def prepare(im: Image.Image, target: Target) -> Prepared:
    """Bring one frame into a mode the target can store."""
    icc = im.info.get("icc_profile") or None
    flattened = False

    if im.mode.startswith("I;16") or im.mode in ("I", "F"):
        if not (target.deep and im.mode.startswith("I;16")):
            im = _deep_to_8bit(im)
    elif im.mode == "CMYK":
        if not target.cmyk:
            im = _to_srgb(im, icc, "RGB") if icc else im.convert("RGB")
            icc = None
    elif im.mode == "1":
        im = im.convert("L")
    elif im.mode in ("P", "PA", "LA", "La", "RGBa"):
        im = im.convert("RGBA" if has_alpha(im) else "RGB")
    elif im.mode == "L" and "transparency" in im.info:
        im = im.convert("RGBA")
    elif im.mode not in ("L", "RGB", "RGBA"):
        im = im.convert("RGBA" if has_alpha(im) else "RGB")

    if im.mode == "RGBA":
        alpha = im.getchannel("A")
        if alpha.getextrema() == (255, 255):
            im = im.convert("RGB")  # fully opaque: nothing to keep or flatten
        elif not target.alpha:
            background = Image.new("RGB", im.size, WHITE)
            background.paste(im.convert("RGB"), mask=alpha)
            im, flattened = background, True
    if im.mode == "L" and not target.gray:
        im = im.convert("RGB")

    if icc and not target.icc:
        if im.mode in ("RGB", "RGBA"):
            im = _to_srgb(im, icc, im.mode)
        icc = None
    return Prepared(im, icc, flattened)


def _exif_bytes(im: Image.Image) -> bytes | None:
    exif = im.getexif()
    exif.pop(EXIF_ORIENTATION, None)
    return exif.tobytes() if len(exif) else None


def _frames_word(format_id: str) -> str:
    return {"tiff": "page", "heic": "image"}.get(format_id, "frame")


def _check_size(im: Image.Image, target_id: str, target: Target) -> None:
    if target.max_side and max(im.size) > target.max_side:
        w, h = im.size
        raise JobError(
            f"{_label(target_id)} images can be at most {target.max_side} pixels wide and "
            f"high; this one is {w} x {h}"
        )


# --- writing ------------------------------------------------------------------


def _save_options(source: Image.Image, prepared: Prepared, target: Target) -> dict[str, Any]:
    options = dict(target.options)
    if target.icc and prepared.icc:
        options["icc_profile"] = prepared.icc
    if target.exif and (exif := _exif_bytes(source)):
        options["exif"] = exif
    if target.dpi and (dpi := source.info.get("dpi")):
        options["dpi"] = dpi
    return options


def _save(im: Image.Image, out: Path, target_id: str, **options: Any) -> None:
    target = TARGETS[target_id]
    if target_id == "heic":
        _register_heif()
    try:
        im.save(out, format=target.pil_format, **options)
    except (OSError, ValueError, KeyError) as exc:
        raise JobError(f"Cannot write {_label(target_id)}: {exc}", str(exc)) from exc


def _save_animation(ctx: JobContext, im: Image.Image, out: Path, target_id: str) -> list[str]:
    target = TARGETS[target_id]
    notes: list[str] = []
    frames, durations = [], []
    for frame in ImageSequence.Iterator(im):
        ctx.check()
        durations.append(frame.info.get("duration", im.info.get("duration", 100)) or 100)
        prepared = prepare(frame.copy(), target)
        if prepared.flattened and FLATTENED_NOTE not in notes:
            notes.append(FLATTENED_NOTE)
        frames.append(prepared.image)
    _check_size(frames[0], target_id, target)
    options = dict(target.options)
    if target_id == "gif" and any(f.mode == "RGBA" for f in frames):
        options["disposal"] = 2  # clear each frame, or transparent areas keep old pixels
    _save(
        frames[0],
        out,
        target_id,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=im.info.get("loop", 0),
        **options,
    )
    return notes


def _pdf_dpi(im: Image.Image) -> float:
    dpi = im.info.get("dpi")
    try:
        value = float(dpi[0]) if dpi else 0.0
    except (TypeError, ValueError, IndexError):
        value = 0.0
    low, high = PDF_DPI_RANGE
    return value if low <= value <= high else DEFAULT_PDF_DPI


def _pdf_page_data(page: Image.Image, lossless: bool) -> bytes:
    prepared = prepare(page, Target("PNG", alpha=True, gray=True, cmyk=True))
    im = prepared.image
    buffer = io.BytesIO()
    if lossless or im.mode == "RGBA":
        if im.mode == "CMYK":
            im = _to_srgb(im, prepared.icc, "RGB") if prepared.icc else im.convert("RGB")
        im.save(buffer, format="PNG", icc_profile=prepared.icc)
    else:
        im.save(buffer, format="JPEG", quality=PDF_JPEG_QUALITY, icc_profile=prepared.icc)
    return buffer.getvalue()


def _write_pdf(ctx: JobContext, src: Path, im: Image.Image, route: Route, out: Path) -> list[str]:
    import pymupdf

    notes: list[str] = []
    multi_page = route.source == "tiff" and getattr(im, "n_frames", 1) > 1
    if getattr(im, "n_frames", 1) > 1 and not multi_page:
        notes.append(f"Only the first {_frames_word(route.source)} was kept")
    pages = list(ImageSequence.Iterator(im)) if multi_page else [im]
    doc = pymupdf.open()
    try:
        for page_image in pages:
            ctx.check()
            upright = ImageOps.exif_transpose(page_image.copy())
            orientation = page_image.getexif().get(EXIF_ORIENTATION, 1)
            if route.source == "jpg" and len(pages) == 1 and orientation == 1:
                data = src.read_bytes()  # embedded as it is: no second JPEG encoding
            else:
                data = _pdf_page_data(upright, route.source in LOSSLESS_SOURCES)
            scale = 72.0 / _pdf_dpi(page_image)
            page = doc.new_page(width=upright.width * scale, height=upright.height * scale)
            page.insert_image(page.rect, stream=data)
        doc.save(str(out), garbage=3, deflate=True)
    except (RuntimeError, ValueError) as exc:
        raise JobError(f"Cannot write PDF: {exc}", str(exc)) from exc
    finally:
        doc.close()
    return notes


# --- the backend entry point ------------------------------------------------------


def convert(ctx: JobContext, src: Path, out: Path, route: Route) -> None:
    """Convert one image file ``src`` into ``out`` (a temporary path) along ``route``."""
    if route.source == "svg":
        if route.target == "pdf":
            _render_svg(ctx, src, out, "pdf")  # stays vector
            return
        with tempfile.TemporaryDirectory(prefix="quince-svg-") as tmp:
            png = Path(tmp) / "render.png"
            _render_svg(ctx, src, png, "png")
            _convert_raster(ctx, png, out, route, read_as="png")
        return
    _convert_raster(ctx, src, out, route, read_as=route.source)


def _convert_raster(ctx: JobContext, src: Path, out: Path, route: Route, read_as: str) -> None:
    im = open_image(src, read_as)
    try:
        notes = _write(ctx, src, im, route, out)
    finally:
        im.close()
    for text in notes:  # only for a file that was converted
        ctx.note(text)


def _write(ctx: JobContext, src: Path, im: Image.Image, route: Route, out: Path) -> list[str]:
    ctx.check()
    if route.target == "pdf":
        return _write_pdf(ctx, src, im, route, out)
    target = TARGETS[route.target]
    frames = getattr(im, "n_frames", 1)
    if frames > 1 and getattr(im, "is_animated", False) and route.target in ANIMATION_TARGETS:
        return _save_animation(ctx, im, out, route.target)
    notes = []
    if frames > 1:
        notes.append(f"Only the first {_frames_word(route.source)} was kept")
        im.seek(0)
    upright = ImageOps.exif_transpose(im)
    prepared = prepare(upright, target)
    if prepared.flattened:
        notes.append(FLATTENED_NOTE)
    _check_size(prepared.image, route.target, target)
    ctx.check()
    _save(prepared.image, out, route.target, **_save_options(upright, prepared, target))
    return notes
