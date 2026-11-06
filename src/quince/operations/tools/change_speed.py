"""Change speed: a faster or slower copy of a video or sound (``name-2x``)."""

from __future__ import annotations

from quince.core.formats import Category
from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import MEDIA_DEPS

OPERATION = Operation(
    id="tool.change_speed",
    kind=Kind.TOOL,
    label="Speed",
    title="Change speed…",
    icon="media-seek-forward",
    accepts=Accepts(categories=frozenset({Category.VIDEO, Category.AUDIO}), max_items=1),
    requires=MEDIA_DEPS,
    order=13,
    editor="media.speed",
)
