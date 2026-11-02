"""Frameless, translucent, always-on-top circle that accepts file drops as COPY."""

from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QGuiApplication,
    QPainter,
    QPaintEvent,
)
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

DIAMETER = 220
# A cancel click and the drop it causes arrive on different X connections in either order,
# so a drop is only acted on after this delay.
DROP_SETTLE_MS = 80


def clamp_to_screen(center: QPoint, size: int, screen: QRect) -> QPoint:
    """Top-left position for a ``size`` square centered at ``center``, kept inside ``screen``."""
    half = size // 2
    x = min(max(center.x() - half, screen.left()), screen.right() + 1 - size)
    y = min(max(center.y() - half, screen.top()), screen.bottom() + 1 - size)
    return QPoint(x, y)


def local_paths(event: QDropEvent | QDragEnterEvent) -> list[str]:
    mime = event.mimeData()
    if mime is None or not mime.hasUrls():
        return []
    return [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]


class DebugOverlay(QWidget):
    """Shows the wheel name at the cursor and logs the paths dropped on it."""

    dropped = Signal(str, list)  # wheel name, file paths

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.X11BypassWindowManagerHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.setFixedSize(DIAMETER, DIAMETER)
        self.wheel = ""
        self._hovered = False
        # Paths read at drag enter. Reading them again at drop is a second round trip to the
        # drag source, which blocks the GUI thread (up to Qt's 5 s timeout) if it stalls.
        self._enter_paths: list[str] = []
        # Cancelled overlays stay mapped (but paint nothing) to swallow the drop GTK sends.
        self._cancelled = False
        self._dropped_paths: list[str] = []

    def show_at(self, center: QPoint, wheel: str) -> None:
        self.wheel = wheel
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        self.move(clamp_to_screen(center, DIAMETER, screen.geometry()))
        self._hovered = False
        self._cancelled = False
        self.show()
        self.raise_()
        self.update()
        log.info("overlay '%s' shown at %d,%d on %s", wheel, center.x(), center.y(), screen.name())

    def close_overlay(self, reason: str) -> None:
        if self.isVisible():
            log.info("overlay closed: %s", reason)
            self.hide()
        self._cancelled = False

    def cancel(self, reason: str) -> None:
        """Look closed at once, but keep accepting (and ignoring) the drop that may follow."""
        if self.isVisible() and not self._cancelled:
            log.info("overlay cancelled: %s", reason)
            self._cancelled = True
            self.update()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    # --- drag and drop ------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self._enter_paths = local_paths(event)
        self._hovered = True
        self.update()
        log.info(
            "drag enter: %d item(s), proposed=%s", len(self._enter_paths), event.proposedAction()
        )

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event) -> None:
        self._hovered = False
        self.update()

    def dropEvent(self, event: QDropEvent) -> None:
        # Always COPY: the drag source must never delete or move the original.
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self._dropped_paths = self._enter_paths or local_paths(event)
        self._enter_paths = []
        QTimer.singleShot(DROP_SETTLE_MS, self._finish_drop)

    def _finish_drop(self) -> None:
        paths, self._dropped_paths = self._dropped_paths, []
        if self._cancelled:
            log.info("drop ignored (cancelled): %s", paths)
            self.close_overlay("cancelled")
            return
        log.info("dropped on '%s': %s", self.wheel, paths)
        self.dropped.emit(self.wheel, paths)
        self.close_overlay("drop")

    # --- painting -----------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._cancelled:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill = QColor(40, 120, 220, 230) if self._hovered else QColor(30, 30, 36, 220)
        painter.setBrush(fill)
        painter.setPen(QColor(255, 255, 255, 90))
        painter.drawEllipse(self.rect().adjusted(2, 2, -2, -2))
        painter.setPen(QColor("white"))
        font = QFont(self.font())
        font.setPointSize(16)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.wheel.capitalize())
