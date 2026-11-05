"""Metadata: read it (for the Metadata window) and write a copy without it.

What counts as metadata: EXIF (camera, dates, GPS position), XMP, IPTC, comments and text
chunks in images; tags and chapters in audio and video; the document information and XMP in
PDFs. The pixels, the sound and the pages are not touched:

- JPEG, PNG, WebP: the file is copied with those blocks left out, without re-encoding. The
  EXIF orientation of a JPEG is kept (alone), or the photo would turn on its side.
- TIFF and GIF are saved again without it (lossless). HEIC and AVIF are re-encoded at a high
  quality, since their metadata cannot be cut out (noted on the card).
- Audio and video: ffmpeg copies every stream without global, stream and chapter metadata.
- PDF: PyMuPDF clears the document information and the XMP packet.

The color profile (ICC) is kept everywhere: it describes the colors, not the photo.
"""

from __future__ import annotations

import contextlib
import io
import logging
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from PIL import ExifTags, Image

from quince.backends import ffmpeg, imaging
from quince.backends.files import human_size
from quince.backends.media import MUXERS
from quince.core import formats
from quince.core.formats import Category
from quince.core.jobs import JobContext, JobError

log = logging.getLogger(__name__)

IMAGE_FORMATS = frozenset({"jpg", "png", "webp", "tiff", "gif", "heic", "avif"})
MAX_VALUE_CHARS = 2000
REENCODED_NOTE = "Re-encoded at high quality: {label} metadata cannot be cut out"


@dataclass
class Metadata:
    """``info``: what the file is (not metadata); ``rows``: the metadata found."""

    info: list[tuple[str, str]]
    rows: list[tuple[str, str]]


def _text(value) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8").rstrip("\x00")
        except UnicodeDecodeError:
            return f"({len(value)} bytes)"
    text = str(value).strip()
    return text if len(text) <= MAX_VALUE_CHARS else text[: MAX_VALUE_CHARS - 1] + "…"


# --- images: reading -------------------------------------------------------------------------


def _gps_coordinate(values, ref) -> float | None:
    try:
        d, m, s = (float(Fraction(v)) if not isinstance(v, float) else v for v in values)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    value = d + m / 60 + s / 3600
    return -value if ref in ("S", "W") else value


def gps_position(gps: dict) -> str | None:
    lat = _gps_coordinate(gps.get(2, ()), gps.get(1))
    lon = _gps_coordinate(gps.get(4, ()), gps.get(3))
    if lat is None or lon is None:
        return None
    return f"{lat:.6f}, {lon:.6f}"


def _exif_rows(im: Image.Image) -> list[tuple[str, str]]:
    exif = im.getexif()
    rows = []
    for tag, value in exif.items():
        if tag in (ExifTags.Base.ExifOffset, ExifTags.Base.GPSInfo):
            continue
        rows.append((ExifTags.TAGS.get(tag, f"Tag 0x{tag:04x}"), _text(value)))
    for tag, value in exif.get_ifd(ExifTags.IFD.Exif).items():
        if tag == ExifTags.Base.MakerNote:
            rows.append(("MakerNote", f"({len(value)} bytes)"))
            continue
        rows.append((ExifTags.TAGS.get(tag, f"Tag 0x{tag:04x}"), _text(value)))
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    if gps:
        position = gps_position(gps)
        rows.append(("GPS position", position or "present"))
        for tag, value in gps.items():
            rows.append((f"GPS {ExifTags.GPSTAGS.get(tag, tag)}", _text(value)))
    return rows


def _image_rows(im: Image.Image) -> list[tuple[str, str]]:
    rows = _exif_rows(im)
    info = im.info
    if xmp := info.get("xmp") or info.get("XML:com.adobe.xmp"):
        rows.append(("XMP", _text(xmp)))
    if comment := info.get("comment"):
        rows.append(("Comment", _text(comment)))
    for key, value in getattr(im, "text", {}).items():
        if key != "XML:com.adobe.xmp":
            rows.append((key, _text(value)))
    if "photoshop" in info or "iptc" in info:
        rows.append(("IPTC", "present"))
    return rows


def read_image(path: Path, format_id: str) -> Metadata:
    im = imaging.open_image(path, format_id)
    try:
        info = [
            ("Format", formats.get(format_id).name),
            ("Size", f"{im.width} × {im.height} px"),
            ("File size", human_size(path.stat().st_size)),
        ]
        if im.info.get("icc_profile"):
            info.append(("Color profile", "embedded (kept)"))
        return Metadata(info, _image_rows(im))
    finally:
        im.close()


# --- images: stripping -----------------------------------------------------------------------

_JPEG_KEEP_APP = {0xE0, 0xE2, 0xEE}  # JFIF, ICC profile, Adobe (color transform)
_PNG_DROP = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}
_WEBP_DROP = {b"EXIF", b"XMP "}


def _orientation(data: bytes) -> int:
    try:
        with Image.open(io.BytesIO(data)) as im:
            return int(im.getexif().get(imaging.EXIF_ORIENTATION, 1))
    except (OSError, ValueError):
        return 1


