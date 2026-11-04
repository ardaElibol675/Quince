"""Copy: copy the selected files and folders into a folder the user chooses."""

from __future__ import annotations

from quince.backends import files
from quince.core.operations import Accepts, Host, JobRequest, Kind, Operation
from quince.core.selection import Selection
from quince.operations.actions._common import choose_destination, folder_label, paths


def _start(selection: Selection, host: Host) -> JobRequest | None:
    dest = choose_destination(selection, host, "Copy")
    if dest is None:
        return None
    sources = paths(selection)
    return JobRequest(
        lambda ctx: files.copy_items(ctx, sources, dest), title=f"Copy to {folder_label(dest)}"
    )


OPERATION = Operation(
    id="action.copy",
    kind=Kind.ACTION,
    label="Copy",
    title="Copy to folder…",
    icon="edit-copy",
    accepts=Accepts.anything(),
    order=10,
    start=_start,
)
