"""Float: put the selection on the Float shelf, to drag it out somewhere else later."""

from __future__ import annotations

from quince.core.operations import Accepts, Host, Kind, Operation
from quince.core.selection import Selection
from quince.operations.actions._common import paths


def _start(selection: Selection, host: Host) -> None:
    host.float_items(paths(selection))


OPERATION = Operation(
    id="action.float",
    kind=Kind.ACTION,
    label="Float",
    title="Float on the shelf",
    icon="folder-drag-accept",
    accepts=Accepts.anything(),
    order=70,
    start=_start,
)