def _exif_with_orientation(orientation: int) -> bytes:
    exif = Image.Exif()
    exif[imaging.EXIF_ORIENTATION] = orientation
    data = exif.tobytes()
    return data if data.startswith(b"Exif\x00\x00") else b"Exif\x00\x00" + data


def _keep_jpeg_segment(marker: int, segment: bytes) -> bool:
    if marker == 0xFE:  # comment
        return False
    if not 0xE0 <= marker <= 0xEF:
        return True  # tables, frame headers: the image itself
    if marker == 0xE2:
        return segment[4:16] == b"ICC_PROFILE\x00"  # not MPF (pointers to extra images)
    return marker in _JPEG_KEEP_APP


def strip_jpeg(data: bytes) -> bytes:
    """The JPEG without APP1 (EXIF, XMP), APP13 (IPTC), comments, other APPn blocks and the
    extra images some cameras append after the end of the image (they carry EXIF too)."""
    if data[:2] != b"\xff\xd8":
        raise JobError("Not a readable JPEG image")
    orientation = _orientation(data)
    exif = b""
    if orientation != 1:
        payload = _exif_with_orientation(orientation)
        exif = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    out = bytearray(b"\xff\xd8")
    pos = 2
    while pos < len(data):
        if data[pos] != 0xFF:
            raise JobError("The JPEG structure is damaged")
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if exif and marker != 0xE0:  # after JFIF, which must come first
            out += exif
            exif = b""
        if marker == 0xD9:
            return bytes(out + b"\xff\xd9")
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out += data[pos : pos + 2]
            pos += 2
            continue
        (length,) = struct.unpack(">H", data[pos + 2 : pos + 4])
        segment = data[pos : pos + 2 + length]
        if len(segment) < 2 + length:
            raise JobError("The JPEG structure is damaged")
        if _keep_jpeg_segment(marker, segment):
            out += segment
        pos += 2 + length
        if marker == 0xDA:  # start of scan: entropy-coded data up to the next marker
            scan_end = _scan_end(data, pos)
            out += data[pos:scan_end]
            pos = scan_end
    raise JobError("The JPEG has no end")


def _scan_end(data: bytes, pos: int) -> int:
    """Where the entropy-coded data after a SOS ends: the next marker that is not a stuffed
    0xFF00 or a restart marker."""
    while True:
        pos = data.find(b"\xff", pos)
        if pos < 0 or pos + 1 >= len(data):
            raise JobError("The JPEG has no end")
        following = data[pos + 1]
        if following == 0x00 or 0xD0 <= following <= 0xD7 or following == 0xFF:
            pos += 1 if following == 0xFF else 2
            continue
        return pos


def strip_png(data: bytes) -> bytes:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise JobError("Not a readable PNG image")
    out = bytearray(data[:8])
    pos = 8
    while pos + 12 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        chunk = data[pos : pos + 12 + length]
        if len(chunk) < 12 + length:
            raise JobError("The PNG structure is damaged")
        if kind not in _PNG_DROP:
            out += chunk
        pos += 12 + length
        if kind == b"IEND":
            return bytes(out)
    raise JobError("The PNG structure is damaged")


def strip_webp(data: bytes) -> bytes:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise JobError("Not a readable WebP image")
    chunks = []
    pos = 12
    while pos + 8 <= len(data):
        kind = data[pos : pos + 4]
        (length,) = struct.unpack("<I", data[pos + 4 : pos + 8])
        padded = length + (length & 1)
        chunk = bytearray(data[pos : pos + 8 + padded])
        if len(chunk) < 8 + length:
            raise JobError("The WebP structure is damaged")
        if kind == b"VP8X" and len(chunk) >= 9:
            chunk[8] &= ~0x0C & 0xFF  # clear the EXIF (0x08) and XMP (0x04) flags
        if kind not in _WEBP_DROP:
            chunks.append(bytes(chunk))
        pos += 8 + padded
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _resave(ctx: JobContext, src: Path, format_id: str, out: Path) -> None:
    im = imaging.open_image(src, format_id)
    try:
        icc = im.info.get("icc_profile")
        options: dict = {"icc_profile": icc} if icc else {}
        target = imaging.TARGETS[format_id]
        frames = getattr(im, "n_frames", 1)
        if format_id == "gif":
            options.update(save_all=frames > 1, loop=im.info.get("loop", 0))
            if "duration" in im.info:
                options["duration"] = im.info["duration"]
            if (transparency := im.info.get("transparency")) is not None:
                options["transparency"] = transparency
            options.pop("icc_profile", None)
        elif format_id == "tiff":
            options.update(save_all=frames > 1, compression=im.info.get("compression") or "raw")
            if dpi := im.info.get("dpi"):
                options["dpi"] = dpi
        else:  # heic, avif
            if frames > 1:
                raise JobError("Images with several frames are not supported yet")
            options.update(target.options)
            options.update(quality=95, exif=b"", xmp=b"")
            ctx.note(REENCODED_NOTE.format(label=formats.get(format_id).label))
        im.info.pop("exif", None)
        im.info.pop("xmp", None)
        im.info.pop("comment", None)
        imaging._save(im, out, format_id, **options)
    finally:
        im.close()


