"""Trim audio: opens the Trim editor with the waveform (``name-trimmed``)."""

from __future__ import annotations

from quince.core.formats import Category
from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import MEDIA_DEPS

OPERATION = Operation(
    id="tool.trim_audio",
    kind=Kind.TOOL,
    label="Trim",
    title="Trim audio…",
    icon="edit-cut",
    accepts=Accepts(categories=frozenset({Category.AUDIO}), max_items=1),
    requires=MEDIA_DEPS,
    order=11,
    editor="audio.trim",
)
