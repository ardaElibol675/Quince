"""Split video: cuts a video into parts at markers, into equal parts or by length."""

from __future__ import annotations

from quince.core.formats import Category
from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import MEDIA_DEPS

OPERATION = Operation(
    id="tool.split_video",
    kind=Kind.TOOL,
    label="Split",
    title="Split video…",
    icon="edit-cut",
    accepts=Accepts(categories=frozenset({Category.VIDEO}), max_items=1),
    requires=MEDIA_DEPS,
    order=14,
    editor="video.split",
)
