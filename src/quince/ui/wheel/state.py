"""Which slice is highlighted, driven by the pointer or by the keyboard (pure logic, no Qt).

Pointer and keyboard are mutually exclusive ways to choose (Decision Log, 2026-11-02). After
an arrow key the wheel is in keyboard mode and ignores the pointer, because GTK nudges the
pointer by 1 px per arrow key during a drag. Moving the pointer clearly away
(``KEYBOARD_EXIT_PX``) hands control back to the pointer.

Keys are X keysym names, so the same code serves Qt key events and raw X11 key events.
"""

from __future__ import annotations

import math
from enum import Enum

KEYBOARD_EXIT_PX = 24.0

PREVIOUS_KEYS = frozenset({"Left", "Up"})
NEXT_KEYS = frozenset({"Right", "Down"})
CHOOSE_KEYS = frozenset({"Return", "KP_Enter", "space"})
CLOSE_KEYS = frozenset({"Escape", "Tab"})


class Mode(Enum):
    POINTER = "pointer"
    KEYBOARD = "keyboard"


class KeyResult(Enum):
    IGNORED = "ignored"
    MOVED = "moved"
    CHOOSE = "choose"
    CLOSE = "close"


class WheelState:
    def __init__(self, count: int = 0) -> None:
        self.reset(count)

    def reset(self, count: int) -> None:
        """New contents (wheel opened or switched): nothing highlighted, pointer mode."""
        self.count = count
        self.highlighted: int | None = None
        self.mode = Mode.POINTER
        self._last_pointer: tuple[float, float] | None = None
        self._anchor: tuple[float, float] | None = None

    def pointer(self, pos: tuple[float, float], index: int | None) -> bool:
        """The pointer is at ``pos`` over slice ``index`` (None: center or outside).

        Returns True when the highlight changed.
        """
        self._last_pointer = pos
        if self.mode is Mode.KEYBOARD:
            if self._anchor is None:
                self._anchor = pos
            if math.dist(pos, self._anchor) < KEYBOARD_EXIT_PX:
                return False
            self.mode = Mode.POINTER
            self._anchor = None
        changed = index != self.highlighted
        self.highlighted = index
        return changed

    def pointer_gone(self) -> bool:
        """The pointer left the wheel. Keyboard mode keeps its highlight."""
        if self.mode is Mode.KEYBOARD or self.highlighted is None:
            return False
        self.highlighted = None
        return True

    def key(self, key: str) -> KeyResult:
        if key in CLOSE_KEYS:
            return KeyResult.CLOSE
        if key in CHOOSE_KEYS:
            return KeyResult.CHOOSE if self.highlighted is not None else KeyResult.IGNORED
        if self.count == 0:
            return KeyResult.IGNORED

        current = self.highlighted
        if key in NEXT_KEYS:
            target = 0 if current is None else (current + 1) % self.count
        elif key in PREVIOUS_KEYS:
            target = self.count - 1 if current is None else (current - 1) % self.count
        elif key == "Home":
            target = 0
        elif key == "End":
            target = self.count - 1
        else:
            return KeyResult.IGNORED

        if self.mode is Mode.POINTER:
            self.mode = Mode.KEYBOARD
            self._anchor = self._last_pointer
        self.highlighted = target
        return KeyResult.MOVED
