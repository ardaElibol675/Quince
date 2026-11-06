"""Audio to video: an MP4 with a waveform, bars, a spectrum or a still picture."""

from __future__ import annotations

from quince.core.formats import Category
from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import MEDIA_DEPS

OPERATION = Operation(
    id="tool.audio_to_video",
    kind=Kind.TOOL,
    label="To Video",
    title="Audio to video…",
    icon="video-x-generic",
    accepts=Accepts(categories=frozenset({Category.AUDIO}), max_items=1),
    requires=MEDIA_DEPS,
    order=19,
    editor="audio.to_video",
)
