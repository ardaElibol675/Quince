"""Edit video: opens the Video Editor (trim, crop, redact, speed, sound, split and frames in
one window); Save asks for the name, format and folder of the new file (``name-edited``)."""

from __future__ import annotations

from quince.core.formats import Category
from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import MEDIA_DEPS

OPERATION = Operation(
    id="tool.edit_video",
    kind=Kind.TOOL,
    label="Edit",
    title="Edit video…",
    icon="quince-edit",
    accepts=Accepts(categories=frozenset({Category.VIDEO}), max_items=1),
    requires=MEDIA_DEPS,
    order=5,
    editor="video.edit",
    suffixes=("edited", "parts", "frame", "frames"),
)
