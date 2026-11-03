"""File type detection: file name extension plus content sniffing.

``sniff`` looks at the first bytes of a file and returns the format ids the content is
compatible with, best guess first. Containers that several formats share (ISO media,
Matroska, Ogg, ASF, ZIP, OLE2, plain text) yield all of them, so the extension decides
between them. ``resolve`` combines both:

- the extension's format wins when the content agrees with it or tells nothing;
- otherwise the content wins (a PNG named ``photo.jpg`` is a PNG);
- with neither, the type is unknown.
"""

from __future__ import annotations

import codecs
import re
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO

from quince.core import formats
from quince.core.formats import Format

SNIFF_BYTES = 4096

_TEXT = formats.text_format_ids()
_ISO_MEDIA = ("mp4", "m4a", "mov")
_HEIF_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1"}
_AVIF_BRANDS = {b"avif", b"avis"}
_ZIP_BASED = ("zip", "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub")
_OLE_BASED = ("doc", "xls", "ppt")

_ASF_HEADER = bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")
_ASF_VIDEO_MEDIA = bytes.fromhex("c0ef19bc4d5bcf11a8fd00805f5c442b")
_OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_OLE_END_OF_CHAIN = 0xFFFFFFFA  # this and higher sector numbers are special markers

_SRT_START = re.compile(r"\d+\r?\n\d\d:\d\d:\d\d,\d{3} --> ")
# Control characters that may appear in text files: \b \t \n \f \r and ESC.
_TEXT_CONTROLS = {8, 9, 10, 12, 13, 27}

_ODF_MIMES = {
    b"application/vnd.oasis.opendocument.text": "odt",
    b"application/vnd.oasis.opendocument.spreadsheet": "ods",
    b"application/vnd.oasis.opendocument.presentation": "odp",
    b"application/epub+zip": "epub",
}
_OOXML_PARTS = {
    "word/document.xml": "docx",
    "xl/workbook.xml": "xlsx",
    "ppt/presentation.xml": "pptx",
}
_OLE_STREAMS = {
    "WordDocument": "doc",
    "Workbook": "xls",
    "Book": "xls",
    "PowerPoint Document": "ppt",
}


def _prefer(first: str, group: Sequence[str]) -> tuple[str, ...]:
    return (first, *(f for f in group if f != first))


def _iso_media(head: bytes) -> tuple[str, ...]:
    size = int.from_bytes(head[:4], "big")
    major = head[8:12]
    brands = {major} | {head[i : i + 4] for i in range(16, min(size, len(head)) - 3, 4)}
    if brands & _AVIF_BRANDS:
        return ("avif",)
    if brands & _HEIF_BRANDS:
        return ("heic",)
    if major == b"qt  ":
        return _prefer("mov", _ISO_MEDIA)
    if major in (b"M4A ", b"M4B ", b"M4P "):
        return _prefer("m4a", _ISO_MEDIA)
    return _ISO_MEDIA


def _is_text(head: bytes) -> bool:
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return True
    return all(b >= 32 or b in _TEXT_CONTROLS for b in head)


def _sniff_text(head: bytes) -> tuple[str, ...]:
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = head.decode("utf-16", errors="ignore")
    else:
        text = codecs.getincrementaldecoder("utf-8")(errors="replace").decode(head)
    start = text.lstrip("\ufeff \t\r\n")
    lower = start[:1024].lower()
    if start.startswith("WEBVTT"):
        return _prefer("vtt", _TEXT)
    if _SRT_START.match(start):
        return _prefer("srt", _TEXT)
    if start.startswith("{\\rtf"):
        return _prefer("rtf", _TEXT)
    if lower.startswith(("<!doctype html", "<html")):
        return _prefer("html", _TEXT)
    if lower.startswith("<svg") or (lower.startswith("<?xml") and "<svg" in lower):
        return _prefer("svg", _TEXT)
    return _prefer("txt", _TEXT)


