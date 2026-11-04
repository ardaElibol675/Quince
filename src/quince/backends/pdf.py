"""PDF conversions with PyMuPDF and pymupdf4llm.

**PDF → Markdown** (a priority route):

1. Pages with (almost) no text but a page-sized image are *scanned*. They are read with OCR
   (Tesseract inside PyMuPDF, Turkish + English, 300 DPI). Without the Tesseract language
   data the conversion stops and says what to install; it never writes a partial file.
2. The other pages go through ``pymupdf4llm`` (headings from font sizes, bold/italic, lists,
   ruled tables, images), one page at a time with headings measured on the whole document.
3. Links get their exact text back (pymupdf4llm links whole text spans).
4. Running headers, footers and page numbers are removed: lines in the top or bottom 12 % of
   the page that are page numbers, or are smaller than the body text and repeated on at least
   half of the pages; only among the first and last three lines of a page's text, so body
   text is never lost. A paragraph cut by a page break is joined again.
5. ``tidy_markdown`` cleans the rest: emphasis inside headings, heading levels starting at
   ``#``, list spacing and indentation, lines of one paragraph broken where the PDF line
   ended, hyphenated words, line breaks inside table cells.

Images go to ``media/`` (a folder ``name/`` with ``name.md``); without images, one ``.md``.

Other targets: TXT (page text, OCR for scans), PNG/JPG (every page at 300 DPI), DOCX/ODT/EPUB
(the Markdown through pandoc: editable flowing text), PPTX (every page as a slide image),
XLSX (every ruled table as a sheet).
"""

from __future__ import annotations

import base64
import contextlib
import glob
import io
import math
import os
import re
import shutil
import sys
import tempfile
import threading
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from quince.backends import office, pandoc
from quince.core.jobs import JobContext, JobError
from quince.core.routes import Route

MIN_TEXT_CHARS = 10  # a page with fewer characters and a page-sized image is a scan
SCAN_COVERAGE = 0.5
OCR_DPI = 300
OCR_LANGUAGES = "tur+eng"
OCR_HINT = "sudo apt install tesseract-ocr tesseract-ocr-tur"
PAGE_DPI = 300
SLIDE_JPEG_QUALITY = 90
RUNNING_LINE_SHARE = 0.5  # a line at the edge of at least half the pages is a header/footer
EDGE_ZONE = 0.12  # the top and bottom 12 % of a page hold its header and footer
EDGE_LINES = 3  # only this many lines at the start and end of a page's text may be removed
UNREADABLE = "*[Page {n}: no text could be read]*"

_PAGE_NUMBER = re.compile(
    r"^[\s\-–—|*_]*(?:(?:page|sayfa|s\.)\s*)?\d+(?:\s*(?:/|of|-|’den|'den)\s*\d+)?[\s\-–—|*_]*$",
    re.IGNORECASE,
)
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


# --- opening and classifying ----------------------------------------------------------


def open_pdf(path: Path):
    import pymupdf

    try:
        doc = pymupdf.open(path)
    except (RuntimeError, ValueError) as exc:  # pymupdf raises FileDataError (a RuntimeError)
        raise JobError("Not a readable PDF", str(exc)) from exc
    if doc.needs_pass:
        doc.close()
        raise JobError("The PDF is password-protected")
    if doc.page_count == 0:
        doc.close()
        raise JobError("The PDF has no pages")
    return doc


def is_scanned(page) -> bool:
    """True for a page that is only a picture of text."""
    import pymupdf

    if len(page.get_text("text").strip()) >= MIN_TEXT_CHARS:
        return False
    area = abs(page.rect)
    covered = sum(abs(pymupdf.Rect(i["bbox"]) & page.rect) for i in page.get_image_info())
    return area > 0 and covered >= SCAN_COVERAGE * area


def tessdata_dir() -> str | None:
    """A Tesseract data folder holding the Turkish and English models, or None."""
    candidates = [os.environ.get("TESSDATA_PREFIX", "")]
    candidates += sorted(glob.glob("/usr/share/tesseract-ocr/*/tessdata"), reverse=True)
    candidates += ["/usr/share/tessdata"]
    for folder in candidates:
        if folder and all(
            os.path.isfile(os.path.join(folder, f"{lang}.traineddata")) for lang in ("tur", "eng")
        ):
            return folder
    return None


def pages_phrase(numbers: Sequence[int]) -> str:
    shown = ", ".join(map(str, numbers[:8])) + (" …" if len(numbers) > 8 else "")
    return f"Page {shown} is" if len(numbers) == 1 else f"Pages {shown} are"


