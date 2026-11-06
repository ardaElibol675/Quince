"""Merge PDFs: two or more PDFs into one, in the order confirmed in a small dialog.

Text, links and bookmarks are kept; each file also gets a bookmark with its name.
"""

from __future__ import annotations

from quince.backends import pdftools
from quince.core.jobs import JobContext
from quince.core.operations import Accepts, Host, JobRequest, Kind, Operation
from quince.core.selection import Selection
from quince.operations.tools._common import ask_order


def _start(selection: Selection, host: Host) -> JobRequest | None:
    items = ask_order(selection, host, "Merge PDFs", "Merge")
    if items is None:
        return None
    paths = [item.path for item in items]

    def run(ctx: JobContext) -> None:
        out = ctx.new_output(paths[0], extension="pdf", suffix="merged")
        pdftools.merge(ctx, paths, out)

    return JobRequest(run, f"Merge {len(paths)} PDFs")


OPERATION = Operation(
    id="tool.merge_pdfs",
    kind=Kind.TOOL,
    label="Merge PDFs",
    title="Merge PDFs…",
    icon="document-new",
    accepts=Accepts(formats=frozenset({"pdf"}), min_items=2),
    requires=("pymupdf",),
    order=60,
    start=_start,
    suffixes=("merged",),
)
