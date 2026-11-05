"""Trigger taps outside a drag (pure logic, no X11 or Qt).

The Home window and a wheel opened without a drag (Home, the Nautilus menu, ``quince open``)
get their keys as Qt key events. A tap follows the same rules as during a drag (CLAUDE.md
Hard Rule 8): a trigger key pressed and released with no other key or button in between,
within ``max_tap_ms``. Keys are X keysym names, as in the config.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from quince.core.config import DEFAULT_MAX_TAP_MS, DEFAULT_TRIGGERS, validate_triggers


class TapDetector:
    def __init__(
        self,
        triggers: Mapping[str, Sequence[str]] = DEFAULT_TRIGGERS,
        *,
        max_tap_ms: int = DEFAULT_MAX_TAP_MS,
    ) -> None:
        self._wheel_for_key = validate_triggers(triggers)
        self.max_tap_ms = max_tap_ms
        self._down: set[str] = set()
        self._tap: tuple[str, int] | None = None  # (key, press time)

    @property
    def trigger_keys(self) -> frozenset[str]:
        return frozenset(self._wheel_for_key)

    def press(self, key: str, time_ms: int) -> None:
        if key in self._down:  # auto-repeat
            return
        others = bool(self._down)
        self._down.add(key)
        self._tap = (key, time_ms) if key in self._wheel_for_key and not others else None

    def release(self, key: str, time_ms: int) -> str | None:
        """The wheel tapped by this release, or None."""
        self._down.discard(key)
        tap, self._tap = self._tap, None
        if tap is None or tap[0] != key or time_ms - tap[1] > self.max_tap_ms:
            return None
        return self._wheel_for_key[key]

    def button(self) -> None:
        """A mouse button was pressed: no tap (Shift+click or Ctrl+click selects files)."""
        self._tap = None

    def reset(self) -> None:
        """Focus was lost; keys released elsewhere are never seen."""
        self._down.clear()
        self._tap = None
