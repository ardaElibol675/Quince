"""Frame image: opens the Frame editor (padding, background, rounded corners, shadow);
the result is saved as ``name-framed``."""

from __future__ import annotations

from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import IMAGE_EDITABLE, IMAGE_EDITOR_DEPS

OPERATION = Operation(
    id="tool.frame_image",
    kind=Kind.TOOL,
    label="Frame",
    title="Frame image…",
    icon="insert-image",
    accepts=Accepts(formats=IMAGE_EDITABLE, max_items=1),
    requires=("pillow",),
    requires_by_format=IMAGE_EDITOR_DEPS,
    order=15,
    editor="image.frame",
)
