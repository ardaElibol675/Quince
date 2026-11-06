"""Crop image: opens the Crop editor; the chosen area is saved as ``name-cropped``."""

from __future__ import annotations

from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import IMAGE_EDITABLE, IMAGE_EDITOR_DEPS

OPERATION = Operation(
    id="tool.crop_image",
    kind=Kind.TOOL,
    label="Crop",
    title="Crop image…",
    icon="transform-crop",
    accepts=Accepts(formats=IMAGE_EDITABLE, max_items=1),
    requires=("pillow",),
    requires_by_format=IMAGE_EDITOR_DEPS,
    order=11,
    editor="image.crop",
)
