"""Icons that come with Quince, for what the desktop's icon theme has no icon for (Edit,
Crop, Redact, Collage). They are found by name like theme icons: ``QIcon.fromTheme("quince-
crop")`` falls back to ``icons/quince-crop.svg`` once ``install()`` has run."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon

ICON_DIR = Path(__file__).resolve().parent / "icons"
_installed = False


def install() -> None:
    """Add the bundled icons to Qt's fallback search paths (once)."""
    global _installed
    if _installed:
        return
    paths = QIcon.fallbackSearchPaths()
    if str(ICON_DIR) not in paths:
        QIcon.setFallbackSearchPaths([*paths, str(ICON_DIR)])
    _installed = True


def themed(name: str) -> QIcon:
    install()
    return QIcon.fromTheme(name)