def require_ocr(scanned: Sequence[int]) -> str:
    folder = tessdata_dir()
    if folder is None:
        need = "needs" if len(scanned) == 1 else "need"
        raise JobError(
            f"{pages_phrase([n + 1 for n in scanned])} scanned and {need} OCR (Turkish and "
            f"English). Install it with: {OCR_HINT}"
        )
    return folder


def ocr_text(page, tessdata: str) -> str:
    """The text of a scanned page as paragraphs, read by Tesseract."""
    textpage = page.get_textpage_ocr(
        language=OCR_LANGUAGES, dpi=OCR_DPI, full=True, tessdata=tessdata
    )
    blocks = page.get_text("blocks", textpage=textpage, sort=True)
    paragraphs = [" ".join(b[4].split()) for b in blocks if b[6] == 0 and b[4].strip()]
    return "\n\n".join(paragraphs)


# --- Markdown ---------------------------------------------------------------------------


_IMPORT_LOCK = threading.Lock()


def _llm():
    """The ``pymupdf4llm`` module. Its import prints an advertisement for an extra package
    to stdout, so the first import runs with stdout redirected, once and under a lock
    (``redirect_stdout`` swaps ``sys.stdout`` for the whole process, so it must never be
    used around calls that run while other threads print)."""
    with _IMPORT_LOCK:
        if "pymupdf4llm" not in sys.modules:
            with contextlib.redirect_stdout(io.StringIO()):
                import pymupdf4llm  # noqa: F401
    return sys.modules["pymupdf4llm"]


def fix_links(text: str, page) -> str:
    """pymupdf4llm turns a whole text span into the link; cut it back to the linked words."""
    for link in page.get_links():
        uri = link.get("uri")
        if not uri:
            continue
        anchor = " ".join(page.get_textbox(link["from"]).split())
        if not anchor:
            continue

        def shrink(match: re.Match, anchor: str = anchor, uri: str = uri) -> str:
            inner = " ".join(match.group(1).split())
            if inner == anchor or anchor not in inner:
                return match.group(0)
            before, after = inner.split(anchor, 1)
            return f"{before}[{anchor}]({uri}){after}"

        text = re.sub(r"\[([^\]]+)\]\(" + re.escape(uri) + r"\)", shrink, text)
    return text


def _edge_key(line: str) -> str:
    key = re.sub(r"\d+", "#", re.sub(r"[*_#>`|\\]", "", line)).lower()
    return " ".join(key.split())


def _body_size(doc) -> float:
    """The font size most characters are set in."""
    sizes: Counter[float] = Counter()
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    sizes[round(span["size"], 1)] += len(span["text"].strip())
    return sizes.most_common(1)[0][0] if sizes else 0.0


def running_lines(doc) -> list[set[str]]:
    """Per page, the keys of its running header/footer and page-number lines.

    A line counts only if it sits in the top or bottom ``EDGE_ZONE`` of the page, and either
    is a page number, or is set smaller than the body text and repeats on at least half of
    the pages. Body-sized text is never taken, so repeated content cannot be lost.
    """
    body = _body_size(doc)
    edges: list[list[tuple[str, bool]]] = []  # (text, smaller than the body) per page
    for page in doc:
        height = page.rect.height
        found = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                y0, y1 = line["bbox"][1], line["bbox"][3]
                if EDGE_ZONE * height < y1 and y0 < (1 - EDGE_ZONE) * height:
                    continue
                spans = line["spans"]
                text = "".join(s["text"] for s in spans).strip()
                if text:
                    found.append((text, max(s["size"] for s in spans) < body - 0.5))
        edges.append(found)
    counts: Counter[str] = Counter()
    for found in edges:
        counts.update({_edge_key(text) for text, small in found if small})
    threshold = max(2, math.ceil(RUNNING_LINE_SHARE * len(edges)))
    result = []
    for found in edges:
        keys = set()
        for text, small in found:
            key = _edge_key(text)
            if key and ((small and counts[key] >= threshold) or _PAGE_NUMBER.match(text)):
                keys.add(key)
        result.append(keys)
    return result


def _only_running(line: str, keys: set[str]) -> bool:
    """True for a line made only of running pieces. pymupdf4llm may put pieces that sit on
    the same height into one line ("CH2 Basic Physics of Semiconductors 10")."""
    key = _edge_key(line)
    if not key:
        return False
    if key in keys:
        return True
    rest = key
    for piece in sorted(keys, key=len, reverse=True):
        rest = rest.replace(piece, " ")
    return rest != key and not re.sub(r"[\W#]", "", rest)


