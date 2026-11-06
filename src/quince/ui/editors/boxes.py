"""Drawing, moving and resizing a box with the mouse (Crop, Redact), on top of ``rects``."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from quince.ui.editors import rects
from quince.ui.editors.canvas import ImageCanvas
from quince.ui.editors.rects import Box

HANDLE_PX = 9  # handle size on screen
GRAB_PX = 8  # how close to an edge or corner a press grabs it
NEW_MIN_PX = 4  # a click (no drag) does not make a box

_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor,
    "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor,
    "w": Qt.CursorShape.SizeHorCursor,
}


def handle_cursor(handle: str) -> Qt.CursorShape:
    return _CURSORS[handle]


def widget_box(canvas: ImageCanvas, box: Box) -> Box:
    rect = canvas.rect_to_widget(QRectF(QPointF(box[0], box[1]), QPointF(box[2], box[3])))
    return rect.left(), rect.top(), rect.right(), rect.bottom()


def handle_under(canvas: ImageCanvas, box: Box, widget_pos: QPointF) -> str | None:
    """The handle of ``box`` (image coordinates) under a widget position."""
    return rects.handle_at(widget_box(canvas, box), (widget_pos.x(), widget_pos.y()), GRAB_PX)


@dataclass
class BoxGesture:
    """One mouse drag: ``mode`` is "new", "move" or "resize" (by ``handle``)."""

    mode: str
    start: tuple[float, float]
    box: Box
    bounds: Box
    handle: str = ""

    def update(self, pos: tuple[float, float], ratio: float | None, min_size: float) -> Box:
        if self.mode == "new":
            return rects.from_points(self.start, pos, self.bounds, ratio=ratio, min_size=0.0)
        if self.mode == "move":
            dx, dy = pos[0] - self.start[0], pos[1] - self.start[1]
            return rects.moved(self.box, dx, dy, self.bounds)
        # Drag the handle by the pointer's movement, so grabbing it off-center does not jump.
        hx, hy = rects.handle_points(self.box)[self.handle]
        target = (hx + pos[0] - self.start[0], hy + pos[1] - self.start[1])
        return rects.resized(
            self.box, self.handle, target, self.bounds, ratio=ratio, min_size=min_size
        )


def paint_box(
    painter: QPainter,
    canvas: ImageCanvas,
    box: Box,
    *,
    handles: bool = True,
    dashed: bool = False,
    thirds: bool = False,
) -> None:
    x0, y0, x1, y1 = widget_box(canvas, box)
    rect = QRectF(x0, y0, x1 - x0, y1 - y0)
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if thirds:
        painter.setPen(QPen(QColor(255, 255, 255, 110), 1))
        for n in (1, 2):
            x = rect.left() + rect.width() * n / 3
            y = rect.top() + rect.height() * n / 3
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
    painter.setPen(QPen(QColor(0, 0, 0, 150), 3))
    painter.drawRect(rect)
    white = QPen(QColor(255, 255, 255), 1)
    if dashed:
        white.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(white)
    painter.drawRect(rect)
    if handles:
        painter.setPen(QPen(QColor(0, 0, 0, 170), 1))
        painter.setBrush(QColor(255, 255, 255))
        half = HANDLE_PX / 2
        for hx, hy in rects.handle_points((x0, y0, x1, y1)).values():
            painter.drawRect(QRectF(hx - half, hy - half, HANDLE_PX, HANDLE_PX))
    painter.restore()
