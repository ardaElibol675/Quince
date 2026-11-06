"""Edit PDF: opens the PDF Editor (organize and split pages) for one or more PDFs; Save
asks for the name and folder of the new PDF (``name-edited`` by default)."""

from __future__ import annotations

from quince.core.operations import Accepts, Kind, Operation

OPERATION = Operation(
    id="tool.edit_pdf",
    kind=Kind.TOOL,
    label="Edit",
    title="Edit PDF…",
    icon="quince-edit",
    accepts=Accepts(formats=frozenset({"pdf"})),
    requires=("pymupdf",),
    order=5,
    editor="pdf.edit",
    suffixes=("edited", "pages", "parts"),
)
