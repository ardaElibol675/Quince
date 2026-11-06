"""Redact audio: covers parts of the sound with a bleep or silence (``name-redacted``)."""

from __future__ import annotations

from quince.core.formats import Category
from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import MEDIA_DEPS

OPERATION = Operation(
    id="tool.redact_audio",
    kind=Kind.TOOL,
    label="Bleep",
    title="Redact audio (bleep / silence)…",
    icon="audio-volume-muted",
    accepts=Accepts(categories=frozenset({Category.AUDIO, Category.VIDEO}), max_items=1),
    requires=MEDIA_DEPS,
    order=18,
    editor="audio.redact",
)