def strip_running_lines(pages: list[str], running: list[set[str]]) -> list[str]:
    """Remove each page's running header/footer and page-number lines from its text."""
    result = []
    for text, keys in zip(pages, running, strict=True):
        if keys:
            lines = text.split("\n")
            filled = [i for i, line in enumerate(lines) if line.strip()]
            edge = set(filled[:EDGE_LINES] + filled[-EDGE_LINES:])
            lines = [x for i, x in enumerate(lines) if i not in edge or not _only_running(x, keys)]
            text = "\n".join(lines)
        result.append(text)
    return result


def _plain(line: str) -> bool:
    """A line of running text (not a heading, list item, table row, image, rule or fence)."""
    s = line.strip()
    return bool(s) and not (
        _HEADING.match(s) or _LIST_ITEM.match(line) or s.startswith(("|", "![", "```", "---", ">"))
    )


def _list_indents(lines: list[str]) -> list[str]:
    """Re-indent each run of list items: four spaces per level, levels from the original
    indentation; no blank lines inside the run."""
    out: list[str] = []
    run: list[tuple[int, str, str]] = []

    def flush() -> None:
        levels = sorted({indent for indent, _m, _t in run})
        for indent, marker, text in run:
            out.append(" " * 4 * levels.index(indent) + f"{marker} {text}")
        run.clear()

    def kind(marker: str) -> str:
        return "bullet" if marker in "-*+" else "number"

    for i, line in enumerate(lines):
        match = _LIST_ITEM.match(line)
        if match:
            run.append((len(match.group(1)), match.group(2), match.group(3).strip()))
            continue
        if run and not line.strip():
            nxt = _LIST_ITEM.match(next((x for x in lines[i + 1 :] if x.strip()), ""))
            indent, marker, _text = run[-1]
            if nxt and (len(nxt.group(1)) > indent or kind(nxt.group(2)) == kind(marker)):
                continue  # a blank line between two items of one list
        if run:
            flush()
        out.append(line)
    if run:
        flush()
    return out


