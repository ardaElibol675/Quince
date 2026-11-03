"""Drag and trigger-tap state machine (pure logic, no X11).

The X11 watcher translates raw input into the events below and feeds them to a
``DragTracker``, which returns the signals the UI cares about.

Rules (CLAUDE.md Hard Rule 8):
- A drag is button 1 held, the pointer moved past a threshold, and (by default) a
  file drag-and-drop started, i.e. ``XdndSelection`` got a new owner while the button
  was held.
- A tap is a trigger key pressed and released with no other key or button in between,
  within ``max_tap_ms``, while a drag is in progress at both press and release.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from quince.core.config import DEFAULT_MAX_TAP_MS, DEFAULT_TRIGGERS, validate_triggers

DEFAULT_DRAG_THRESHOLD_PX = 8

# Middle and right click during a drag cancel the wheel. GTK drops on these clicks, so the
# drop that follows must be swallowed and ignored.
CANCEL_BUTTONS = frozenset({2, 3})
# Scroll wheel "buttons" never affect a drag or a tap.
SCROLL_BUTTONS = frozenset({4, 5, 6, 7})

# Keys reported during a drag so the wheel can be driven from the keyboard.
NAV_KEYS = frozenset(
    {"Escape", "Left", "Right", "Up", "Down", "Home", "End", "Return", "KP_Enter", "space", "Tab"}
)


# --- input events -----------------------------------------------------------


@dataclass(frozen=True)
class ButtonPress:
    button: int
    time: int
    x: int
    y: int


@dataclass(frozen=True)
class ButtonRelease:
    button: int
    time: int


@dataclass(frozen=True)
class Motion:
    time: int
    x: int
    y: int


@dataclass(frozen=True)
class KeyPress:
    key: str  # keysym name, or "keycode:<n>" for keys Quince does not track
    time: int


@dataclass(frozen=True)
class KeyRelease:
    key: str
    time: int


@dataclass(frozen=True)
class DndStarted:
    """``XdndSelection`` got a new owner, i.e. some application started a drag-and-drop."""

    time: int


InputEvent = ButtonPress | ButtonRelease | Motion | KeyPress | KeyRelease | DndStarted


# --- output signals ---------------------------------------------------------


@dataclass(frozen=True)
class DragStarted:
    time: int


@dataclass(frozen=True)
class DragEnded:
    time: int


@dataclass(frozen=True)
class TriggerTapped:
    wheel: str
    key: str
    time: int


@dataclass(frozen=True)
class DragCancel:
    """Middle or right click while a drag is in progress."""

    button: int
    time: int


@dataclass(frozen=True)
class DragKey:
    """A navigation key pressed while a drag is in progress."""

    key: str
    time: int


@dataclass(frozen=True)
class DragFiles:
    """What the drag carries, read from ``XdndSelection`` right after the drag started.

    ``drag_time`` matches the ``DragStarted`` of the same drag. ``paths`` is None when the
    data could not be read (the wheel then waits for the drag-enter data), and empty when
    the drag carries no local files (text, web links, ...).
    """

    drag_time: int
    paths: tuple[str, ...] | None


Signal = DragStarted | DragEnded | TriggerTapped | DragKey | DragCancel | DragFiles


class DragTracker:
    def __init__(
        self,
        triggers: Mapping[str, Sequence[str]] = DEFAULT_TRIGGERS,
        *,
        max_tap_ms: int = DEFAULT_MAX_TAP_MS,
        threshold_px: int = DEFAULT_DRAG_THRESHOLD_PX,
        require_dnd: bool = True,
    ) -> None:
        self._wheel_for_key = validate_triggers(triggers)
        self.max_tap_ms = max_tap_ms
        self.threshold_px = threshold_px
        self.require_dnd = require_dnd

        self._button_down = False
        self._press_pos = (0, 0)
        self._moved = False
        self._dnd = False
        self._in_drag = False
        self._keys_down: set[str] = set()
        self._tap: tuple[str, int] | None = None  # (key, press time)

    @property
    def trigger_keys(self) -> frozenset[str]:
        return frozenset(self._wheel_for_key)

    @property
    def in_drag(self) -> bool:
        return self._in_drag

    @property
    def wants_motion(self) -> bool:
        """True while pointer positions are still needed to decide the threshold."""
        return self._button_down and not self._moved

    def feed(self, event: InputEvent) -> list[Signal]:
        if isinstance(event, ButtonPress):
            return self._on_button_press(event)
        if isinstance(event, ButtonRelease):
            return self._on_button_release(event)
        if isinstance(event, Motion):
            return self._on_motion(event)
        if isinstance(event, DndStarted):
            if self._button_down:
                self._dnd = True
                return self._update_drag(event.time)
            return []
        if isinstance(event, KeyPress):
            return self._on_key_press(event)
        return self._on_key_release(event)

    def _update_drag(self, time: int) -> list[Signal]:
        active = self._button_down and self._moved and (self._dnd or not self.require_dnd)
        if active and not self._in_drag:
            self._in_drag = True
            return [DragStarted(time)]
        return []

    def _on_button_press(self, event: ButtonPress) -> list[Signal]:
        if event.button in SCROLL_BUTTONS:
            return []
        self._tap = None
        if event.button != 1:
            if self._in_drag and event.button in CANCEL_BUTTONS:
                return [DragCancel(event.button, event.time)]
            return []
        self._button_down = True
        self._press_pos = (event.x, event.y)
        self._moved = False
        self._dnd = False
        return []

    def _on_button_release(self, event: ButtonRelease) -> list[Signal]:
        if event.button != 1 or not self._button_down:
            return []
        was_dragging = self._in_drag
        self._button_down = self._moved = self._dnd = self._in_drag = False
        self._tap = None
        return [DragEnded(event.time)] if was_dragging else []

    def _on_motion(self, event: Motion) -> list[Signal]:
        if not self.wants_motion:
            return []
        dx = event.x - self._press_pos[0]
        dy = event.y - self._press_pos[1]
        if dx * dx + dy * dy >= self.threshold_px * self.threshold_px:
            self._moved = True
            return self._update_drag(event.time)
        return []

    def _on_key_press(self, event: KeyPress) -> list[Signal]:
        if event.key in self._keys_down:  # auto-repeat
            return []
        others_down = bool(self._keys_down)
        self._keys_down.add(event.key)
        self._tap = None
        if event.key in self._wheel_for_key and not others_down and self._in_drag:
            self._tap = (event.key, event.time)
        if self._in_drag and event.key in NAV_KEYS:
            return [DragKey(event.key, event.time)]
        return []

    def _on_key_release(self, event: KeyRelease) -> list[Signal]:
        self._keys_down.discard(event.key)
        tap, self._tap = self._tap, None
        if tap is None or tap[0] != event.key or not self._in_drag:
            return []
        if event.time - tap[1] > self.max_tap_ms:
            return []
        return [TriggerTapped(self._wheel_for_key[event.key], event.key, event.time)]
