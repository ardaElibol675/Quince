"""Normalize audio: even loudness (-16 LUFS, EBU R128) for several audio files or videos at
once (one file is normalized in the Video or Sound Editor)."""

from __future__ import annotations

from quince.backends import mediatools
from quince.core.formats import Category
from quince.core.jobs import JobContext
from quince.core.operations import Accepts, Kind, Operation
from quince.core.selection import Item
from quince.operations.tools._common import MEDIA_DEPS, each_file


def _one(ctx: JobContext, item: Item) -> None:
    out = ctx.new_output(item.path, suffix="normalized")
    mediatools.normalize(ctx, item.path, item.format_id, out)


OPERATION = Operation(
    id="tool.normalize_audio",
    kind=Kind.TOOL,
    label="Normalize",
    title="Normalize loudness (-16 LUFS)",
    icon="audio-volume-high",
    accepts=Accepts(categories=frozenset({Category.AUDIO, Category.VIDEO}), min_items=2),
    requires=MEDIA_DEPS,
    order=40,
    run=lambda ctx: each_file(ctx, _one),
)
