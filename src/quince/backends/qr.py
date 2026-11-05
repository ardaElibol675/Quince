"""Reading QR codes and barcodes with ``zbarimg`` (package zbar-tools).

The image is decoded by Pillow (so HEIC, AVIF and WebP work too), flattened onto white and
handed to zbarimg as a grayscale PNG. zbarimg's XML output keeps every symbol's type and
exact text, also when it spans several lines. Exit status 4 means "nothing found".
"""

from __future__ import annotations

import base64
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from quince.backends import imaging, process
from quince.core.jobs import JobContext, JobError

TIMEOUT_S = 60
NOTHING_FOUND = 4
_NS = "{http://zbar.sourceforge.net/2008/barcode}"
TYPE_NAMES = {"QR-Code": "QR code", "EAN-13": "EAN-13 barcode", "EAN-8": "EAN-8 barcode",
              "UPC-A": "UPC-A barcode", "UPC-E": "UPC-E barcode", "CODE-128": "Code 128 barcode",
              "CODE-39": "Code 39 barcode", "I2/5": "Interleaved 2 of 5 barcode",
              "PDF417": "PDF417 code", "DataBar": "DataBar barcode"}  # fmt: skip


@dataclass(frozen=True)
class Symbol:
    kind: str  # "QR code", "EAN-13 barcode", ...
    text: str


def parse_xml(text: str) -> list[Symbol]:
    """zbarimg ``--xml`` output → the symbols, in the order found."""
    if not text.strip():
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise JobError("zbarimg gave an unreadable answer", str(exc)) from exc
    symbols = []
    for symbol in root.iter(f"{_NS}symbol"):
        data = symbol.find(f"{_NS}data")
        if data is None:
            continue
        value = data.text or ""
        if data.get("format") == "base64":
            raw = base64.b64decode(value)
            try:
                value = raw.decode("utf-8")
            except UnicodeDecodeError:
                value = raw.decode("latin-1")
        kind = symbol.get("type", "code")
        symbols.append(Symbol(TYPE_NAMES.get(kind, kind), value))
    return symbols


def _grayscale_png(path: Path, format_id: str, dest: Path) -> None:
    im = imaging.open_image(path, format_id)
    try:
        upright = ImageOps.exif_transpose(im)
        if imaging.has_alpha(upright):
            background = Image.new("RGBA", upright.size, "white")
            upright = Image.alpha_composite(background, upright.convert("RGBA"))
        upright.convert("L").save(dest, "PNG")
    finally:
        im.close()


def read_codes(path: Path, format_id: str, ctx: JobContext | None = None) -> list[Symbol]:
    with tempfile.TemporaryDirectory(prefix="quince-qr-") as tmp:
        png = Path(tmp) / "image.png"
        _grayscale_png(path, format_id, png)
        lines: list[str] = []
        result = process.run(
            ["zbarimg", "--quiet", "--xml", png],
            ctx,
            timeout_s=TIMEOUT_S,
            on_stdout=lines.append,
            check=False,
        )
    if result.returncode not in (0, NOTHING_FOUND):
        raise JobError(
            process.failure_reason("zbarimg", result.stderr_tail, result.returncode),
            "\n".join(result.stderr_tail),
        )
    return parse_xml("\n".join(lines))
