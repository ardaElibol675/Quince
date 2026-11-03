"""What a wheel shows: its name and items, built from registry operations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from quince.core.config import Config
from quince.core.deps import Availability
from quince.core.operations import Kind, Operation
from quince.core.registry import Registry
from quince.core.selection import Selection

WHEEL_TITLES = {"convert": "Convert", "tools": "Tools", "actions": "Actions"}
DEFAULT_EMPTY_MESSAGE = "Nothing available"
PENDING_MESSAGE = "Reading files…"


@dataclass(frozen=True)
class WheelItem:
    id: str
    label: str  # short text on the slice
    title: str  # full name in the center
    icon: str  # freedesktop icon name


@dataclass(frozen=True)
class WheelContent:
    wheel: str  # "convert" | "tools" | "actions"
    items: tuple[WheelItem, ...] = ()
    empty_message: str = DEFAULT_EMPTY_MESSAGE

    @property
    def title(self) -> str:
        return WHEEL_TITLES.get(self.wheel, self.wheel.capitalize())


def item_from_operation(op: Operation) -> WheelItem:
    return WheelItem(op.id, op.label, op.title, op.icon)


def content_from_operations(
    wheel: str, ops: Iterable[Operation], empty_message: str = DEFAULT_EMPTY_MESSAGE
) -> WheelContent:
    """Wheel contents for the operations ``Registry.query`` returned, in that order."""
    return WheelContent(wheel, tuple(map(item_from_operation, ops)), empty_message)


def empty_message(selection: Selection, kind: Kind) -> str:
    """Why a wheel of ``kind`` would be empty for ``selection``."""
    if kind is not Kind.ACTION and not selection.is_uniform:
        if selection.has_folders or selection.is_mixed:
            return "Only file actions for folders and mixed files"
        if selection.files:
            return "Unknown file type"
    return DEFAULT_EMPTY_MESSAGE


def build_contents(
    selection: Selection, registry: Registry, available: Availability, config: Config
) -> Mapping[str, WheelContent]:
    """The contents of every wheel for ``selection``, keyed by wheel name."""
    contents = {}
    for wheel in WHEEL_TITLES:
        kind = Kind.from_wheel(wheel)
        ops = registry.query(selection, kind, available=available, prefs=config.wheel(wheel))
        contents[wheel] = content_from_operations(wheel, ops, empty_message(selection, kind))
    return contents


def pending_content(wheel: str) -> WheelContent:
    """Shown while the dragged files are not known yet."""
    return WheelContent(wheel, (), PENDING_MESSAGE)
