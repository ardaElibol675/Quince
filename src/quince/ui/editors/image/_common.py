"""What the image editors share."""

from __future__ import annotations

from quince.backends import imageedit
from quince.core.jobs import JobError
from quince.core.selection import Item, Selection

ANIMATION_HINT = "every frame of the animation is edited"


def single_file(selection: Selection) -> Item:
    files = selection.files
    if len(files) != 1 or files[0].format_id not in imageedit.EDITABLE:
        raise JobError("This editor works on one image")
    return files[0]


def size_text(size: tuple[float, float]) -> str:
    return f"{round(size[0])} × {round(size[1])}"
