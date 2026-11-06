"""Remove audio: copies of several videos without their sound; every other stream is copied
as is (one video loses its sound in the Video Editor)."""

from __future__ import annotations

from quince.backends import mediatools
from quince.core.formats import Category
from quince.core.jobs import JobContext
from quince.core.operations import Accepts, Kind, Operation
from quince.core.selection import Item
from quince.operations.tools._common import MEDIA_DEPS, each_file


def _one(ctx: JobContext, item: Item) -> None:
    out = ctx.new_output(item.path, suffix="no-audio")
    mediatools.remove_audio(ctx, item.path, item.format_id, out)


OPERATION = Operation(
    id="tool.remove_audio",
    kind=Kind.TOOL,
    label="Remove Audio",
    icon="audio-volume-muted",
    accepts=Accepts(categories=frozenset({Category.VIDEO}), min_items=2),
    requires=MEDIA_DEPS,
    order=30,
    run=lambda ctx: each_file(ctx, _one),
    suffixes=("no-audio",),
)
