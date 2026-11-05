"""Text out of plain text, source code, sheets and slides (the ``text`` backend).

Everything is first written as Markdown by Quince itself (``quince.backends.markdown``);
HTML, EPUB and PDF are made from that Markdown with pandoc.

- **Plain text** → paragraphs; every line break is kept and nothing is read as markup.
- **Source code** → one fenced code block tagged with its language (``Format.syntax``);
  HTML and PDF are highlighted by pandoc, with the file name as title. → TXT is the code as
  UTF-8 text.
- **Sheets** → one Markdown table per sheet (first row as header), under a ``## name``
  heading when there are several. CSV/TSV are read in Python (encoding and separator
  detected); workbooks go through LibreOffice's CSV export, values as shown. Sheet order
  comes from the workbook (LibreOffice writes all sheets at once).
- **Slides** → ``## title`` per slide, its points as nested lists, its tables and its
  speaker notes (``quince.backends.slidetext``); → TXT is the same as plain text. PPT is
  converted to PPTX by LibreOffice first.
"""

from __future__ import annotations

import csv
import io
import shutil
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path
from xml.etree import ElementTree as ET

from quince.backends import markdown, office, slidetext, textfiles
from quince.core import formats
from quince.core.formats import Category
from quince.core.jobs import JobContext, JobError
from quince.core.routes import Route

SML = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
TABLE_NAME = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}name"
TABLE_TAG = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table"
DELIMITED = ("csv", "tsv")
WORKBOOKS = ("xlsx", "xls", "ods")

Sheet = tuple[str, list[list[str]]]


def read_text(ctx: JobContext, src: Path) -> str:
    """The file's text (UTF-8 or Windows-1254 detected) with ``\\n`` line ends."""
    text, encoding = textfiles.read_text(src)
    if note := textfiles.encoding_note(encoding):
        ctx.note(note)
    return text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


# --- sheets -----------------------------------------------------------------------------


def trim(rows: Sequence[Sequence[str]]) -> list[list[str]]:
    """Rows without trailing empty cells, and without empty rows at the end."""
    out = [list(row) for row in rows]
    for row in out:
        while row and not row[-1].strip():
            row.pop()
    while out and not out[-1]:
        out.pop()
    return out


def sheet_names(path: Path, format_id: str) -> list[str]:
    """Sheet names in workbook order (XLSX, ODS); [] when they cannot be read."""
    try:
        with zipfile.ZipFile(path) as zf:
            if format_id == "xlsx":
                root = ET.fromstring(zf.read("xl/workbook.xml"))
                return [s.get("name", "") for s in root.iter(f"{SML}sheet")]
            if format_id == "ods":
                with zf.open("content.xml") as f:
                    return [
                        el.get(TABLE_NAME, "")
                        for _event, el in ET.iterparse(f, events=("start",))
                        if el.tag == TABLE_TAG
                    ]
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
        pass
    return []


def _ordered(files: list[Path], names: list[str]) -> list[tuple[str, Path]]:
    """LibreOffice's ``input-<sheet>.csv`` files, in workbook order when it is known."""
    by_name = {office.sheet_name(f, "input"): f for f in files}
    ordered = [(n, by_name.pop(n)) for n in names if n in by_name]
    return ordered + sorted(by_name.items())


def read_sheets(ctx: JobContext, src: Path, source: str, workdir: Path) -> list[Sheet]:
    if source in DELIMITED:
        text = read_text(ctx, src)
        sep = "\t" if source == "tsv" else textfiles.sniff_delimiter(text)
        if source == "csv" and sep != ",":
            ctx.note(f"Read with {'semicolons' if sep == ';' else repr(sep)} as separator")
        try:
            rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=sep))
        except csv.Error as exc:
            raise JobError(f"Cannot read the table: {exc}") from exc
        return [("", rows)]
    workbook, kind = src, source
    if source == "xls":  # sheet order is read from the XLSX copy
        (workdir / "xlsx").mkdir()
        (workbook,) = office.convert_file(ctx, src, "xls", "xlsx", workdir / "xlsx")
        kind = "xlsx"
    (workdir / "csv").mkdir()
    files = office.convert_file(ctx, workbook, kind, "csv", workdir / "csv")
    sheets = []
    for name, path in _ordered(files, sheet_names(workbook, kind)):
        with path.open(encoding="utf-8", newline="") as f:
            sheets.append((name if len(files) > 1 else "", list(csv.reader(f))))
    return sheets


def sheets_markdown(sheets: Sequence[Sheet]) -> str:
    tables = [(name, trim(rows)) for name, rows in sheets]
    tables = [(name, rows) for name, rows in tables if rows]
    if not tables:
        raise JobError("The spreadsheet has no data")
    parts = []
    for name, rows in tables:
        if name and len(tables) > 1:
            parts.append(f"## {markdown.escape_inline(name)}")
        parts.append(markdown.pipe_table(rows))
    return "\n\n".join(parts)


# --- slides -----------------------------------------------------------------------------


