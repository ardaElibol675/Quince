"""Route coverage audit: which conversions the backends could do that the wheel does not offer.

Conversion routes are a hand-written table (``quince.operations.convert``), so a conversion
the backends can do is missing from the wheel until someone writes its line. This module
finds such gaps systematically:

1. ``CAPABILITIES`` declares, as data, what each backend reads and writes in one step,
   in Quince's format ids.
2. ``candidates`` is every source → target pair reachable from those one-step conversions
   and the existing routes, chained any number of times (``txt → docx → md`` makes
   ``txt → md`` a candidate).
3. A candidate is fine when it is a route or left out on purpose (``EXCLUSIONS``, each with
   its reason). Anything else is a gap. A unit test fails on gaps, so a new format can
   never leave silent ones.

``scripts/audit_routes.py`` prints the report.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from quince.core import formats
from quince.core.formats import Category
from quince.core.routes import RouteTable

Pair = tuple[str, str]


def ids(*items: str | Category) -> tuple[str, ...]:
    """Format ids from format ids and whole categories, in the order given."""
    out: list[str] = []
    for item in items:
        if isinstance(item, Category):
            out += [f.id for f in formats.in_category(item)]
        else:
            formats.get(item)  # unknown ids fail loudly
            out.append(item)
    return tuple(dict.fromkeys(out))


def but(group: Iterable[str], *left_out: str) -> tuple[str, ...]:
    return tuple(f for f in group if f not in left_out)


@dataclass(frozen=True)
class Capability:
    """One backend (or one part of it) converts every format it reads to every one it writes."""

    backend: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]

    def pairs(self) -> set[Pair]:
        return {(s, t) for s in self.reads for t in self.writes if s != t}


@dataclass(frozen=True)
class Exclusion:
    """Conversions from any of ``sources`` to any of ``targets`` left out on purpose."""

    sources: tuple[str, ...]
    targets: tuple[str, ...]
    reason: str

    def covers(self, pair: Pair) -> bool:
        return pair[0] in self.sources and pair[1] in self.targets


_I, _A, _V = Category.IMAGE, Category.AUDIO, Category.VIDEO
_D, _S, _X = Category.DOCUMENT, Category.SLIDES, Category.SPREADSHEET
_SUB, _ARC, _CODE = Category.SUBTITLE, Category.ARCHIVE, Category.CODE

ALL = tuple(f.id for f in formats.all_formats())
RASTER = but(ids(_I), "svg")
MEDIA = ids(_A, _V, "gif")
ARCHIVES_RW = but(ids(_ARC), "rar")
LEGACY_OFFICE = ("doc", "rtf", "ppt", "xls")
SHEETS = ids(_X)
WORKBOOKS = ("xlsx", "xls", "ods")
SLIDE_FILES = ids(_S)
TEXT_DOCS = ("txt", "md", "html", "epub")  # reflowing text: no fixed pages
SUBTITLES = ids(_SUB)
CODE = ids(_CODE)

# What each backend reads and writes in ONE step, in Quince's format ids. Only formats that
# Quince knows; e.g. LibreOffice also writes DOC (Word 97) — that is a capability, and
# whether Quince offers it is decided by routes and exclusions, not here.
CAPABILITIES: tuple[Capability, ...] = (
    Capability("Pillow", RASTER, (*RASTER, "pdf")),
    Capability("rsvg-convert", ("svg",), ("png", "pdf")),
    Capability("ffmpeg", MEDIA, MEDIA),
    Capability(
        "LibreOffice Writer",
        ("docx", "doc", "odt", "rtf", "txt", "html"),
        ("docx", "doc", "odt", "rtf", "txt", "html", "epub", "pdf"),
    ),
    Capability("LibreOffice Calc", (*SHEETS, "html"), (*SHEETS, "html", "pdf")),
    Capability("LibreOffice Impress", SLIDE_FILES, (*SLIDE_FILES, "html", "pdf")),
    Capability(
        "pandoc",
        ("docx", "odt", "epub", "html", "md", "csv"),
        ("md", "docx", "odt", "html", "epub", "txt"),
    ),
    Capability("PyMuPDF", ("pdf",), ("txt", "png", "jpg")),
    Capability("PyMuPDF (documents in)", ("epub", "svg", *RASTER), ("pdf",)),
    Capability("pymupdf4llm", ("pdf",), ("md",)),
    Capability("pysubs2", SUBTITLES, (*SUBTITLES, "txt")),
    Capability("Python zipfile/tarfile, 7z", ARCHIVES_RW, ARCHIVES_RW),
    Capability("unrar", ("rar",), ARCHIVES_RW),
)  # fmt: skip

# Conversions left out on purpose. Each entry must cover at least one candidate and no
# route (unit-tested), so the list stays honest.
EXCLUSIONS: tuple[Exclusion, ...] = (
    # --- sound and moving pictures ---------------------------------------------------
    Exclusion(
        but(ALL, *MEDIA),
        ids(_A, _V),
        "Only audio, video and animated GIFs hold sound or moving pictures; a document "
        "that reaches a GIF through its page images is no video.",
    ),
    Exclusion(
        ids(_A),
        (*ids(_V), "gif"),
        "A video needs a picture; the Sound Editor makes one (its Video mode).",
    ),
    Exclusion(
        ids(_A, _V),
        but(ALL, *MEDIA),
        "Audio and video hold no document text; pictures from a video are the animated "
        "GIF (a route) and Save Frames in the Video Editor.",
    ),
    Exclusion(
        ids(_V),
        but(ids(_A), "mp3", "m4a", "wav"),
        "User decision (2026-11-04): a video's sound track as MP3, M4A or WAV only.",
    ),
    Exclusion(
        ("gif",),
        ("avi", "wmv", *ids(_A)),
        "An animated GIF becomes a modern video (MP4, MOV, MKV, WebM); it has no sound.",
    ),
    # --- images --------------------------------------------------------------------------
    Exclusion(
        ids(_I),
        ids(_D, _S, _X, _SUB, _CODE),
        "An image holds no text; reading text from pictures (OCR) is not a format conversion.",
    ),
    Exclusion(
        (*TEXT_DOCS, *SHEETS, *CODE, *SUBTITLES),
        RASTER,
        "Page images are offered for files with a fixed page layout (office documents, "
        "slides, PDF). Text, Markdown, HTML, EPUB, sheets and code reflow, so their pages "
        "would be an accident of the paper size; convert to PDF instead.",
    ),
    Exclusion(
        (*but(ids(_D), *TEXT_DOCS), *SLIDE_FILES, "pdf"),
        but(RASTER, "png", "jpg"),
        "Page images come as PNG (sharp text) or JPG (small); other image formats can be "
        "made from those.",
    ),
    # --- documents, slides, sheets -------------------------------------------------------
    Exclusion(
        but(ALL, *ids(_I), *MEDIA),
        LEGACY_OFFICE,
        "DOC, RTF, PPT and XLS are read but not written: the 1997–2007 formats lose "
        "features, and DOCX/ODT, PPTX/ODP and XLSX/ODS open everywhere.",
    ),
    Exclusion(
        ("pdf",),
        ("odp", "ods", "csv", "tsv"),
        "PDF → slides and sheets: PPTX (page pictures) and XLSX (ruled tables) are "
        "offered; the OpenDocument and CSV twins add nothing.",
    ),
    Exclusion(
        (*ids(_D), *SHEETS, *CODE, *SUBTITLES),
        SLIDE_FILES,
        "Text, documents, sheets and code have no slide layout; making slides from them "
        "is authoring, not conversion.",
    ),
    Exclusion(
        (*ids(_D), *SLIDE_FILES, *CODE, *SUBTITLES),
        SHEETS,
        "Only tables become sheets; documents, slides and code are not tabular.",
    ),
    Exclusion(
        (*SLIDE_FILES, *SHEETS),
        ("docx", "odt", "epub"),
        "Slides and sheets → word-processor documents and e-books: PDF keeps the layout, "
        "Markdown and TXT (slides) or CSV (sheets) the content.",
    ),
    Exclusion(
        SLIDE_FILES,
        ("html",),
        "LibreOffice writes slides as one HTML page per slide plus pictures; the slide "
        "text is offered as Markdown and TXT, the layout as PDF.",
    ),
    Exclusion(
        SHEETS,
        ("txt",),
        "CSV and TSV are a sheet's plain text; Markdown gives readable tables.",
    ),
    # --- subtitles and code --------------------------------------------------------------
    Exclusion(
        SUBTITLES,
        but(ids(_D), "txt"),
        "Subtitles → documents: the spoken text is offered as TXT; timings make no document.",
    ),
    Exclusion(
        SUBTITLES,
        ("pdf",),
        "Subtitles → PDF: the spoken text is offered as TXT.",
    ),
    Exclusion(
        CODE,
        ("docx", "odt", "epub"),
        "Source code as a word-processor document or e-book is rarely useful; HTML and "
        "PDF (highlighted) cover reading and printing.",
    ),
)


def one_step() -> set[Pair]:
    """Every source → target pair some backend converts in one step."""
    pairs: set[Pair] = set()
    for capability in CAPABILITIES:
        pairs |= capability.pairs()
    return pairs


def candidates(table: RouteTable) -> set[Pair]:
    """Pairs reachable through one-step conversions and routes, chained (transitive closure)."""
    edges: dict[str, set[str]] = {}
    for s, t in one_step() | {(r.source, r.target) for r in table}:
        edges.setdefault(s, set()).add(t)
    reachable: set[Pair] = set()
    for start in edges:
        seen: set[str] = set()
        todo = [start]
        while todo:
            for nxt in edges.get(todo.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        reachable |= {(start, t) for t in seen if t != start}
    return reachable


def excluded_by(pair: Pair) -> Exclusion | None:
    return next((e for e in EXCLUSIONS if e.covers(pair)), None)


def _sorted(pairs: Iterable[Pair]) -> list[Pair]:
    return sorted(pairs, key=lambda p: (formats.position(p[0]), formats.position(p[1])))


def gaps(table: RouteTable) -> list[Pair]:
    """Candidates that are neither a route nor left out on purpose."""
    routes = {(r.source, r.target) for r in table}
    return _sorted(p for p in candidates(table) - routes if excluded_by(p) is None)


def excluded_routes(table: RouteTable) -> list[Pair]:
    """Routes an exclusion covers: the two lists contradict each other."""
    return _sorted((r.source, r.target) for r in table if excluded_by((r.source, r.target)))


def unused_exclusions(table: RouteTable) -> list[Exclusion]:
    """Exclusions that cover no candidate (stale entries)."""
    found = candidates(table)
    return [e for e in EXCLUSIONS if not any(e.covers(p) for p in found)]
