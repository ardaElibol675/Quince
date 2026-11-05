"""Writing GitHub-flavored Markdown from plain text, tables and code, and rendering it.

Quince writes this Markdown itself (pandoc 2.9's GFM writer does not escape ``|`` inside
table cells), escaping only what Markdown would otherwise read as markup, so the text comes
out exactly as it was. HTML, EPUB and PDF are then made from that Markdown with pandoc, the
same way as for PDF → DOCX:

- HTML: one self-contained page (``--standalone --self-contained``).
- EPUB: EPUB 3, titled after the file.
- PDF: through DOCX and LibreOffice, because pandoc's ODT writer does not highlight code and
  pandoc's own PDF needs LaTeX; code keeps its colors.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Sequence
from pathlib import Path

from quince.backends import office, pandoc
from quince.core.jobs import JobContext

# Characters that start markup anywhere in a line. ``|`` matters only in tables but is
# harmless elsewhere; ``~`` is GFM strikethrough.
_INLINE = re.compile(r"([\\`*_\[\]<>|~])")
_ENTITY = re.compile(r"&(?=#?[0-9A-Za-z]+;)")
# What makes a line a heading, quote, list item, rule or setext underline.
_LINE_START = re.compile(r"^(?:>|#{1,6}(?=\s|$)|[-+](?=\s|$)|=+\s*$|-+\s*$)")
_ORDERED = re.compile(r"^(\d{1,9})([.)])(?=\s|$)")
_BACKTICKS = re.compile(r"`+")
_BLANK = re.compile(r"\n[ \t\f\v]*\n")


def escape_inline(text: str) -> str:
    """``text`` as literal Markdown inline content."""
    return _ENTITY.sub(r"\\&", _INLINE.sub(r"\\\1", text))


def escape_line(line: str) -> str:
    """One line as literal Markdown, also where it could start a block. Leading spaces are
    dropped (four of them would make a code block)."""
    line = escape_inline(line.strip())
    line = _LINE_START.sub(lambda m: "\\" + m.group(), line)
    return _ORDERED.sub(r"\1\\\2", line)


def text_paragraphs(text: str) -> str:
    """Plain text as Markdown paragraphs: blank lines separate paragraphs, and every line
    break is kept (a backslash hard break), so the text looks as it did."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    paragraphs = []
    for block in _BLANK.split(text.strip("\n")):
        lines = [escape_line(line) for line in block.split("\n")]
        lines = [line for line in lines if line]
        if lines:
            paragraphs.append("\\\n".join(lines))
    return "\n\n".join(paragraphs)


def _cell(value: str) -> str:
    lines = [escape_inline(line.strip()) for line in value.replace("\r", "").split("\n")]
    return "<br>".join(line for line in lines if line)


def pipe_table(rows: Sequence[Sequence[str]]) -> str:
    """A GFM table; the first row is the header. Line breaks in a cell become ``<br>``."""
    width = max((len(row) for row in rows), default=0)
    if not width:
        return ""
    cells = [[_cell(v) for v in row] + [""] * (width - len(row)) for row in rows]
    sizes = [max(3, *(len(row[i]) for row in cells)) for i in range(width)]

    def line(row: list[str]) -> str:
        return "| " + " | ".join(v.ljust(n) for v, n in zip(row, sizes, strict=True)) + " |"

    rule = "| " + " | ".join("-" * n for n in sizes) + " |"
    return "\n".join([line(cells[0]), rule, *(line(row) for row in cells[1:])])


def fence(code: str, language: str = "") -> str:
    """``code`` as a fenced code block; the fence is longer than any backtick run inside."""
    code = code.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿").rstrip("\n")
    longest = max((len(m.group()) for m in _BACKTICKS.finditer(code)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{language}\n{code}\n{ticks}" if code else f"{ticks}{language}\n{ticks}"


def render(
    ctx: JobContext,
    markdown: str,
    target: str,
    out: Path,
    workdir: Path,
    *,
    stem: str,
    title: str | None = None,
) -> None:
    """Write ``markdown`` (written by this module) as ``target``: md, html, epub or pdf."""
    if target == "md":
        out.write_text(markdown.rstrip("\n") + "\n", encoding="utf-8")
        return
    source = workdir / f"{stem}.md"
    source.write_text(markdown, encoding="utf-8")
    options = [] if title is None else ["--metadata", f"title={title}"]
    ctx.progress(None, "Writing the document")
    if target == "pdf":
        docx = workdir / f"{stem}.docx"
        pandoc.run_pandoc(ctx, source, "gfm", "docx", docx, options=options, cwd=workdir)
        (workdir / "pdf").mkdir()
        (pdf,) = office.convert_file(ctx, docx, "docx", "pdf", workdir / "pdf")
        shutil.move(str(pdf), out)
        return
    options += pandoc.writer_options(target, stem)
    pandoc.run_pandoc(ctx, source, "gfm", pandoc.WRITERS[target], out, options=options, cwd=workdir)
