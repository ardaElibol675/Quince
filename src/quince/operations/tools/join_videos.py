"""Join videos: two or more videos into one, in the order confirmed in a small dialog.

Videos with the same format, codecs and size are joined without re-encoding; others become
one MP4 at the first video's size (``backends/mediatools.py``).
"""

from __future__ import annotations

from quince.backends import mediatools
from quince.core import formats
from quince.core.formats import Category
from quince.core.jobs import JobContext
from quince.core.operations import Accepts, Host, JobRequest, Kind, Operation
from quince.core.selection import Selection
from quince.operations.tools._common import MEDIA_DEPS, ask_order


def _start(selection: Selection, host: Host) -> JobRequest | None:
    items = ask_order(selection, host, "Join Videos", "Join")
    if items is None:
        return None
    paths = [item.path for item in items]
    format_ids = [item.format_id for item in items]

    def run(ctx: JobContext) -> None:
        ctx.progress(None, "Reading the videos")
        plan = mediatools.plan_join(ctx, paths, format_ids)
        extension = formats.get(plan.out_format).extension
        out = ctx.new_output(paths[0], extension=extension, suffix="joined")
        mediatools.join(ctx, paths, plan, out)

    return JobRequest(run, f"Join {len(paths)} videos")


OPERATION = Operation(
    id="tool.join_videos",
    kind=Kind.TOOL,
    label="Join",
    title="Join videos…",
    icon="video-x-generic",
    accepts=Accepts(categories=frozenset({Category.VIDEO}), min_items=2),
    requires=MEDIA_DEPS,
    order=65,
    start=_start,
)
