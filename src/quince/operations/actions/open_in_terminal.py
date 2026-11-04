"""Open in Terminal: the user's terminal in the selected folder (or the file's folder).

One window per distinct folder, at most ``MAX_WINDOWS``.
"""

from __future__ import annotations

from pathlib import Path

from quince.backends import desktop
from quince.core.jobs import JobError
from quince.core.operations import Accepts, Host, Kind, Operation
from quince.core.selection import Selection

MAX_WINDOWS = 5


def folders(selection: Selection) -> list[Path]:
    return list(dict.fromkeys(i.path if i.is_dir else i.path.parent for i in selection.items))


def _start(selection: Selection, host: Host) -> None:
    terminal = desktop.find_terminal()
    if terminal is None:
        raise JobError("No terminal program found")
    targets = folders(selection)
    for folder in targets[:MAX_WINDOWS]:
        terminal.open_in(folder)
    if len(targets) > MAX_WINDOWS:
        host.notify(f"Opened the first {MAX_WINDOWS} of {len(targets)} folders")


OPERATION = Operation(
    id="action.open_in_terminal",
    kind=Kind.ACTION,
    label="Terminal",
    title="Open in Terminal",
    icon="utilities-terminal",
    accepts=Accepts.anything(),
    order=60,
    start=_start,
)
