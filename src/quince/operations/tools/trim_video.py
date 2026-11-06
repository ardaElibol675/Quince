"""Trim video: opens the Trim editor; the part between In and Out is saved as
``name-trimmed``."""

from __future__ import annotations

from quince.core.formats import Category
from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import MEDIA_DEPS

OPERATION = Operation(
    id="tool.trim_video",
    kind=Kind.TOOL,
    label="Trim",
    title="Trim video…",
    icon="edit-cut",
    accepts=Accepts(categories=frozenset({Category.VIDEO}), max_items=1),
    requires=MEDIA_DEPS,
    order=11,
    editor="video.trim",
)
