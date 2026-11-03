"""Merge PDFs: join two or more PDFs into one, in selection order.

Declared in Phase 2 as an example of a multi-file tool; the tool runs from Phase 12.
"""

from quince.core.operations import Accepts, Kind, Operation

OPERATION = Operation(
    id="tool.merge_pdfs",
    kind=Kind.TOOL,
    label="Merge PDFs",
    icon="document-new",
    accepts=Accepts(formats=frozenset({"pdf"}), min_items=2),
    requires=("pypdf",),
    order=200,
)
