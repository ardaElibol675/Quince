"""Video snapshots: saves chosen frames of a video as pictures."""

from __future__ import annotations

from quince.core.formats import Category
from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import MEDIA_DEPS

OPERATION = Operation(
    id="tool.video_snapshots",
    kind=Kind.TOOL,
    label="Snapshots",
    title="Video snapshots…",
    icon="camera-photo",
    accepts=Accepts(categories=frozenset({Category.VIDEO}), max_items=1),
    requires=MEDIA_DEPS,
    order=15,
    editor="video.snapshots",
)
