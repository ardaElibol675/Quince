"""Helpers shared by the tools (skipped by operation discovery)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from quince.core.forms import natural_order
from quince.core.jobs import JobContext
from quince.core.operations import Host
from quince.core.selection import Item, Selection

MEDIA_DEPS = ("ffmpeg", "ffprobe")


def each_file(ctx: JobContext, work: Callable[[JobContext, Item], None]) -> None:
    """Run ``work`` for every selected file on its own: a failure loses only that file's
    outputs, and the job's progress is shared out between the files."""
    files = ctx.selection.files
    for n, item in enumerate(files):
        ctx.check()
        span = (n / len(files), (n + 1) / len(files))
        with ctx.item(item.path, span):
            ctx.progress(0.0, item.path.name if len(files) > 1 else None)
            work(ctx, item)


def ask_order(selection: Selection, host: Host, title: str, accept: str) -> list[Item] | None:
    """The selected files in the order the user confirms (natural name order first)."""
    by_path = {item.path: item for item in selection.files}
    order = host.ask_order(title, natural_order(list(by_path)), accept_label=accept)
    if order is None:
        return None
    return [by_path[Path(p)] for p in order]


def requires_for(format_ids, deps: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    return {format_id: deps for format_id in format_ids}
