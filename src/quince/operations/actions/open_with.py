"""Open With: open the selection with an application the user picks.

Applications that open every selected type come first (the default one on top); every other
application is offered under "Other Applications".
"""

from __future__ import annotations

from quince.backends import desktop
from quince.core.jobs import JobError
from quince.core.operations import Accepts, Choice, Host, Kind, Operation
from quince.core.selection import Selection
from quince.operations.actions._common import describe, paths


def mime_types(selection: Selection, db: desktop.AppDatabase) -> list[str]:
    return [desktop.mime_type(i.path, i.format, db.mime) for i in selection.items]


def choices(selection: Selection, db: desktop.AppDatabase) -> tuple[list[Choice], list[Choice]]:
    """The matching applications and all the others."""
    mimes = mime_types(selection, db)
    matching = db.for_types(mimes)
    defaults = {db.default_for(m) for m in mimes}
    if len(defaults) == 1 and (default := defaults.pop()) in matching:
        matching.remove(default)
        matching.insert(0, default)
    ids = {a.id for a in matching}
    others = [a for a in db.shown() if a.id not in ids]
    return (
        [Choice(a.id, a.name, a.icon) for a in matching],
        [Choice(a.id, a.name, a.icon) for a in others],
    )


def _start(selection: Selection, host: Host) -> None:
    db = desktop.AppDatabase.load()
    matching, others = choices(selection, db)
    key = host.choose(
        f"Open {describe(selection)} with",
        matching,
        more=others,
        more_label="Other Applications",
    )
    if key is None:
        return
    app = db.get(key)
    if app is None:
        raise JobError("That application is no longer installed")
    terminal = desktop.find_terminal() if app.terminal else None
    desktop.launch(app, paths(selection), terminal)


OPERATION = Operation(
    id="action.open_with",
    kind=Kind.ACTION,
    label="Open With",
    title="Open With…",
    icon="document-open",
    accepts=Accepts.anything(),
    order=50,
    start=_start,
)
