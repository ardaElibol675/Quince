"""A small borderless message near the bottom of the screen under the pointer.

Phase 5 builds the full feedback UI (progress, success and error cards) on top of this.
"""

from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QCursor, QFontMetricsF, QGuiApplication, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from quince.ui.theme.colors import wheel_colors

SHOW_MS = 2600
FADE_MS = 180
BOTTOM_GAP = 72
PADDING_X, PADDING_Y = 18, 11
MAX_WIDTH = 520


class Toast(QWidget):
    def __init__(self, *, scheme: str = "system") -> None:
        super().__init__(
            None,
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.X11BypassWindowManagerHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.scheme = scheme
        self.text = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(FADE_MS)
        self._fade.finished.connect(self._on_fade_finished)

    def show_message(self, text: str, duration_ms: int = SHOW_MS) -> None:
        self.text = text
        metrics = QFontMetricsF(self.font())
        bounds = metrics.boundingRect(
            QRectF(0, 0, MAX_WIDTH - 2 * PADDING_X, 1000), int(Qt.TextFlag.TextWordWrap), text
        )
        width = int(bounds.width() + 2 * PADDING_X) + 1
        height = int(bounds.height() + 2 * PADDING_Y) + 1
        self.setFixedSize(width, height)

        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(area.center().x() - width // 2, area.bottom() - BOTTOM_GAP - height)

        self._fade.stop()
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.update()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._timer.start(duration_ms)

    def _fade_out(self) -> None:
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if self.windowOpacity() <= 0.0:
            self.hide()

    def paintEvent(self, event: QPaintEvent) -> None:
        colors = wheel_colors(self.scheme)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(colors.ring_border, 1.0))
        p.setBrush(colors.ring)
        p.drawRoundedRect(rect, 12, 12)
        p.setPen(colors.text)
        p.drawText(
            rect.adjusted(PADDING_X, PADDING_Y, -PADDING_X, -PADDING_Y),
            int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
            self.text,
        )
        p.end()
