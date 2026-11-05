"""Extract archive: unpack next to the archive, with the same safety checks as conversions.

A single top-level folder or file lands directly next to the archive; several go into a
folder named after it.
"""

from __future__ import annotations

from quince.backends import archive
from quince.core.formats import Category
from quince.core.jobs import JobContext
from quince.core.operations import Accepts, Kind, Operation
from quince.core.selection import Item
from quince.operations.tools._common import each_file


def _one(ctx: JobContext, item: Item) -> None:
    archive.extract(ctx, item.path, item.format_id)


OPERATION = Operation(
    id="tool.extract_archive",
    kind=Kind.TOOL,
    label="Extract",
    title="Extract here",
    icon="folder-open",
    accepts=Accepts(categories=frozenset({Category.ARCHIVE})),
    requires_by_format={"7z": ("7z",), "rar": ("unrar",)},
    order=50,
    run=lambda ctx: each_file(ctx, _one),
)
