"""Edit sound: opens the Sound Editor (trim, speed, channels, bleeps, loudness and turning the
sound into a video in one window); Save asks for the name, format and folder of the new file
(``name-edited``)."""

from __future__ import annotations

from quince.core.formats import Category
from quince.core.operations import Accepts, Kind, Operation
from quince.operations.tools._common import MEDIA_DEPS

OPERATION = Operation(
    id="tool.edit_audio",
    kind=Kind.TOOL,
    label="Edit",
    title="Edit sound…",
    icon="quince-edit",
    accepts=Accepts(categories=frozenset({Category.AUDIO}), max_items=1),
    requires=MEDIA_DEPS,
    order=5,
    editor="audio.edit",
    suffixes=("edited",),
)
