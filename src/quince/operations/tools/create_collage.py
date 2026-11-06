"""Create collage: opens the Collage editor for two or more images; the result is saved as
``name-collage`` (named after the first picture)."""

from __future__ import annotations

from quince.backends.collage import MAX_PICTURES
from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import IMAGE_EDITABLE, IMAGE_EDITOR_DEPS

OPERATION = Operation(
    id="tool.create_collage",
    kind=Kind.TOOL,
    label="Collage",
    title="Create collage…",
    icon="view-grid",
    accepts=Accepts(formats=IMAGE_EDITABLE, min_items=2, max_items=MAX_PICTURES),
    requires=("pillow",),
    requires_by_format=IMAGE_EDITOR_DEPS,
    order=16,
    editor="image.collage",
)