def _join_paragraphs(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if out and _plain(line) and _plain(out[-1]):
            prev = out[-1].rstrip()
            if re.search(r"\w-$", prev) and re.match(r"[a-zçğıöşü]", line.strip()):
                out[-1] = prev[:-1] + line.strip()  # a word hyphenated at the line end
            else:
                out[-1] = prev + " " + line.strip()
        else:
            out.append(line.rstrip())
    return out


def join_pages(pages: list[str]) -> str:
    """Put the pages together; a paragraph cut by a page break becomes one again (the page
    ends without a sentence end and the next one starts in lower case)."""
    text = ""
    for page in pages:
        body = page.strip("\n")
        if not body.strip():
            continue
        if text:
            last = text.rstrip().rsplit("\n", 1)[-1]
            first = body.lstrip().split("\n", 1)[0]
            continued = (
                _plain(last)
                and _plain(first)
                and not re.search(r"[.!?:;…]\W*$", last)
                and re.match(r"[a-zçğıöşü]", first.strip())
            )
            if continued:
                if re.search(r"\w-$", last):
                    text = text.rstrip()[:-1] + body.lstrip()
                else:
                    text = text.rstrip() + " " + body.lstrip()
                continue
            text = text.rstrip() + "\n\n"
        text += body
    return text


def _space_headings(lines: list[str]) -> list[str]:
    """Give every heading a blank line before and after it."""
    out: list[str] = []
    for line in lines:
        heading = bool(_HEADING.match(line.strip()))
        if heading and out and out[-1].strip():
            out.append("")
        if out and out[-1].strip() and _HEADING.match(out[-1].strip()) and line.strip():
            out.append("")
        out.append(line)
    return out


def _heading_levels(lines: list[str]) -> list[str]:
    """Close gaps in heading levels so the top level used is ``#`` (slides often come out
    as ``#####`` because their titles are the only large text)."""
    used = sorted({len(m.group(1)) for line in lines if (m := _HEADING.match(line))})
    mapping = {level: n for n, level in enumerate(used, start=1)}
    out = []
    for line in lines:
        m = _HEADING.match(line)
        out.append(f"{'#' * mapping[len(m.group(1))]} {m.group(2)}" if m else line)
    return out


def tidy_markdown(text: str) -> str:
    lines = []
    for line in text.split("\n"):
        if re.fullmatch(r"\s*#{1,6}\s*", line):
            continue  # an empty heading
        if line.lstrip().startswith("|"):
            # pymupdf4llm marks line breaks inside table cells with <br>; a GFM cell is one line.
            line = re.sub(r"\s*<br\s*/?>\s*", " ", line)
        heading = _HEADING.match(line.strip())
        if heading:
            title = re.sub(r"\*\*|__", "", heading.group(2))
            title = re.sub(r"^_(.*)_$", r"\1", " ".join(title.split()))
            line = f"{heading.group(1)} {title}"
        lines.append(line)
    lines = _space_headings(_join_paragraphs(_list_indents(_heading_levels(lines))))
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text + "\n"


def _identify_headers(doc, pages: list[int]):
    return _llm().IdentifyHeaders(doc, pages=pages)


def page_markdowns(ctx: JobContext | None, doc, media: Path | None) -> tuple[list[str], list[str]]:
    """Markdown per page, and notes for the card."""
    scanned = [i for i, page in enumerate(doc) if is_scanned(page)]
    tessdata = require_ocr(scanned) if scanned else None
    text_pages = [i for i in range(doc.page_count) if i not in scanned]
    pymupdf4llm = _llm()
    headers = _identify_headers(doc, text_pages) if text_pages else None
    pages: list[str] = []
    unreadable: list[int] = []
    for i in range(doc.page_count):
        if ctx is not None:
            ctx.check()
            ctx.progress(0.9 * i / doc.page_count, f"Page {i + 1} of {doc.page_count}")
        page = doc[i]
        if i in scanned:
            text = ocr_text(page, tessdata)
        else:
            chunk = pymupdf4llm.to_markdown(
                doc,
                pages=[i],
                hdr_info=headers,
                page_chunks=True,
                write_images=media is not None,
                image_path=str(media) if media is not None else "",
                show_progress=False,
            )[0]
            text = fix_links(chunk["text"], page)
        if not text.strip() and (i in scanned or page.get_images()):
            unreadable.append(i + 1)
            text = UNREADABLE.format(n=i + 1)
        pages.append(text)
    notes = []
    if scanned:
        notes.append(f"{pages_phrase([n + 1 for n in scanned])} read with OCR")
    if unreadable:
        notes.append(f"No text could be read on page {', '.join(map(str, unreadable))}")
    return pages, notes


def to_markdown(ctx: JobContext | None, src: Path, result: Path, stem: str) -> Path:
    """Write ``result/stem.md`` (and ``result/media/``); returns the Markdown file."""
    media = result / "media"
    with contextlib.closing(open_pdf(src)) as doc:
        pages, notes = page_markdowns(ctx, doc, media)
        running = running_lines(doc)
    text = tidy_markdown(join_pages(strip_running_lines(pages, running)))
    if not re.sub(r"\*\[Page \d+: no text could be read\]\*", "", text).strip():
        raise JobError("No text found in this PDF")
    text = text.replace(f"]({media}/", "](media/")
    md = result / f"{stem}.md"
    md.write_text(text, encoding="utf-8")
    if media.is_dir() and not any(media.iterdir()):
        media.rmdir()
    if ctx is not None:
        for note in notes:
            ctx.note(note)
    return md


# --- other targets ------------------------------------------------------------------------


def to_text(ctx: JobContext, src: Path, out: Path) -> None:
    with contextlib.closing(open_pdf(src)) as doc:
        scanned = [i for i, page in enumerate(doc) if is_scanned(page)]
        tessdata = require_ocr(scanned) if scanned else None
        pages = []
        for i, page in enumerate(doc):
            ctx.check()
            ctx.progress(i / doc.page_count, f"Page {i + 1} of {doc.page_count}")
            if i in scanned:
                pages.append(ocr_text(page, tessdata))
            else:
                pages.append(page.get_text("text", sort=True).strip())
        running = running_lines(doc)
    text = join_pages(strip_running_lines(pages, running))
    if not text.strip():
        raise JobError("No text found in this PDF")
    out.write_text(text.strip() + "\n", encoding="utf-8")
    if scanned:
        ctx.note(f"{pages_phrase([n + 1 for n in scanned])} read with OCR")


def _fodp(slides: list[bytes], width_pt: float, height_pt: float, sizes) -> str:
    """A flat OpenDocument presentation: one full-page picture per slide."""
    cm = 2.54 / 72
    w, h = width_pt * cm, height_pt * cm
    pages = []
    for n, (data, (iw, ih)) in enumerate(zip(slides, sizes, strict=True), start=1):
        scale = min(w / (iw * cm), h / (ih * cm))
        fw, fh = iw * cm * scale, ih * cm * scale
        x, y = (w - fw) / 2, (h - fh) / 2
        pages.append(
            f'<draw:page draw:name={quoteattr(f"page{n}")} draw:master-page-name="Default">'
            f'<draw:frame svg:x="{x:.3f}cm" svg:y="{y:.3f}cm" svg:width="{fw:.3f}cm" '
            f'svg:height="{fh:.3f}cm"><draw:image><office:binary-data>'
            f"{base64.b64encode(data).decode()}</office:binary-data></draw:image></draw:frame>"
            "</draw:page>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
        'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
        'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
        'office:version="1.2" office:mimetype="application/vnd.oasis.opendocument.presentation">'
        "<office:automatic-styles>"
        '<style:page-layout style:name="PM1">'
        f'<style:page-layout-properties fo:page-width="{w:.3f}cm" '
        f'fo:page-height="{h:.3f}cm" fo:margin-top="0cm" fo:margin-bottom="0cm" '
        'fo:margin-left="0cm" fo:margin-right="0cm"/></style:page-layout>'
        "</office:automatic-styles><office:master-styles>"
        '<style:master-page style:name="Default" style:page-layout-name="PM1"/>'
        "</office:master-styles><office:body><office:presentation>"
        + "".join(pages)
        + "</office:presentation></office:body></office:document>"
    )


def to_pptx(ctx: JobContext, src: Path, workdir: Path, outputs) -> None:
    slides, sizes = [], []
    with contextlib.closing(open_pdf(src)) as doc:
        first = doc[0].rect
        for i, page in enumerate(doc):
            ctx.check()
            ctx.progress(0.8 * i / doc.page_count, f"Page {i + 1} of {doc.page_count}")
            pix = page.get_pixmap(dpi=PAGE_DPI, alpha=False)
            slides.append(pix.tobytes("jpeg", jpg_quality=SLIDE_JPEG_QUALITY))
            sizes.append((pix.width, pix.height))
    fodp = workdir / "slides.fodp"
    fodp.write_text(_fodp(slides, first.width, first.height, sizes), encoding="utf-8")
    (workdir / "lo").mkdir()
    (pptx,) = office.soffice(ctx, fodp, office.IMPRESS["pptx"], workdir / "lo")
    shutil.move(str(pptx), outputs.file())


_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


def _cell(value) -> str:
    text = "" if value is None else " ".join(str(value).split())
    if _NUMBER.match(text):
        return (
            f'<table:table-cell office:value-type="float" office:value="{text}">'
            f"<text:p>{escape(text)}</text:p></table:table-cell>"
        )
    return (
        f'<table:table-cell office:value-type="string"><text:p>{escape(text)}</text:p>'
        "</table:table-cell>"
    )


def _fods(sheets: list[tuple[str, list[list]]]) -> str:
    body = []
    for name, rows in sheets:
        body.append(f"<table:table table:name={quoteattr(name)}>")
        for row in rows:
            body.append("<table:table-row>" + "".join(_cell(c) for c in row) + "</table:table-row>")
        body.append("</table:table>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2" '
        'office:mimetype="application/vnd.oasis.opendocument.spreadsheet">'
        "<office:body><office:spreadsheet>" + "".join(body) + "</office:spreadsheet></office:body>"
        "</office:document>"
    )


def find_tables(ctx: JobContext | None, src: Path) -> list[tuple[str, list[list]]]:
    """Every ruled table: (sheet name, rows)."""
    sheets = []
    with contextlib.closing(open_pdf(src)) as doc:
        for i, page in enumerate(doc):
            if ctx is not None:
                ctx.check()
                ctx.progress(0.7 * i / doc.page_count, f"Page {i + 1} of {doc.page_count}")
            tables = page.find_tables().tables
            for k, table in enumerate(tables, start=1):
                rows = [row for row in table.extract() if any(c not in (None, "") for c in row)]
                if rows:
                    name = f"Page {i + 1}" + (f" ({k})" if len(tables) > 1 else "")
                    sheets.append((name, rows))
    return sheets


def to_xlsx(ctx: JobContext, src: Path, workdir: Path, outputs) -> None:
    sheets = find_tables(ctx, src)
    if not sheets:
        raise JobError("No tables found in this PDF (only tables with ruled lines are detected)")
    fods = workdir / "tables.fods"
    fods.write_text(_fods(sheets), encoding="utf-8")
    (workdir / "lo").mkdir()
    (xlsx,) = office.soffice(ctx, fods, office.CALC["xlsx"], workdir / "lo")
    shutil.move(str(xlsx), outputs.file())
    ctx.note(f"{len(sheets)} table{'s' if len(sheets) != 1 else ''} found")


def _markdown_result(ctx: JobContext, src: Path, workdir: Path) -> Path:
    result = workdir / "result"
    result.mkdir()
    return to_markdown(ctx, src, result, src.stem)


def convert_into(ctx: JobContext, src: Path, route: Route, outputs) -> None:
    """Convert one PDF along ``route``; ``outputs`` allocates the result (file or folder)."""
    target = route.target
    with tempfile.TemporaryDirectory(prefix="quince-pdf-") as tmp:
        workdir = Path(tmp)
        if target == "md":
            md = _markdown_result(ctx, src, workdir)
            if (md.parent / "media").is_dir():
                folder = outputs.folder()
                for item in md.parent.iterdir():
                    shutil.move(str(item), folder / item.name)
            else:
                shutil.move(str(md), outputs.file())
        elif target == "txt":
            to_text(ctx, src, outputs.file())
        elif target in ("png", "jpg"):
            open_pdf(src).close()  # explains broken or locked files before rendering
            office.render_pages(ctx, src, src, target, outputs, dpi=PAGE_DPI)
        elif target in ("docx", "odt", "epub"):
            md = _markdown_result(ctx, src, workdir)
            ctx.progress(0.95, "Writing the document")
            result = pandoc.run_pandoc(
                ctx, md, "gfm", pandoc.WRITERS[target], outputs.file(),
                options=pandoc.writer_options(target, src.stem), cwd=md.parent,
                resource_dir=md.parent,
            )  # fmt: skip
            if any(pandoc.REMOTE_MARKER in line for line in result.stderr_tail):
                ctx.note(pandoc.REMOTE_NOTE)
        elif target == "pptx":
            to_pptx(ctx, src, workdir, outputs)
        elif target == "xlsx":
            to_xlsx(ctx, src, workdir, outputs)
        else:
            raise JobError(f"Cannot convert PDF to {target}")


# --- quince doctor ----------------------------------------------------------------------

SELF_TEST_EXPECT = ("# Öz Test Başlığı", "**Kesin**", "- Bir", "- İki", "Son satır: ğüşöç ĞÜŞİÖÇ")


def _self_test_pdf(path: Path) -> None:
    """A one-page PDF with a heading, bold text, a list and Turkish letters. Fonts are built
    into MuPDF (Noto Serif for the Turkish letters, Helvetica-Bold for the bold word), so no
    system font is needed."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    regular = pymupdf.Font()  # MuPDF's built-in Noto Serif, which has the Turkish letters
    page.insert_font(fontname="R", fontbuffer=regular.buffer)
    page.insert_text((56, 90), "Öz Test Başlığı", fontname="R", fontsize=22)
    page.insert_text((56, 130), "Kesin", fontname="hebo", fontsize=11)
    width = pymupdf.get_text_length("Kesin ", fontname="hebo", fontsize=11)
    page.insert_text((56 + width, 130), "ve eğik söz.", fontname="R", fontsize=11)
    page.insert_text((56, 160), "- Bir", fontname="R", fontsize=11)
    page.insert_text((56, 176), "- İki", fontname="R", fontsize=11)
    page.insert_text((56, 206), "Son satır: ğüşöç ĞÜŞİÖÇ", fontname="R", fontsize=11)
    doc.save(path)


def self_test() -> tuple[bool, str]:
    """A real PDF → Markdown conversion for ``quince doctor``: (passed, explanation)."""
    with tempfile.TemporaryDirectory(prefix="quince-selftest-") as tmp:
        workdir = Path(tmp)
        source = workdir / "selftest.pdf"
        try:
            _self_test_pdf(source)
            result = workdir / "result"
            result.mkdir()
            text = to_markdown(None, source, result, "selftest").read_text(encoding="utf-8")
        except JobError as exc:
            return False, exc.reason
        missing = [part for part in SELF_TEST_EXPECT if part not in text]
        if missing:
            return False, f"missing from the result: {', '.join(missing)}"
    ocr = "OCR ready" if tessdata_dir() else f"OCR not installed ({OCR_HINT})"
    return True, f"headings, bold, lists and Turkish letters kept; {ocr}"
