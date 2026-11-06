"""The pages of one or more PDFs as data, and writing them with PyMuPDF (the PDF Editor).

The edit is a tuple of ``Page``s in output order. A page points to a page of a ``Source``
(a PDF, or a picture made into PDF pages when it was added) or is blank; it carries its own
extra turn. A duplicate is another ``Page`` (its own ``uid``) pointing to the same page.

Writing keeps what makes a PDF useful. The first source used is opened as it is (metadata,
forms, annotations; page labels too while its pages stay in order), the pages of the others
are inserted after it (their links between their own pages keep working), and
``Document.select`` puts the pages in order: text stays selectable, links between kept pages
point to their new place, links to removed pages are dropped. Turns change ``/Rotate``
(lossless). Bookmarks of every source that point to a kept page are kept (to its first copy);
a bookmark under a removed one moves up a level.
"""

from __future__ import annotations

import logging
import re
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from quince.core.jobs import JobContext, JobError

log = logging.getLogger(__name__)

BLANK = -1
MAX_PARTS = 500
A4 = (595.28, 841.89)  # points
PICTURE_FORMATS = frozenset({"jpg", "png", "webp", "heic", "tiff", "avif", "bmp", "gif", "svg"})
ADDABLE = PICTURE_FORMATS | {"pdf"}
NOT_ADDABLE = "only PDFs and pictures can be added"

Size = tuple[float, float]


@dataclass(frozen=True)
class Page:
    uid: int
    source: int  # index into the sources; BLANK for a blank page
    number: int = 0  # page index in its source
    turns: int = 0  # extra quarter turns, clockwise
    blank_size: Size = (0.0, 0.0)  # a blank page's size in points

    @property
    def blank(self) -> bool:
        return self.source == BLANK


@dataclass(frozen=True, eq=False)
class Source:
    """A file whose pages can be used: ``sizes`` are the pages' sizes as shown (points);
    ``data`` holds the PDF made from a picture."""

    path: Path
    format_id: str
    sizes: tuple[Size, ...]
    data: bytes | None = field(default=None, repr=False)
    notes: tuple[str, ...] = ()

    @property
    def page_count(self) -> int:
        return len(self.sizes)


# --- reading --------------------------------------------------------------------------------


def load_source(path: Path, format_id: str | None) -> Source:
    """Read the page sizes of a PDF, or make a picture into PDF pages (off the GUI thread)."""
    path = Path(path)
    if format_id == "pdf":
        doc = _open_pdf(path)
        try:
            sizes = tuple((page.rect.width, page.rect.height) for page in doc)
        finally:
            doc.close()
        return Source(path, "pdf", sizes)
    if format_id in PICTURE_FORMATS:
        data, notes = picture_pdf(path, format_id)
        import pymupdf

        doc = pymupdf.open("pdf", data)
        try:
            sizes = tuple((page.rect.width, page.rect.height) for page in doc)
        finally:
            doc.close()
        return Source(path, format_id, sizes, data, tuple(notes))
    raise JobError(f"{path.name}: {NOT_ADDABLE}")


def picture_pdf(path: Path, format_id: str) -> tuple[bytes, list[str]]:
    """A picture as PDF pages, exactly as the Image → PDF conversion makes them."""
    import pymupdf

    from quince.backends import imaging

    if format_id == "svg":
        with tempfile.TemporaryDirectory(prefix="quince-svg-") as tmp:
            out = Path(tmp) / "picture.pdf"
            imaging.svg_to_pdf(path, out)
            return out.read_bytes(), []
    im = imaging.open_image(path, format_id)
    doc = pymupdf.open()
    try:
        notes = imaging.add_pdf_pages(None, doc, path, im, format_id)
        return doc.tobytes(deflate=True), notes
    except (RuntimeError, ValueError) as exc:
        raise JobError(f"{path.name}: cannot make a page of it: {exc}", str(exc)) from exc
    finally:
        doc.close()
        im.close()


