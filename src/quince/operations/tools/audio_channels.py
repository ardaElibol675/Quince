"""Audio channels: mono, stereo, swap or mute the left / right channel."""

from __future__ import annotations

from quince.core.formats import Category
from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import MEDIA_DEPS

OPERATION = Operation(
    id="tool.audio_channels",
    kind=Kind.TOOL,
    label="Channels",
    title="Audio channels…",
    icon="audio-speakers",
    accepts=Accepts(categories=frozenset({Category.AUDIO, Category.VIDEO}), max_items=1),
    requires=MEDIA_DEPS,
    order=17,
    editor="audio.channels",
)
