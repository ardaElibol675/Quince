"""Light and dark color sets, following the application's color scheme.

``apply_theme`` sets the scheme of every Quince window (Settings → Appearance); "system"
follows the desktop. Widgets with their own colors listen to ``colorSchemeChanged``.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QStyleHints

SCHEMES = ("system", "light", "dark")


@dataclass(frozen=True)
class WheelColors:
    ring: QColor
    ring_border: QColor
    separator: QColor
    center: QColor
    text: QColor
    text_dim: QColor
    accent: QColor
    accent_text: QColor
    shadow: QColor


LIGHT = WheelColors(
    ring=QColor(250, 249, 246, 245),
    ring_border=QColor(0, 0, 0, 34),
    separator=QColor(0, 0, 0, 22),
    center=QColor(255, 255, 255, 250),
    text=QColor(43, 42, 40),
    text_dim=QColor(122, 118, 111),
    accent=QColor(226, 128, 38),
    accent_text=QColor(255, 255, 255),
    shadow=QColor(0, 0, 0, 70),
)

DARK = WheelColors(
    ring=QColor(42, 42, 46, 245),
    ring_border=QColor(255, 255, 255, 30),
    separator=QColor(255, 255, 255, 22),
    center=QColor(31, 31, 34, 250),
    text=QColor(242, 240, 236),
    text_dim=QColor(166, 162, 155),
    accent=QColor(240, 151, 60),
    accent_text=QColor(27, 27, 29),
    shadow=QColor(0, 0, 0, 120),
)


def system_is_dark() -> bool:
    app = QGuiApplication.instance()
    return app is not None and app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def wheel_colors(scheme: str = "system") -> WheelColors:
    """Colors for ``"light"``, ``"dark"`` or ``"system"`` (the desktop's current scheme)."""
    if scheme not in SCHEMES:
        raise ValueError(f"unknown color scheme {scheme!r}")
    if scheme == "system":
        return DARK if system_is_dark() else LIGHT
    return DARK if scheme == "dark" else LIGHT


def apply_theme(theme: str, hints: QStyleHints | None = None) -> None:
    """Light or dark for every Quince window; ``"system"`` follows the desktop again.

    (The offscreen platform of the tests has no color schemes and ignores this.)
    """
    if theme not in SCHEMES:
        raise ValueError(f"unknown color scheme {theme!r}")
    app = QGuiApplication.instance()
    if hints is None and app is None:
        return
    scheme = {
        "light": Qt.ColorScheme.Light,
        "dark": Qt.ColorScheme.Dark,
        "system": Qt.ColorScheme.Unknown,
    }[theme]
    (hints or app.styleHints()).setColorScheme(scheme)
