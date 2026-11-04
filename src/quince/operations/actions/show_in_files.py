"""Show in Files: open a file manager window with the selected items highlighted."""

from __future__ import annotations

from quince.core.operations import Accepts, Host, Kind, Operation
from quince.core.selection import Selection
from quince.operations.actions._common import paths


def _start(selection: Selection, host: Host) -> None:
    host.show_in_files(paths(selection))


OPERATION = Operation(
    id="action.show_in_files",
    kind=Kind.ACTION,
    label="Show",
    title="Show in Files",
    icon="system-file-manager",
    accepts=Accepts.anything(),
    order=40,
    start=_start,
)
