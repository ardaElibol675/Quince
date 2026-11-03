"""Showing files in the file manager.

``FileManager1.ShowItems`` over D-Bus opens a window with the files selected (Nautilus,
Nemo, Dolphin, ...). Without it, the containing folder is opened with ``xdg-open``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusPendingCallWatcher
from PySide6.QtGui import QDesktopServices

log = logging.getLogger(__name__)

SERVICE = "org.freedesktop.FileManager1"
OBJECT = "/org/freedesktop/FileManager1"
TIMEOUT_MS = 5000

# Watchers must outlive the call; kept here until their reply arrives.
_pending: set[QDBusPendingCallWatcher] = set()


def open_folder(path: Path) -> bool:
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def show_in_files(paths: Sequence[Path], *, fallback: Callable[[Path], bool] = open_folder) -> None:
    """Select ``paths`` in a file manager window; never blocks the GUI thread."""
    if not paths:
        return
    folder = Path(paths[0]).parent
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        fallback(folder)
        return
    msg = QDBusMessage.createMethodCall(SERVICE, OBJECT, SERVICE, "ShowItems")
    msg.setArguments([[QUrl.fromLocalFile(str(p)).toString() for p in paths], ""])
    watcher = QDBusPendingCallWatcher(bus.asyncCall(msg, TIMEOUT_MS))
    _pending.add(watcher)

    def finished(w: QDBusPendingCallWatcher) -> None:
        _pending.discard(w)
        if w.isError():
            log.info("FileManager1.ShowItems failed (%s); opening the folder", w.error().message())
            fallback(folder)
        w.deleteLater()

    watcher.finished.connect(finished)