def _open_pdf(path: Path):
    from quince.backends import pdf

    try:
        return pdf.open_pdf(path)
    except JobError as exc:
        raise JobError(f"{path.name}: {exc.reason}", exc.details) from exc


def open_source(source: Source):
    """The source as a PyMuPDF document (the caller closes it)."""
    import pymupdf

    if source.data is not None:
        return pymupdf.open("pdf", source.data)
    doc = _open_pdf(source.path)
    if doc.page_count != source.page_count:
        doc.close()
        raise JobError(f"{source.path.name} changed after it was opened; open it again")
    return doc


# --- the page list --------------------------------------------------------------------------


def first_pages(sources: Sequence[Source]) -> tuple[Page, ...]:
    """Every page of every source, in order; uids 0, 1, 2, ..."""
    pages: list[Page] = []
    for s, source in enumerate(sources):
        pages += [Page(len(pages) + n, s, n) for n in range(source.page_count)]
    return tuple(pages)


def source_pages(source_index: int, source: Source, uids: Iterator[int]) -> tuple[Page, ...]:
    return tuple(Page(next(uids), source_index, n) for n in range(source.page_count))


def shown_size(page: Page, sources: Sequence[Source]) -> Size:
    w, h = page.blank_size if page.blank else sources[page.source].sizes[page.number]
    return (h, w) if page.turns % 2 else (w, h)


def without(pages: Sequence[Page], uids: Iterable[int]) -> tuple[Page, ...]:
    gone = set(uids)
    return tuple(p for p in pages if p.uid not in gone)


def moved(pages: Sequence[Page], uids: Iterable[int], index: int) -> tuple[Page, ...]:
    """The pages ``uids`` (kept in their order) moved to ``index``, a place between the
    pages of the current list (0 … len)."""
    chosen = set(uids)
    index = max(0, min(index, len(pages)))
    picked = [p for p in pages if p.uid in chosen]
    rest = [p for p in pages if p.uid not in chosen]
    at = index - sum(1 for p in pages[:index] if p.uid in chosen)
    return tuple(rest[:at] + picked + rest[at:])


def shifted(pages: Sequence[Page], uids: Iterable[int], step: int) -> tuple[Page, ...]:
    """Every page of ``uids`` one place earlier (``step`` -1) or later (+1); a page that
    meets the start, the end or another chosen page that cannot move stays."""
    chosen = set(uids)
    out = list(pages)
    order = range(len(out)) if step < 0 else range(len(out) - 1, -1, -1)
    for i in order:
        j = i + step
        if out[i].uid in chosen and 0 <= j < len(out) and out[j].uid not in chosen:
            out[i], out[j] = out[j], out[i]
    return tuple(out)


def rotated(pages: Sequence[Page], uids: Iterable[int], turns: int) -> tuple[Page, ...]:
    chosen = set(uids)
    return tuple(replace(p, turns=(p.turns + turns) % 4) if p.uid in chosen else p for p in pages)


def duplicated(
    pages: Sequence[Page], uids: Iterable[int], new_uids: Iterator[int]
) -> tuple[tuple[Page, ...], tuple[int, ...]]:
    """A copy of every page of ``uids`` right after it; the pages and the copies' uids."""
    chosen = set(uids)
    out: list[Page] = []
    copies: list[int] = []
    for page in pages:
        out.append(page)
        if page.uid in chosen:
            copy = replace(page, uid=next(new_uids))
            out.append(copy)
            copies.append(copy.uid)
    return tuple(out), tuple(copies)


def inserted(pages: Sequence[Page], index: int, new: Sequence[Page]) -> tuple[Page, ...]:
    index = max(0, min(index, len(pages)))
    return (*pages[:index], *new, *pages[index:])