def sniff_bytes(head: bytes) -> tuple[str, ...]:
    """Format ids compatible with a file starting with ``head``, best guess first.

    ZIP and OLE2 files return every format built on them; ``sniff`` refines those by
    looking inside the file.
    """
    if not head:
        return ()
    if head[4:8] == b"ftyp":
        return _iso_media(head)
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return ("webm", "mkv") if b"webm" in head[:64] else ("mkv", "webm")
    if head.startswith(b"OggS"):
        return ("opus", "ogg") if b"OpusHead" in head else ("ogg", "opus")
    if head.startswith(_ASF_HEADER):
        return ("wmv", "wma") if _ASF_VIDEO_MEDIA in head else ("wma", "wmv")
    if head.startswith(b"RIFF"):
        riff = {b"WAVE": ("wav",), b"AVI ": ("avi",), b"WEBP": ("webp",)}
        return riff.get(head[8:12], ())
    if head.startswith(b"FORM") and head[8:12] in (b"AIFF", b"AIFC"):
        return ("aiff",)
    if head.startswith(b"fLaC"):
        return ("flac",)
    if head.startswith(b"ID3"):
        return ("mp3",)
    # Checked before the MPEG frame sync, which the UTF-16 LE BOM (FF FE) also matches.
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return _sniff_text(head)
    # MPEG audio frame sync; layer bits 00 would be AAC (ADTS), which is not MP3.
    if len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0 and head[1] & 0x06:
        return ("mp3",)
    if head.startswith(b"\xff\xd8\xff"):
        return ("jpg",)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ("png",)
    if head.startswith((b"GIF87a", b"GIF89a")):
        return ("gif",)
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return ("tiff",)
    if head.startswith(b"BM") and head[6:10] == b"\x00\x00\x00\x00":
        return ("bmp",)
    if b"%PDF-" in head[:1024]:
        return ("pdf",)
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return _ZIP_BASED
    if head.startswith(_OLE_MAGIC):
        return _OLE_BASED
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return ("7z",)
    if head.startswith(b"Rar!\x1a\x07"):
        return ("rar",)
    if head.startswith(b"\x1f\x8b"):
        return ("targz",)
    if head[257:262] == b"ustar":
        return ("tar",)
    if _is_text(head):
        return _sniff_text(head)
    return ()


def _zip_formats(path: Path) -> tuple[str, ...]:
    """Tell ODF, EPUB and OOXML files apart from plain ZIP archives."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "mimetype" in names:
                fmt = _ODF_MIMES.get(zf.read("mimetype").strip())
                if fmt:
                    return (fmt, "zip")
            for part, fmt in _OOXML_PARTS.items():
                if part in names:
                    return (fmt, "zip")
    except (OSError, zipfile.BadZipFile, KeyError):
        return _ZIP_BASED
    return ("zip",)


def _ole_stream_names(f: BinaryIO, max_sectors: int = 64) -> list[str]:
    """Names in the directory of an OLE2 compound file, following the FAT chain."""
    header = f.read(512)
    sector = 1 << int.from_bytes(header[0x1E:0x20], "little")
    if sector not in (512, 4096):
        return []

    def read_sector(n: int) -> bytes:
        f.seek((n + 1) * sector)
        return f.read(sector)

    fat_sectors = [
        int.from_bytes(header[0x4C + 4 * i : 0x50 + 4 * i], "little") for i in range(109)
    ]
    fat: list[int] = []
    for s in fat_sectors[: int.from_bytes(header[0x2C:0x30], "little")]:
        data = read_sector(s)
        fat += [int.from_bytes(data[i : i + 4], "little") for i in range(0, len(data), 4)]

    names: list[str] = []
    current = int.from_bytes(header[0x30:0x34], "little")
    for _ in range(max_sectors):
        if current >= _OLE_END_OF_CHAIN:
            break
        data = read_sector(current)
        for off in range(0, len(data) - 127, 128):
            length = int.from_bytes(data[off + 64 : off + 66], "little")
            if 2 <= length <= 64:
                names.append(data[off : off + length - 2].decode("utf-16-le", errors="replace"))
        current = fat[current] if current < len(fat) else _OLE_END_OF_CHAIN
    return names


def _ole_formats(path: Path) -> tuple[str, ...]:
    try:
        with open(path, "rb") as f:
            names = _ole_stream_names(f)
    except OSError:
        return _OLE_BASED
    for name in names:
        if name in _OLE_STREAMS:
            return _prefer(_OLE_STREAMS[name], _OLE_BASED)
    return _OLE_BASED


def sniff(path: Path) -> tuple[str, ...]:
    """Format ids compatible with the file's content, best guess first; () if unknown."""
    try:
        with open(path, "rb") as f:
            head = f.read(SNIFF_BYTES)
    except OSError:
        return ()
    found = sniff_bytes(head)
    if found == _ZIP_BASED:
        return _zip_formats(path)
    if found == _OLE_BASED:
        return _ole_formats(path)
    return found


def resolve(by_extension: Format | None, by_content: Sequence[str]) -> Format | None:
    """Combine the extension's format and the sniffed candidates (see module docstring)."""
    if by_extension is not None and (not by_content or by_extension.id in by_content):
        return by_extension
    if by_content:
        return formats.get(by_content[0])
    return None


def detect(path: Path) -> Format | None:
    """The format of the file at ``path``, or None if Quince does not know it."""
    path = Path(path)
    return resolve(formats.from_filename(path.name), sniff(path))
