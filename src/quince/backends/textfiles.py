"""Reading plain-text inputs (TXT, CSV, TSV, Markdown, HTML) whatever their encoding.

Files from Turkish Windows programs are often Windows-1254, not UTF-8. Text is decoded as
UTF-8 (with or without a byte order mark) when it is valid UTF-8, else as Windows-1254, which
can decode any byte sequence's Turkish letters; Latin-1 is the last resort.
"""

from __future__ import annotations

import csv
from pathlib import Path

ENCODINGS = ("utf-8-sig", "cp1254", "latin-1")
DELIMITERS = (",", ";", "\t", "|")
SNIFF_CHARS = 64 * 1024


def decode(data: bytes) -> tuple[str, str]:
    """The text and the encoding it was read with."""
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise AssertionError("latin-1 decodes everything")


def read_text(path: Path) -> tuple[str, str]:
    return decode(Path(path).read_bytes())


def utf8_copy(src: Path, dest: Path) -> str:
    """Write ``src`` as UTF-8 (no BOM, ``\\n`` line ends) to ``dest``; returns the encoding read."""
    text, encoding = read_text(src)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    dest.write_text(text, encoding="utf-8")
    return encoding


def sniff_delimiter(text: str, default: str = ",") -> str:
    """The field separator of delimited text: comma, semicolon, tab or bar."""
    sample = text[:SNIFF_CHARS]
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(DELIMITERS)).delimiter
    except csv.Error:
        pass
    first = sample.split("\n", 1)[0]
    counts = {d: first.count(d) for d in DELIMITERS}
    best = max(counts, key=counts.get)
    return best if counts[best] else default


def encoding_note(encoding: str) -> str | None:
    """A remark for the result card when the input was not UTF-8."""
    if encoding == "cp1254":
        return "Read as Windows-1254 (Turkish) text"
    if encoding == "latin-1":
        return "Read as Latin-1 text"
    return None
