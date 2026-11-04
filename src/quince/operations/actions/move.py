"""Move: move the selected files and folders into a folder the user chooses.

The only operation that changes where the originals are (CLAUDE.md Hard Rule 4).
"""

from __future__ import annotations

from quince.backends import files
from quince.core.operations import Accepts, Host, JobRequest, Kind, Operation
from quince.core.selection import Selection
from quince.operations.actions._common import choose_destination, folder_label, paths


def _start(selection: Selection, host: Host) -> JobRequest | None:
    dest = choose_destination(selection, host, "Move")
    if dest is None:
        return None
    sources = paths(selection)
    return JobRequest(
        lambda ctx: files.move_items(ctx, sources, dest), title=f"Move to {folder_label(dest)}"
    )


OPERATION = Operation(
    id="action.move",
    kind=Kind.ACTION,
    label="Move",
    title="Move to folder…",
    icon="folder-move",
    accepts=Accepts.anything(),
    order=20,
    start=_start,
)
