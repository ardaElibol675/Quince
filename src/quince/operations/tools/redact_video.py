"""Redact video: hides areas of the picture for a stretch of time (``name-redacted``)."""

from __future__ import annotations

from quince.core.formats import Category
from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import MEDIA_DEPS

OPERATION = Operation(
    id="tool.redact_video",
    kind=Kind.TOOL,
    label="Redact",
    title="Redact video…",
    icon="view-private",
    accepts=Accepts(categories=frozenset({Category.VIDEO}), max_items=1),
    requires=MEDIA_DEPS,
    order=16,
    editor="video.redact",
)