def blank_size_near(pages: Sequence[Page], sources: Sequence[Source], index: int) -> Size:
    """The size for a blank page put at ``index``: that of the page before it (as shown),
    else the one after it, else A4."""
    for i in (index - 1, index):
        if 0 <= i < len(pages):
            return shown_size(pages[i], sources)
    return A4


# --- splitting ------------------------------------------------------------------------------

_RANGE = re.compile(r"^(\d*)\s*[-–]\s*(\d*)$")


def parse_ranges(text: str, count: int) -> list[tuple[int, ...]]:
    """``"1-3, 5, 8-"`` → one part per range (page indices, 0-based). Pages count from 1 in
    the current order; ``8-`` runs to the end, ``-3`` from the start. ``ValueError`` with a
    short reason for a wrong range."""
    parts: list[tuple[int, ...]] = []
    for raw in re.split(r"[,;]", text):
        token = raw.strip()
        if not token:
            continue
        if token.isdigit():
            first = last = int(token)
        elif (match := _RANGE.match(token)) and (match[1] or match[2]):
            first = int(match[1]) if match[1] else 1
            last = int(match[2]) if match[2] else count
        else:
            raise ValueError(f"“{token}” is not a page or a range")
        for n in (first, last):
            if not 1 <= n <= count:
                raise ValueError(f"There is no page {n} ({count} pages)")
        if first > last:
            raise ValueError(f"{token}: the first page comes after the last")
        parts.append(tuple(range(first - 1, last)))
    return parts


def parts_every(count: int, size: int) -> list[tuple[int, ...]]:
    size = max(1, size)
    return [tuple(range(start, min(start + size, count))) for start in range(0, count, size)]


def parts_at(count: int, starts: Iterable[int]) -> list[tuple[int, ...]]:
    """Parts that begin at each index of ``starts`` (and at the first page)."""
    if count <= 0:
        return []
    cuts = sorted({0, *(s for s in starts if 0 < s < count)})
    ends = [*cuts[1:], count]
    return [tuple(range(a, b)) for a, b in zip(cuts, ends, strict=True)]


def kept_levels(entries: Sequence[tuple[int, bool]]) -> list[int]:
    """New levels for the kept bookmarks of an outline (level, kept): a bookmark's level is
    one more than the number of its kept parents, so one under a removed bookmark moves up
    a level and the outline stays valid."""
    out: list[int] = []
    parents: list[tuple[int, bool]] = []
    for level, keep in entries:
        while parents and parents[-1][0] >= level:
            parents.pop()
        if keep:
            out.append(1 + sum(1 for _level, kept in parents if kept))
        parents.append((level, keep))
    return out


# --- writing --------------------------------------------------------------------------------


def write(ctx: JobContext, sources: Sequence[Source], pages: Sequence[Page], out: Path) -> None:
    write_parts(ctx, sources, pages, [tuple(range(len(pages)))], [out])


def write_parts(
    ctx: JobContext,
    sources: Sequence[Source],
    pages: Sequence[Page],
    parts: Sequence[Sequence[int]],
    outs: Sequence[Path],
) -> None:
    """Write each part (indices into ``pages``) to its path in ``outs``."""
    import pymupdf

    if not parts or any(not part for part in parts):
        raise JobError("There are no pages to save")
    ctx.progress(0.0, "Reading the pages")
    base, index_of, toc, merged = _combine(ctx, sources, pages)
    try:
        for s in sorted({p.source for p in pages if not p.blank}):
            for note in sources[s].notes:
                ctx.note(f"{sources[s].path.name}: {note}")
        single = len(parts) == 1
        data = None if single else base.tobytes()
        for n, (part, out) in enumerate(zip(parts, outs, strict=True)):
            ctx.check()
            message = "Saving" if single else f"Saving part {n + 1} of {len(parts)}"
            ctx.progress(0.1 + 0.9 * n / len(parts), message)
            doc = base if single else pymupdf.open("pdf", data)
            try:
                chosen = [pages[i] for i in part]
                _arrange(doc, [index_of[p.uid] for p in chosen], [p.turns for p in chosen], toc,
                         merged)  # fmt: skip
                doc.save(str(out), garbage=3, deflate=True)
            finally:
                if doc is not base:
                    doc.close()
    except (RuntimeError, ValueError) as exc:
        raise JobError(f"Cannot write the PDF: {exc}", str(exc)) from exc
    finally:
        base.close()


