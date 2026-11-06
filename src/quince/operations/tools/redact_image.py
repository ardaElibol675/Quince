"""Redact image: opens the Redact editor (black box, blur, pixelate); the result is saved
as ``name-redacted`` without EXIF metadata."""

from __future__ import annotations

from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import IMAGE_EDITABLE, IMAGE_EDITOR_DEPS

OPERATION = Operation(
    id="tool.redact_image",
    kind=Kind.TOOL,
    label="Redact",
    title="Redact image…",
    icon="edit-clear",
    accepts=Accepts(formats=IMAGE_EDITABLE, max_items=1),
    requires=("pillow",),
    requires_by_format=IMAGE_EDITOR_DEPS,
    order=14,
    editor="image.redact",
)
