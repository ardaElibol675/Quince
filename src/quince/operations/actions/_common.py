"""Helpers shared by the file actions (skipped by operation discovery)."""

from __future__ import annotations

from pathlib import Path

from quince.core import recent
from quince.core.operations import Host
from quince.core.selection import Selection


def paths(selection: Selection) -> list[Path]:
    return [item.path for item in selection.items]


def describe(selection: Selection) -> str:
    """``"photo.jpg"`` for one item, ``"3 items"`` otherwise."""
    if len(selection) == 1:
        return selection.items[0].path.name
    return f"{len(selection)} items"


def choose_destination(selection: Selection, host: Host, verb: str) -> Path | None:
    """Ask for the Copy / Move destination; the recent ones are offered in the sidebar."""
    recents = recent.destinations()
    start = recents[0] if recents else selection.items[0].path.parent
    dest = host.choose_folder(
        f"{verb} {describe(selection)} to",
        accept_label=f"{verb} Here",
        start=start,
        recent=recents,
    )
    if dest is not None:
        recent.remember(dest)
    return dest


def folder_label(folder: Path) -> str:
    return folder.name or str(folder)
