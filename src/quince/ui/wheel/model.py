"""What a wheel shows: its name and items, built from registry operations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from quince.core.operations import Operation

WHEEL_TITLES = {"convert": "Convert", "tools": "Tools", "actions": "Actions"}
DEFAULT_EMPTY_MESSAGE = "Nothing available"


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
