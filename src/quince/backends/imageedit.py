"""The pixel work of the image editors (crop, edit, frame, redact) and saving their results.

An editor describes its result as data (a crop box, an ``Adjust``, a ``FrameStyle``, a list of
``Redaction``); the editor applies the same function to its small preview and the job applies
it to the full image, so the saved file looks like the preview. Sizes that must look the same
at every scale (padding, corner radius, blur strength) are fractions of the image, not pixels.

Coordinates are pixels of the upright original (EXIF orientation applied).

Saving (``save_edited``):

- The result keeps the source's format; SVG becomes PNG. A result with transparency whose
  format cannot store it (a JPG with rounded corners on a transparent background) becomes a
  PNG, and a note says so.
- Animated GIF and WebP: every frame is edited. Other multi-frame files keep the first frame.
- EXIF (without the orientation tag) and the color profile are kept. Pillow never writes the
  EXIF thumbnail, which would still show the unedited picture. ``keep_exif=False`` (Redact)
  removes EXIF entirely: camera maker notes may hold a preview of the original.
- Pixels are edited as 8-bit RGB or RGBA; 16-bit and CMYK images are converted first.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageSequence

from quince.backends import imaging, process
from quince.core.jobs import JobContext, JobError

log = logging.getLogger(__name__)

EDITABLE = frozenset({"jpg", "png", "webp", "heic", "tiff", "avif", "bmp", "gif", "svg"})
RASTER = EDITABLE - {"svg"}
PREVIEW_SIDE = 1600  # longer side of the preview an editor works on
SVG_TIMEOUT_S = 120
# Working mode for edits: 8-bit RGB / RGBA, the color profile kept aside.
_WORK = imaging.Target("PNG", alpha=True)
Transform = Callable[[Image.Image], Image.Image]
RGBA = tuple[int, int, int, int]
SAVED_AS_PNG_NOTE = "Saved as PNG to keep the transparency"
FIRST_FRAME_NOTE = "Only the first frame was kept"


def output_format(source: str, *, alpha: bool = False) -> str:
    """The format an edited ``source`` is saved in; ``alpha``: the result has transparency."""
    if source == "svg":
        return "png"
    if alpha and not imaging.TARGETS[source].alpha:
        return "png"
    return source


# --- opening --------------------------------------------------------------------------------


@contextmanager
def opened(path: Path, format_id: str, ctx: JobContext | None = None) -> Iterator[Image.Image]:
    """The image file opened with every frame (an SVG rendered to pixels first)."""
    if format_id == "svg":
        with tempfile.TemporaryDirectory(prefix="quince-svg-") as tmp:
            png = Path(tmp) / "render.png"
            process.run(
                ["rsvg-convert", "-f", "png", "-o", png, path], ctx, timeout_s=SVG_TIMEOUT_S
            )
            im = imaging.open_image(png, "png")
            try:
                yield im
            finally:
                im.close()
        return
    im = imaging.open_image(path, format_id)
    try:
        yield im
    finally:
        im.close()


def working(frame: Image.Image) -> tuple[Image.Image, bytes | None]:
    """``frame`` as 8-bit RGB or RGBA, and its color profile (None when converted to sRGB)."""
    prepared = imaging.prepare(frame, _WORK)
    return prepared.image, prepared.icc


@dataclass(frozen=True)
class Loaded:
    """What an editor works on: a preview and the size of the full upright image."""

    path: Path
    format_id: str
    preview: Image.Image  # RGB or RGBA, longer side at most ``PREVIEW_SIDE``
    size: tuple[int, int]
    frames: int = 1

    @property
    def scale(self) -> float:
        """Preview pixels per original pixel (at most 1)."""
        return self.preview.width / self.size[0]

    @property
    def animated(self) -> bool:
        return self.frames > 1


def load(path: Path, format_id: str, max_side: int = PREVIEW_SIDE) -> Loaded:
    """Open ``path`` for an editor: the first frame, upright, scaled down for the preview."""
    with opened(path, format_id) as im:
        frames = getattr(im, "n_frames", 1) if getattr(im, "is_animated", False) else 1
        im.seek(0)
        upright = ImageOps.exif_transpose(im)
        size = upright.size
        preview, _icc = working(upright)
        if max(size) > max_side:
            preview.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return Loaded(Path(path), format_id, preview, size, frames)


# --- crop -----------------------------------------------------------------------------------

Box = tuple[float, float, float, float]  # x0, y0, x1, y1


def pixel_box(box: Box, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """``box`` rounded to whole pixels inside an image of ``size``, at least 1 x 1."""
    w, h = size
    x0 = min(max(0, round(box[0])), w - 1)
    y0 = min(max(0, round(box[1])), h - 1)
    x1 = min(max(x0 + 1, round(box[2])), w)
    y1 = min(max(y0 + 1, round(box[3])), h)
    return x0, y0, x1, y1


def crop(im: Image.Image, box: Box) -> Image.Image:
    return im.crop(pixel_box(box, im.size))


# --- edit (orientation, size, colors) -----------------------------------------------------

_ROTATIONS = {
    1: Image.Transpose.ROTATE_270,  # Pillow's ROTATE_* turn counter-clockwise
    2: Image.Transpose.ROTATE_180,
    3: Image.Transpose.ROTATE_90,
}
ADJUST_RANGE = (-100, 100)


@dataclass(frozen=True)
class Adjust:
    """Turns, mirrors, a new size and color changes, applied in that order.

    ``rotate`` counts quarter turns clockwise; the mirrors apply to the turned picture.
    ``size`` is the final size (None keeps it). Color values run from -100 to 100; 0 changes
    nothing, -100 brightness is black, -100 saturation is grayscale.
    """

    rotate: int = 0
    flip_h: bool = False
    flip_v: bool = False
    size: tuple[int, int] | None = None
    brightness: int = 0
    contrast: int = 0
    saturation: int = 0

    def turned_size(self, size: tuple[int, int]) -> tuple[int, int]:
        """``size`` after the turns (before resizing)."""
        w, h = size
        return (h, w) if self.rotate % 2 else (w, h)

    def rotated(self, quarter_turns: int) -> Adjust:
        """Turned further by ``quarter_turns`` clockwise (negative: counter-clockwise).

        Turning a mirrored picture equals mirroring the turned one along the other axis, so
        the mirrors swap on every odd turn. A new size swaps its sides too.
        """
        odd = quarter_turns % 2 == 1
        size = self.size
        if odd and size is not None:
            size = (size[1], size[0])
        return replace(
            self,
            rotate=(self.rotate + quarter_turns) % 4,
            flip_h=self.flip_v if odd else self.flip_h,
            flip_v=self.flip_h if odd else self.flip_v,
            size=size,
        )

    @property
    def colors_changed(self) -> bool:
        return bool(self.brightness or self.contrast or self.saturation)


def _factor(value: int) -> float:
    low, high = ADJUST_RANGE
    return 1.0 + min(high, max(low, value)) / 100.0


def adjust(im: Image.Image, a: Adjust, *, resize: bool = True) -> Image.Image:
    """Apply ``a`` to an RGB / RGBA image; ``resize=False`` skips the size (previews)."""
    if a.rotate % 4:
        im = im.transpose(_ROTATIONS[a.rotate % 4])
    if a.flip_h:
        im = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if a.flip_v:
        im = im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if resize and a.size is not None and tuple(a.size) != im.size:
        w, h = (max(1, int(v)) for v in a.size)
        im = im.resize((w, h), Image.Resampling.LANCZOS)
    if a.colors_changed:
        alpha = im.getchannel("A") if im.mode == "RGBA" else None
        rgb = im.convert("RGB")  # enhancing RGBA would change the transparency too
        if a.brightness:
            rgb = ImageEnhance.Brightness(rgb).enhance(_factor(a.brightness))
        if a.contrast:
            rgb = ImageEnhance.Contrast(rgb).enhance(_factor(a.contrast))
        if a.saturation:
            rgb = ImageEnhance.Color(rgb).enhance(_factor(a.saturation))
        if alpha is not None:
            rgb.putalpha(alpha)
        im = rgb
    return im


# --- frame ----------------------------------------------------------------------------------

SHADOW_BLUR = 0.03  # of the shorter side
SHADOW_OFFSET = 0.012
SHADOW_MAX_OPACITY = 0.6
_SUPERSAMPLE = 4


@dataclass(frozen=True)
class FrameStyle:
    """A border around the picture. ``padding`` and ``radius`` are fractions of the picture's
    shorter side; ``shadow`` (0..1) is the strength of a soft shadow below it."""

    padding: float = 0.06
    background: RGBA = (255, 255, 255, 255)
    radius: float = 0.0
    shadow: float = 0.0

    @property
    def transparent(self) -> bool:
        return self.background[3] < 255

    def pixels(self, size: tuple[int, int]) -> tuple[int, int]:
        """Padding and corner radius in pixels for a picture of ``size``."""
        short = min(size)
        return round(self.padding * short), min(round(self.radius * short), short // 2)


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    """An "L" mask of ``size``: white with smooth (supersampled) rounded corners."""
    w, h = size
    mask = Image.new("L", size, 255)
    radius = min(radius, w // 2, h // 2)
    if radius <= 0:
        return mask
    big = radius * _SUPERSAMPLE
    quarter = Image.new("L", (big, big), 0)
    ImageDraw.Draw(quarter).ellipse((0, 0, 2 * big - 1, 2 * big - 1), fill=255)
    corner = quarter.resize((radius, radius), Image.Resampling.LANCZOS)
    mask.paste(corner, (0, 0))
    mask.paste(corner.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (w - radius, 0))
    mask.paste(corner.transpose(Image.Transpose.FLIP_TOP_BOTTOM), (0, h - radius))
    mask.paste(corner.transpose(Image.Transpose.ROTATE_180), (w - radius, h - radius))
    return mask


def frame(im: Image.Image, style: FrameStyle) -> Image.Image:
    """``im`` on a background with padding, rounded corners and a shadow."""
    w, h = im.size
    short = min(w, h)
    pad, radius = style.pixels(im.size)
    canvas = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), style.background)
    mask = rounded_mask(im.size, radius)
    if im.mode == "RGBA":
        mask = ImageChops.darker(mask, im.getchannel("A"))  # keep its own transparency
    if style.shadow > 0:
        blur = max(1.0, SHADOW_BLUR * short)
        offset = round(SHADOW_OFFSET * short)
        opacity = SHADOW_MAX_OPACITY * min(1.0, style.shadow)
        shade = Image.new("L", canvas.size, 0)
        shade.paste(mask.point(lambda v: round(v * opacity)), (pad, pad + offset))
        shade = shade.filter(ImageFilter.GaussianBlur(blur))
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        layer.putalpha(shade)
        canvas = Image.alpha_composite(canvas, layer)
    picture = im.convert("RGBA")
    picture.putalpha(mask)
    canvas.alpha_composite(picture, (pad, pad))
    if canvas.getchannel("A").getextrema() == (255, 255):
        return canvas.convert("RGB")
    return canvas


# --- redact ---------------------------------------------------------------------------------

REDACT_STYLES = ("solid", "blur", "pixelate")
REDACT_COLOR = (0, 0, 0)
PIXELATE_BLOCKS = 4  # blocks across the shorter side of an area
BLUR_STRENGTH = 0.25  # blur radius, of the shorter side of an area


@dataclass(frozen=True)
class Redaction:
    """An area to hide: a black box, a heavy blur or big pixels."""

    box: Box
    style: str = "solid"

    def __post_init__(self) -> None:
        if self.style not in REDACT_STYLES:
            raise ValueError(f"unknown redaction style {self.style!r}")

    def scaled(self, factor: float) -> Redaction:
        return replace(self, box=tuple(v * factor for v in self.box))


def redact(im: Image.Image, items: Sequence[Redaction]) -> Image.Image:
    """Hide every area of ``items`` (later ones on top); only pixels inside an area are used,
    so nothing from outside leaks in and nothing inside leaks out."""
    im = im.copy()
    for item in items:
        x0, y0, x1, y1 = pixel_box(item.box, im.size)
        region = im.crop((x0, y0, x1, y1))
        alpha = region.getchannel("A") if region.mode == "RGBA" else None
        rgb = region.convert("RGB")
        short = min(rgb.size)
        if item.style == "solid":
            rgb = Image.new("RGB", rgb.size, REDACT_COLOR)
        elif item.style == "blur":
            radius = max(2.0, BLUR_STRENGTH * short)
            for _ in range(2):  # twice: one pass still lets large shapes through
                rgb = rgb.filter(ImageFilter.GaussianBlur(radius))
        else:
            block = max(1.0, short / PIXELATE_BLOCKS)
            small = (max(1, round(rgb.width / block)), max(1, round(rgb.height / block)))
            rgb = rgb.resize(small, Image.Resampling.BOX).resize(rgb.size, Image.Resampling.NEAREST)
        if alpha is not None:
            rgb.putalpha(alpha)
        im.paste(rgb.convert(im.mode), (x0, y0))
    return im


# --- saving ---------------------------------------------------------------------------------


def save_edited(
    ctx: JobContext,
    src: Path,
    source_format: str,
    out: Path,
    transform: Transform,
    *,
    target_format: str | None = None,
    keep_exif: bool = True,
) -> None:
    """Apply ``transform`` to ``src`` (every frame of an animation) and write ``out``."""
    target_id = target_format or output_format(source_format)
    if target_id not in imaging.TARGETS:
        raise JobError(f"Cannot save edited images as {target_id.upper()}")
    if target_id != output_format(source_format) and target_id == "png":
        ctx.note(SAVED_AS_PNG_NOTE)
    with opened(src, source_format, ctx) as im:
        ctx.check()
        dpi = im.info.get("dpi")
        frames = getattr(im, "n_frames", 1)
        if (
            frames > 1
            and getattr(im, "is_animated", False)
            and target_id in imaging.ANIMATION_TARGETS
        ):
            _save_animation(ctx, im, out, target_id, transform)
            return
        if frames > 1:
            ctx.note(FIRST_FRAME_NOTE)
            im.seek(0)
        upright = ImageOps.exif_transpose(im)
        # Only after turning: this drops the orientation tag from the image's EXIF.
        exif = imaging._exif_bytes(im) if keep_exif else None
        picture, icc = working(upright)
        ctx.check()
        result = transform(picture)
        ctx.check()
    target = imaging.TARGETS[target_id]
    prepared = imaging.prepare(result, target)
    if prepared.flattened:
        ctx.note(imaging.FLATTENED_NOTE)
    imaging._check_size(prepared.image, target_id, target)
    options = dict(target.options)
    if target.icc and icc:
        options["icc_profile"] = icc
    if target.exif and exif:
        options["exif"] = exif
    if target.dpi and dpi:
        options["dpi"] = dpi
    imaging._save(prepared.image, out, target_id, **options)


def _save_animation(
    ctx: JobContext, im: Image.Image, out: Path, target_id: str, transform: Transform
) -> None:
    frames, durations = [], []
    for frame in ImageSequence.Iterator(im):
        ctx.check()
        durations.append(imaging.frame_duration(frame, im))
        picture, _icc = working(frame.copy())
        frames.append(transform(picture))
    for note in imaging.save_frames(ctx, frames, durations, im.info.get("loop", 0), out, target_id):
        ctx.note(note)