def read_slides(ctx: JobContext, src: Path, source: str, workdir: Path) -> list[slidetext.Slide]:
    if source == "ppt":
        (pptx,) = office.convert_file(ctx, src, "ppt", "pptx", workdir)
        src, source = pptx, "pptx"
    slides = slidetext.read(src, source)
    if not slides:
        raise JobError("The presentation has no slides")
    return slides


def _depths(paragraphs: Sequence[slidetext.Paragraph]) -> list[int]:
    """List depths from the levels of consecutive bullets: the shallowest is 0, and a
    bullet is at most one level deeper than the one before it."""
    base = min(p.level for p in paragraphs)
    depths: list[int] = []
    for p in paragraphs:
        previous = depths[-1] if depths else -1
        depths.append(min(p.level - base, previous + 1))
    return depths


def _groups(blocks: Sequence[slidetext.Block]) -> list[list[slidetext.Block]]:
    """Consecutive bullets form one list; every other block stands alone."""
    groups: list[list[slidetext.Block]] = []
    for block in blocks:
        bullet = isinstance(block, slidetext.Paragraph) and block.bullet
        if bullet and groups and getattr(groups[-1][0], "bullet", False):
            groups[-1].append(block)
        else:
            groups.append([block])
    return groups


def slide_markdown(slide: slidetext.Slide) -> str:
    parts = [f"## {markdown.escape_inline(slide.heading)}"]
    for group in _groups(slide.blocks):
        first = group[0]
        if isinstance(first, slidetext.Table):
            parts.append(markdown.pipe_table(first.rows))
        elif first.bullet:
            items = []
            for p, depth in zip(group, _depths(group), strict=True):
                indent = "  " * depth
                lines = [markdown.escape_inline(line.strip()) for line in p.text.split("\n")]
                items.append(f"{indent}- " + f"\\\n{indent}  ".join(lines))
            parts.append("\n".join(items))
        else:
            parts.append(markdown.text_paragraphs(first.text))
    if slide.notes:
        notes = [markdown.text_paragraphs(n) for n in slide.notes]
        notes[0] = f"**Notes:** {notes[0]}"
        quoted = "\n>\n".join("\n".join(f"> {line}" for line in n.split("\n")) for n in notes)
        parts.append(quoted)
    return "\n\n".join(parts)


def slides_markdown(slides: Sequence[slidetext.Slide]) -> str:
    return "\n\n".join(slide_markdown(s) for s in slides)


def slide_text(slide: slidetext.Slide) -> str:
    heading = slide.heading
    parts = [f"{heading}\n{'=' * len(heading)}"]
    for group in _groups(slide.blocks):
        first = group[0]
        if isinstance(first, slidetext.Table):
            parts.append("\n".join("\t".join(c.replace("\n", " ") for c in r) for r in first.rows))
        elif first.bullet:
            lines = []
            for p, depth in zip(group, _depths(group), strict=True):
                indent = "  " * depth
                lines.append(f"{indent}- " + f"\n{indent}  ".join(p.text.split("\n")))
            parts.append("\n".join(lines))
        else:
            parts.append(first.text)
    if slide.notes:
        parts.append("Notes: " + "\n\n".join(slide.notes))
    return "\n\n".join(parts)


def slides_text(slides: Sequence[slidetext.Slide]) -> str:
    return "\n\n\n".join(slide_text(s) for s in slides) + "\n"


# --- the backend entry point ------------------------------------------------------------


def to_markdown(ctx: JobContext, src: Path, source: str, workdir: Path) -> str:
    fmt = formats.get(source)
    if fmt.category is Category.CODE:
        return markdown.fence(read_text(ctx, src), fmt.syntax)
    if source == "txt":
        result = markdown.text_paragraphs(read_text(ctx, src))
        if not result:
            raise JobError("The file has no text")
        return result
    if fmt.category is Category.SPREADSHEET:
        return sheets_markdown(read_sheets(ctx, src, source, workdir))
    if fmt.category is Category.SLIDES:
        return slides_markdown(read_slides(ctx, src, source, workdir))
    raise JobError(f"Cannot convert {fmt.label} as text")


def convert_into(ctx: JobContext, src: Path, route: Route, outputs) -> None:
    """Convert one file along ``route``; ``outputs`` allocates the result."""
    source, target = route.source, route.target
    fmt = formats.get(source)
    with tempfile.TemporaryDirectory(prefix="quince-text-") as tmp:
        workdir = Path(tmp)
        ctx.progress(None, f"Converting {src.name}")
        if target == "txt":
            if fmt.category is Category.CODE:
                text = read_text(ctx, src)
            elif fmt.category is Category.SLIDES:
                text = slides_text(read_slides(ctx, src, source, workdir))
            else:
                raise JobError(f"Cannot convert {fmt.label} to TXT")
            outputs.file().write_text(text, encoding="utf-8")
            return
        md = to_markdown(ctx, src, source, workdir)
        title = src.name if fmt.category is Category.CODE else None
        out = workdir / f"result.{formats.get(target).extension}"
        markdown.render(ctx, md, target, out, workdir, stem=src.stem, title=title)
        shutil.move(str(out), outputs.file())
