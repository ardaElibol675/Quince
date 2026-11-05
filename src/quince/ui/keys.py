"""X keysym names for Qt key events, so Qt keys match the trigger names in the config."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from Xlib import XK

XK.load_keysym_group("xkb")  # ISO_Level3_Shift (AltGr)

_NAMES: dict[int, str] = {}
for _name, _value in vars(XK).items():
    if _name.startswith("XK_") and isinstance(_value, int):
        _NAMES.setdefault(_value, _name[3:])

# Without a native keysym (the offscreen platform in tests), the left-hand keys.
_QT_FALLBACK = {
    Qt.Key.Key_Shift: "Shift_L",
    Qt.Key.Key_Control: "Control_L",
    Qt.Key.Key_Alt: "Alt_L",
    Qt.Key.Key_AltGr: "ISO_Level3_Shift",
    Qt.Key.Key_Meta: "Super_L",
    Qt.Key.Key_Super_L: "Super_L",
    Qt.Key.Key_Super_R: "Super_R",
}


def keysym_name(event: QKeyEvent) -> str:
    """The X keysym name of the key, e.g. ``"Shift_R"``; ``"qt:<code>"`` when unknown."""
    sym = event.nativeVirtualKey()
    if sym and sym in _NAMES:
        return _NAMES[sym]
    key = Qt.Key(event.key())
    return _QT_FALLBACK.get(key, f"qt:{int(event.key())}")
