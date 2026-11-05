"""PDF tools with PyMuPDF: Merge PDFs and Images to PDF.

Merge keeps text, links and each file's bookmarks; every file also gets a top-level bookmark
named after it, so the parts stay easy to find. Images to PDF builds one page per image
(every page of a multi-page TIFF) exactly like the Image → PDF conversion: a JPEG is
embedded unchanged and the page size follows the image's DPI.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from quince.backends import imaging
from quince.core.jobs import JobContext, JobError

log = logging.getLogger(__name__)


def _open(path: Path):
    from quince.backends import pdf

    try:
        return pdf.open_pdf(path)
    except JobError as exc:
        raise JobError(f"{path.name}: {exc.reason}", exc.details) from exc


def merge(ctx: JobContext, paths: Sequence[Path], out: Path) -> None:
    import pymupdf

    merged = pymupdf.open()
    toc: list[list] = []
    try:
        for n, path in enumerate(paths):
            ctx.check()
            ctx.progress(n / len(paths), f"Adding {path.name}")
            doc = _open(path)
            try:
                offset = merged.page_count
                merged.insert_pdf(doc)
                toc.append([1, path.stem, offset + 1])
                for level, title, page, *_rest in doc.get_toc(simple=True):
                    toc.append([level + 1, title, page + offset if page > 0 else -1])
            finally:
                doc.close()
        try:
            merged.set_toc(toc)
        except ValueError as exc:  # a broken outline in a source; the pages matter more
            log.warning("cannot keep the bookmarks: %s", exc)
            merged.set_toc([entry for entry in toc if entry[0] == 1])
        ctx.progress(0.95, "Saving")
        merged.save(str(out), garbage=3, deflate=True)
    except RuntimeError as exc:
        raise JobError(f"Cannot merge the PDFs: {exc}", str(exc)) from exc
    finally:
        merged.close()


def images_to_pdf(ctx: JobContext, sources: Sequence[tuple[Path, str]], out: Path) -> None:
    """``sources``: (path, format id) pairs, in page order."""
    import pymupdf

    doc = pymupdf.open()
    try:
        for n, (path, format_id) in enumerate(sources):
            ctx.progress(n / len(sources), f"Adding {path.name}")
            im = imaging.open_image(path, format_id)
            try:
                for note in imaging.add_pdf_pages(ctx, doc, path, im, format_id):
                    ctx.note(f"{path.name}: {note}")
            finally:
                im.close()
        ctx.progress(0.95, "Saving")
        doc.save(str(out), garbage=3, deflate=True)
    except (RuntimeError, ValueError) as exc:
        raise JobError(f"Cannot write the PDF: {exc}", str(exc)) from exc
    finally:
        doc.close()
