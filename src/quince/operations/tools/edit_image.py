"""Edit image: opens the Image Editor (crop, adjust, redact, annotate, frame in one window);
Save asks for the name, format and folder of the new file (``name-edited`` by default)."""

from __future__ import annotations

from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import IMAGE_EDITABLE, IMAGE_EDITOR_DEPS

OPERATION = Operation(
    id="tool.edit_image",
    kind=Kind.TOOL,
    label="Edit",
    title="Edit image…",
    icon="quince-edit",
    accepts=Accepts(formats=IMAGE_EDITABLE, max_items=1),
    requires=("pillow",),
    requires_by_format=IMAGE_EDITOR_DEPS,
    order=5,
    editor="image.edit",
)
