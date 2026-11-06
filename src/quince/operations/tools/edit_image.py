"""Edit image: opens the Edit editor (turn, mirror, resize, brightness, contrast,
saturation); the result is saved as ``name-edited``."""

from __future__ import annotations

from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import IMAGE_EDITABLE, IMAGE_EDITOR_DEPS

OPERATION = Operation(
    id="tool.edit_image",
    kind=Kind.TOOL,
    label="Edit",
    title="Edit image…",
    icon="image-x-generic",
    accepts=Accepts(formats=IMAGE_EDITABLE, max_items=1),
    requires=("pillow",),
    requires_by_format=IMAGE_EDITOR_DEPS,
    order=12,
    editor="image.edit",
)
