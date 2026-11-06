"""Annotate image: opens the Annotate editor (pen, highlighter, lines, arrows, boxes,
ellipses, text); the result is saved as ``name-annotated``."""

from __future__ import annotations

from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import IMAGE_EDITABLE, IMAGE_EDITOR_DEPS

OPERATION = Operation(
    id="tool.annotate_image",
    kind=Kind.TOOL,
    label="Annotate",
    title="Annotate image…",
    icon="draw-freehand",
    accepts=Accepts(formats=IMAGE_EDITABLE, max_items=1),
    requires=("pillow",),
    requires_by_format=IMAGE_EDITOR_DEPS,
    order=13,
    editor="image.annotate",
)
