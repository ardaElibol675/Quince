"""The timeline of the media editors: the playhead, in / out points, markers, cut lines,
time spans (redactions) and, for sound, the waveform (one lane per channel when given).

A click or drag moves the playhead (``seek``); ``pressed`` also tells the editor where the
click landed, so it can pick a span there.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from quince.ui.editors.media.marks import Marks
from quince.ui.theme.colors import wheel_colors

MARGIN = 10
SPAN_COLOR = QColor(220, 50, 47)
CUT_COLOR = QColor(255, 255, 255, 200)
SHADE = QColor(0, 0, 0, 110)
MAX_GAIN = 8.0  # the waveform is scaled to its loudest peak, at most this much


class Timeline(QWidget):
    seek = Signal(float)
    pressed = Signal(float)

    def __init__(self, duration: float, *, tall: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.duration = max(1e-3, duration)
        self.position = 0.0
        self.marks = Marks()
        self.lanes: tuple[tuple[float, ...], ...] = ()
        self.lane_labels: tuple[str, ...] = ()
        self.spans: tuple[tuple[float, float], ...] = ()
        self.selected_span: int | None = None
        self.cuts: tuple[float, ...] = ()
        self.tall = tall
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumHeight(140 if tall else 48)
        vertical = QSizePolicy.Policy.Expanding if tall else QSizePolicy.Policy.Fixed
        self.setSizePolicy(QSizePolicy.Policy.Expanding, vertical)
        if not tall:
            self.setFixedHeight(56)
        self._dragging = False
        self._lane_cache: tuple[tuple, QPixmap] | None = None

    def sizeHint(self) -> QSize:
        return QSize(900, 320 if self.tall else 56)

    # --- what is shown ------------------------------------------------------------------

    def set_position(self, t: float) -> None:
        self.position = t
        self.update()

    def set_marks(self, marks: Marks) -> None:
        self.marks = marks
        self.update()

    def set_lanes(self, lanes: Sequence[Sequence[float]], labels: Sequence[str] = ()) -> None:
        lanes = [tuple(lane) for lane in lanes]
        loudest = max((max(lane, default=0.0) for lane in lanes), default=0.0)
        gain = min(MAX_GAIN, 1.0 / loudest) if loudest > 0 else 1.0  # quiet sound still shows
        self.lanes = tuple(tuple(min(1.0, v * gain) for v in lane) for lane in lanes)
        self.lane_labels = tuple(labels)
        self._lane_cache = None
        self.update()

    def set_spans(self, spans: Sequence[tuple[float, float]], selected: int | None = None) -> None:
        self.spans = tuple(spans)
        self.selected_span = selected
        self.update()

    def set_cuts(self, cuts: Sequence[float]) -> None:
        self.cuts = tuple(cuts)
        self.update()

    # --- coordinates --------------------------------------------------------------------

    def area(self) -> QRectF:
        return QRectF(MARGIN, 6, max(1.0, self.width() - 2 * MARGIN), max(1.0, self.height() - 12))

    def x_of(self, t: float) -> float:
        area = self.area()
        return area.left() + area.width() * max(0.0, min(1.0, t / self.duration))

    def time_at(self, x: float) -> float:
        area = self.area()
        return max(0.0, min(self.duration, (x - area.left()) / area.width() * self.duration))

    # --- painting -----------------------------------------------------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colors = wheel_colors()
        area = self.area()
        base = self.palette().color(QPalette.ColorRole.Base)
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Mid), 1))
        painter.setBrush(base.darker(108))
        painter.drawRoundedRect(area, 6, 6)
        painter.save()
        painter.setClipRect(area)
        self._paint_lanes(painter, area, colors.accent)
        self._paint_spans(painter, area)
        self._paint_range(painter, area, colors.accent)
        self._paint_cuts(painter, area)
        self._paint_markers(painter, area, colors.accent)
        painter.restore()
        self._paint_playhead(painter, area)

    def _paint_lanes(self, painter: QPainter, area: QRectF, color: QColor) -> None:
        """The waveform, drawn once per size into a cached picture."""
        if not self.lanes:
            return
        key = (round(area.width()), round(area.height()), color.rgba())
        if self._lane_cache is None or self._lane_cache[0] != key:
            pixmap = QPixmap(max(1, key[0]), max(1, key[1]))
            pixmap.fill(Qt.GlobalColor.transparent)
            lane_painter = QPainter(pixmap)
            self._draw_lanes(lane_painter, QRectF(0, 0, key[0], key[1]), color)
            lane_painter.end()
            self._lane_cache = (key, pixmap)
        painter.drawPixmap(area.topLeft(), self._lane_cache[1])

    def _draw_lanes(self, painter: QPainter, area: QRectF, color: QColor) -> None:
        lane_h = area.height() / len(self.lanes)
        fill = QColor(color)
        fill.setAlpha(200)
        text = self.palette().color(QPalette.ColorRole.Text)
        for n, lane in enumerate(self.lanes):
            top = area.top() + n * lane_h
            middle = top + lane_h / 2
            if n:
                painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Mid), 1))
                painter.drawLine(QPointF(area.left(), top), QPointF(area.right(), top))
            if not lane:
                continue
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            columns = max(1, int(area.width()))
            per = len(lane) / columns
            for x in range(columns):
                a = int(x * per)
                b = max(a + 1, int((x + 1) * per))
                peak = max(lane[a:b], default=0.0)
                h = max(1.0, peak * (lane_h / 2 - 3))
                painter.drawRect(QRectF(area.left() + x, middle - h, 1.0, 2 * h))
            if n < len(self.lane_labels) and self.lane_labels[n]:
                painter.setPen(text)
                painter.drawText(QPointF(area.left() + 6, top + 14), self.lane_labels[n])

    def _paint_spans(self, painter: QPainter, area: QRectF) -> None:
        for n, (start, end) in enumerate(self.spans):
            x0, x1 = self.x_of(start), self.x_of(end)
            chosen = n == self.selected_span
            fill = QColor(SPAN_COLOR)
            fill.setAlpha(120 if chosen else 70)
            painter.setBrush(fill)
            painter.setPen(QPen(SPAN_COLOR, 2) if chosen else Qt.PenStyle.NoPen)
            painter.drawRect(QRectF(x0, area.top(), max(2.0, x1 - x0), area.height()))

    def _paint_range(self, painter: QPainter, area: QRectF, color: QColor) -> None:
        if not self.marks.has_range:
            return
        start, end = self.marks.span(self.duration)
        x0, x1 = self.x_of(start), self.x_of(end)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(SHADE)
        painter.drawRect(QRectF(area.left(), area.top(), x0 - area.left(), area.height()))
        painter.drawRect(QRectF(x1, area.top(), area.right() - x1, area.height()))
        painter.setPen(QPen(color, 2))
        for x, sign in ((x0, 1), (x1, -1)):
            painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
            painter.drawLine(QPointF(x, area.top() + 1), QPointF(x + 6 * sign, area.top() + 1))
            painter.drawLine(
                QPointF(x, area.bottom() - 1), QPointF(x + 6 * sign, area.bottom() - 1)
            )

    def _paint_cuts(self, painter: QPainter, area: QRectF) -> None:
        pen = QPen(CUT_COLOR, 1, Qt.PenStyle.DashLine)
        shadow = QPen(QColor(0, 0, 0, 140), 3)
        for t in self.cuts:
            x = self.x_of(t)
            painter.setPen(shadow)
            painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
            painter.setPen(pen)
            painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))

    def _paint_markers(self, painter: QPainter, area: QRectF, color: QColor) -> None:
        for t in self.marks.markers:
            x = self.x_of(t)
            painter.setPen(QPen(color, 1))
            painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
            painter.setBrush(color)
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(x - 5, area.top()),
                        QPointF(x + 5, area.top()),
                        QPointF(x, area.top() + 7),
                    ]
                )
            )

    def _paint_playhead(self, painter: QPainter, area: QRectF) -> None:
        x = self.x_of(self.position)
        color = self.palette().color(QPalette.ColorRole.Highlight)
        painter.setPen(QPen(QColor(0, 0, 0, 120), 4))
        painter.drawLine(QPointF(x, area.top() - 3), QPointF(x, area.bottom() + 3))
        painter.setPen(QPen(color, 2))
        painter.drawLine(QPointF(x, area.top() - 3), QPointF(x, area.bottom() + 3))

    # --- mouse --------------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = True
        t = self.time_at(event.position().x())
        self.pressed.emit(t)
        self.seek.emit(t)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            self.seek.emit(self.time_at(event.position().x()))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._dragging = False