def strip_image(ctx: JobContext, src: Path, format_id: str, out: Path) -> None:
    if format_id in ("jpg", "png", "webp"):
        strip = {"jpg": strip_jpeg, "png": strip_png, "webp": strip_webp}[format_id]
        out.write_bytes(strip(src.read_bytes()))
    else:
        _resave(ctx, src, format_id, out)


# --- audio and video ---------------------------------------------------------------------------


def _tag_rows(tags: dict, prefix: str = "") -> list[tuple[str, str]]:
    return [(f"{prefix}{key}", _text(value)) for key, value in sorted((tags or {}).items())]


def read_media(path: Path) -> Metadata:
    data = ffmpeg.probe_json(path)
    fmt = data.get("format", {})
    info = [("Container", str(fmt.get("format_long_name") or fmt.get("format_name", "?")))]
    with contextlib.suppress(KeyError, TypeError, ValueError):
        info.append(("Duration", f"{float(fmt['duration']):.1f} s"))
    info.append(("File size", human_size(path.stat().st_size)))
    rows = _tag_rows(fmt.get("tags", {}))
    for stream in data.get("streams", []):
        kind = stream.get("codec_type", "stream")
        label = f"{kind.capitalize()} {stream.get('index', '')}".strip()
        info.append((label, str(stream.get("codec_long_name") or stream.get("codec_name", "?"))))
        tags = {k: v for k, v in (stream.get("tags") or {}).items()}
        rows += _tag_rows(tags, f"{label}: ")
    if chapters := data.get("chapters"):
        rows.append(("Chapters", str(len(chapters))))
    return Metadata(info, rows)


def strip_media(ctx: JobContext, src: Path, format_id: str, out: Path) -> None:
    info = ffmpeg.probe(src, ctx)
    args: list = [
        "-i", src, "-map", "0", "-map", "-0:d", "-c", "copy",
        "-map_metadata", "-1", "-map_metadata:s", "-1", "-map_chapters", "-1",
        "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
    ]  # fmt: skip
    if format_id in ("mp4", "mov", "m4a"):
        args += ["-movflags", "+faststart"]
    ffmpeg.run_ffmpeg([*args, "-f", MUXERS[format_id], out], ctx, duration_s=info.duration)


# --- PDF ---------------------------------------------------------------------------------------

PDF_KEYS = (
    ("title", "Title"), ("author", "Author"), ("subject", "Subject"), ("keywords", "Keywords"),
    ("creator", "Creator"), ("producer", "Producer"), ("creationDate", "Created"),
    ("modDate", "Modified"), ("trapped", "Trapped"),
)  # fmt: skip


def _open_pdf(path: Path):
    from quince.backends import pdf

    doc = pdf.open_pdf(path)
    if doc.needs_pass:
        doc.close()
        raise JobError("The PDF is password-protected")
    return doc


def read_pdf(path: Path) -> Metadata:
    doc = _open_pdf(path)
    try:
        meta = doc.metadata or {}
        info = [
            ("Format", str(meta.get("format") or "PDF")),
            ("Pages", str(doc.page_count)),
            ("File size", human_size(path.stat().st_size)),
        ]
        rows = [(label, _text(meta[key])) for key, label in PDF_KEYS if meta.get(key)]
        if xmp := doc.get_xml_metadata():
            rows.append(("XMP", _text(xmp)))
        return Metadata(info, rows)
    finally:
        doc.close()


def strip_pdf(ctx: JobContext, src: Path, out: Path) -> None:
    doc = _open_pdf(src)
    try:
        ctx.check()
        doc.set_metadata({})
        doc.del_xml_metadata()
        doc.save(str(out), garbage=3, deflate=True)
    except (RuntimeError, ValueError) as exc:
        raise JobError(f"Cannot write the PDF: {exc}", str(exc)) from exc
    finally:
        doc.close()


# --- entry points ------------------------------------------------------------------------------

MEDIA_FORMATS = frozenset(
    f.id for f in formats.all_formats() if f.category in (Category.AUDIO, Category.VIDEO)
)
SUPPORTED = IMAGE_FORMATS | MEDIA_FORMATS | {"pdf"}


def read(path: Path, format_id: str) -> Metadata:
    if format_id in IMAGE_FORMATS:
        return read_image(path, format_id)
    if format_id in MEDIA_FORMATS:
        return read_media(path)
    if format_id == "pdf":
        return read_pdf(path)
    raise JobError(f"Quince cannot read the metadata of {formats.get(format_id).label} files")


def strip(ctx: JobContext, src: Path, format_id: str, out: Path) -> None:
    if format_id in IMAGE_FORMATS:
        strip_image(ctx, src, format_id, out)
    elif format_id in MEDIA_FORMATS:
        strip_media(ctx, src, format_id, out)
    elif format_id == "pdf":
        strip_pdf(ctx, src, out)
    else:
        raise JobError(f"Quince cannot remove metadata from {formats.get(format_id).label} files")


def describe(rows: Iterable[tuple[str, str]]) -> str:
    return "\n".join(f"{name}: {value}" for name, value in rows)
