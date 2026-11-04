"""Copy Path: put the selected paths on the clipboard, one per line."""

from __future__ import annotations

from quince.core.operations import Accepts, Host, Kind, Operation
from quince.core.selection import Selection
from quince.operations.actions._common import paths


def clipboard_text(selection: Selection) -> str:
    return "\n".join(str(p) for p in paths(selection))


def _start(selection: Selection, host: Host) -> None:
    host.set_clipboard_text(clipboard_text(selection))
    count = len(selection)
    host.notify("Path copied" if count == 1 else f"{count} paths copied")


OPERATION = Operation(
    id="action.copy_path",
    kind=Kind.ACTION,
    label="Copy Path",
    icon="insert-link",
    accepts=Accepts.anything(),
    order=30,
    start=_start,
)