Bookmark = tuple[int, str, int, dict]  # level, title, page index in the combined document, dest


def _combine(ctx: JobContext, sources: Sequence[Source], pages: Sequence[Page]):
    """One document holding every source page used and the blank pages; where each page is
    in it; the bookmarks of the sources (combined page indices); whether several sources
    were joined."""
    import pymupdf

    used = sorted({p.source for p in pages if not p.blank})
    offsets: dict[int, int] = {}
    toc: list[Bookmark] = []
    if used:
        base = open_source(sources[used[0]])
        offsets[used[0]] = 0
        toc += _bookmarks(base, 0)
        try:
            for s in used[1:]:
                ctx.check()
                other = open_source(sources[s])
                try:
                    offsets[s] = base.page_count
                    toc += _bookmarks(other, base.page_count)
                    base.insert_pdf(other)
                finally:
                    other.close()
        except BaseException:
            base.close()
            raise
    else:
        base = pymupdf.open()
    index_of: dict[int, int] = {}
    blanks: dict[tuple[float, float], int] = {}
    for page in pages:
        if page.blank:
            w, h = page.blank_size
            key = (round(w, 2), round(h, 2))
            if key not in blanks:
                base.new_page(-1, width=w, height=h)
                blanks[key] = base.page_count - 1
            index_of[page.uid] = blanks[key]
        else:
            index_of[page.uid] = offsets[page.source] + page.number
    return base, index_of, toc, len(used) > 1


def _bookmarks(doc, offset: int) -> list[Bookmark]:
    try:
        entries = doc.get_toc(simple=False)
    except (RuntimeError, ValueError) as exc:  # a broken outline; the pages matter more
        log.warning("cannot read the bookmarks: %s", exc)
        return []
    out: list[Bookmark] = []
    for level, title, page, *rest in entries:
        dest = {k: v for k, v in (rest[0] if rest else {}).items() if k != "xref"}
        out.append((level, title, page - 1 + offset if page > 0 else -1, dest))
    return out


def _arrange(doc, indices: list[int], turns: list[int], toc: list[Bookmark], merged: bool):
    import pymupdf

    reordered = indices != list(range(doc.page_count))
    if reordered:
        doc.select(indices)
    if reordered or merged:
        first: dict[int, int] = {}
        for new, old in enumerate(indices):
            first.setdefault(old, new)
        entries: list[tuple[list, bool]] = []
        for level, title, page, dest in toc:
            if page >= 0:
                new = first.get(page)
                entry = [level, title, -1 if new is None else new + 1, {**dest, "page": new}]
                entries.append((entry, new is not None))
            else:  # no target page: kept unless it pointed to a page
                entries.append(([level, title, -1, dest], dest.get("kind") != pymupdf.LINK_GOTO))
        try:
            doc.set_toc(_leveled(entries))
        except (RuntimeError, ValueError) as exc:
            log.warning("cannot keep the bookmarks as they were: %s", exc)
            doc.set_toc(_leveled([(e[:3], keep and e[2] > 0) for e, keep in entries]))
    for n, quarter in enumerate(turns):
        if quarter % 4:
            page = doc[n]
            page.set_rotation((page.rotation + 90 * quarter) % 360)


def _leveled(entries: list[tuple[list, bool]]) -> list[list]:
    kept = [list(entry) for entry, keep in entries if keep]
    for entry, level in zip(kept, kept_levels([(e[0], k) for e, k in entries]), strict=True):
        entry[0] = level
    return kept
