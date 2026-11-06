"""Crop video: opens the Crop editor; the chosen area is saved as ``name-cropped``."""

from __future__ import annotations

from quince.core.formats import Category
from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import MEDIA_DEPS

OPERATION = Operation(
    id="tool.crop_video",
    kind=Kind.TOOL,
    label="Crop",
    title="Crop video…",
    icon="transform-crop",
    accepts=Accepts(categories=frozenset({Category.VIDEO}), max_items=1),
    requires=MEDIA_DEPS,
    order=12,
    editor="video.crop",
)
