"""Images to PDF: one PDF with a page per image, in the order confirmed in a small dialog."""

from __future__ import annotations

from quince.backends import pdftools
from quince.core.formats import Category
from quince.core.jobs import JobContext
from quince.core.operations import Accepts, Host, JobRequest, Kind, Operation
from quince.core.selection import Selection
from quince.operations.tools._common import ask_order

RASTER = frozenset({"jpg", "png", "webp", "heic", "tiff", "avif", "bmp", "gif"})


def _start(selection: Selection, host: Host) -> JobRequest | None:
    items = ask_order(selection, host, "Images to PDF", "Create PDF")
    if items is None:
        return None
    sources = [(item.path, item.format_id) for item in items]

    def run(ctx: JobContext) -> None:
        out = ctx.new_output(sources[0][0], extension="pdf", suffix="combined")
        pdftools.images_to_pdf(ctx, sources, out)

    return JobRequest(run, f"{len(sources)} images to PDF")


OPERATION = Operation(
    id="tool.images_to_pdf",
    kind=Kind.TOOL,
    label="To One PDF",
    title="Images to one PDF…",
    icon="application-pdf",
    accepts=Accepts(
        formats=RASTER,
        categories=frozenset({Category.IMAGE}),
        min_items=2,
        predicate=lambda s: s.format_ids <= RASTER,
    ),
    requires=("pymupdf",),
    order=70,
    start=_start,
)
