"""The tray icon (an AppIndicator on Ubuntu's GNOME): open Home, the Library or Settings, quit."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

log = logging.getLogger(__name__)


class Tray(QObject):
    def __init__(
        self,
        icon: QIcon,
        *,
        on_home: Callable[[], None],
        on_library: Callable[[], None],
        on_settings: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        super().__init__()
        self.menu = QMenu()
        self.menu.addAction("Open Quince", on_home)
        self.menu.addAction("Library", on_library)
        self.menu.addAction("Settings", on_settings)
        self.menu.addSeparator()
        self.menu.addAction("Quit Quince", on_quit)
        self.icon = QSystemTrayIcon(icon, self)
        self.icon.setToolTip("Quince")
        self.icon.setContextMenu(self.menu)
        self._on_home = on_home
        self.icon.activated.connect(self._on_activated)

    def show(self) -> bool:
        """Show the icon; False when the desktop has no tray (GNOME without AppIndicator)."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.info("no system tray; open Quince from the app menu or with `quince home`")
            return False
        self.icon.show()
        return True

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._on_home()
