"""Vector annotations (pen, highlighter, lines, arrows, boxes, ellipses, text) painted with Qt.

The Annotate editor draws the same ``Shape`` list on its preview with ``paint_shapes`` that
the job paints on the full image (``annotate``), so the result matches what was drawn,
antialiased, with the same font. Painting on a ``QImage`` is allowed off the GUI thread; it
needs a ``QGuiApplication`` for fonts (the service has one).

Coordinates and sizes are pixels of the upright original image.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

from PIL import Image
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
)

KINDS = ("pen", "highlight", "line", "arrow", "rect", "ellipse", "text")
TWO_POINT_KINDS = frozenset({"line", "arrow", "rect", "ellipse"})
HIGHLIGHT_ALPHA = 0.4
ARROW_HEAD = 3.6  # head length, in stroke widths
ARROW_MIN_HEAD = 10.0
TEXT_OUTLINE = 0.12  # of the font size
Point = tuple[float, float]
RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Shape:
    """One annotation. ``points``: the path of a pen or highlighter stroke, start and end of
    the two-point kinds, the top-left corner of a text. ``width`` is the stroke width, or
    the font size (pixels) of a text."""

    kind: str
    points: tuple[Point, ...]
    color: RGB
    width: float
    text: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown shape {self.kind!r}")
        if not self.points:
            raise ValueError("a shape needs at least one point")

    def moved(self, dx: float, dy: float) -> Shape:
        return replace(self, points=tuple((x + dx, y + dy) for x, y in self.points))

    @property
    def is_empty(self) -> bool:
        """Nothing to draw: a text without characters."""
        return self.kind == "text" and not self.text.strip()


def text_font(size: float) -> QFont:
    font = QFont()
    font.setFamilies(["Ubuntu", "Cantarell", "DejaVu Sans", "Sans Serif"])
    font.setPixelSize(max(1, round(size)))
    font.setWeight(QFont.Weight.Bold)
    return font


def _contrast(color: RGB) -> QColor:
    r, g, b = color
    light = 0.299 * r + 0.587 * g + 0.114 * b > 150
    return QColor(0, 0, 0, 160) if light else QColor(255, 255, 255, 200)


def _smooth_path(points: Sequence[Point]) -> QPainterPath:
    """A curve through the midpoints of a pen stroke (smooths the jitter of the mouse)."""
    path = QPainterPath(QPointF(*points[0]))
    if len(points) == 1:
        path.lineTo(QPointF(*points[0]))
        return path
    for (x0, y0), (x1, y1) in itertools.pairwise(points):
        path.quadTo(QPointF(x0, y0), QPointF((x0 + x1) / 2, (y0 + y1) / 2))
    path.lineTo(QPointF(*points[-1]))
    return path


def _two_points(shape: Shape) -> tuple[QPointF, QPointF]:
    a = QPointF(*shape.points[0])
    b = QPointF(*shape.points[-1])
    return a, b


def _arrow_head(a: QPointF, b: QPointF, width: float) -> tuple[QPointF, QPolygonF]:
    """The end of the shaft and the head of an arrow from ``a`` to ``b``."""
    length = max(ARROW_MIN_HEAD, width * ARROW_HEAD)
    line = QLineF(a, b)
    total = line.length()
    if total < 1e-6:
        return b, QPolygonF()
    length = min(length, total)
    ux, uy = (b.x() - a.x()) / total, (b.y() - a.y()) / total
    base = QPointF(b.x() - ux * length, b.y() - uy * length)
    half = length * 0.55
    left = QPointF(base.x() - uy * half, base.y() + ux * half)
    right = QPointF(base.x() + uy * half, base.y() - ux * half)
    shaft_end = QPointF(b.x() - ux * length * 0.6, b.y() - uy * length * 0.6)
    return shaft_end, QPolygonF([b, left, right])


def text_lines(text: str) -> list[str]:
    return text.split("\n")


def text_rect(shape: Shape) -> QRectF:
    """The box a text covers."""
    metrics = QFontMetricsF(text_font(shape.width))
    lines = text_lines(shape.text) or [""]
    width = max(metrics.horizontalAdvance(line) for line in lines)
    height = metrics.lineSpacing() * (len(lines) - 1) + metrics.height()
    x, y = shape.points[0]
    return QRectF(x, y, max(width, shape.width * 0.3), height)


def outline(shape: Shape) -> QPainterPath:
    """The geometry of ``shape`` without stroke width (for bounds and hit tests)."""
    if shape.kind in ("pen", "highlight"):
        return _smooth_path(shape.points)
    if shape.kind == "text":
        path = QPainterPath()
        path.addRect(text_rect(shape))
        return path
    a, b = _two_points(shape)
    path = QPainterPath()
    if shape.kind == "rect":
        path.addRect(QRectF(a, b).normalized())
    elif shape.kind == "ellipse":
        path.addEllipse(QRectF(a, b).normalized())
    else:
        path.moveTo(a)
        path.lineTo(b)
    return path


def hit_area(shape: Shape, tolerance: float) -> QPainterPath:
    """Where a click selects ``shape``: its stroke widened by ``tolerance`` (a text's box)."""
    path = outline(shape)
    if shape.kind == "text":
        return path
    stroker = QPainterPathStroker()
    stroker.setWidth(max(shape.width * (3 if shape.kind == "highlight" else 1), 1) + tolerance)
    stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return stroker.createStroke(path)


def bounds(shape: Shape) -> QRectF:
    """The area ``shape`` paints on."""
    if shape.kind == "text":
        return text_rect(shape)
    extra = shape.width * (3 if shape.kind == "highlight" else 1)
    if shape.kind == "arrow":
        extra = max(extra, max(ARROW_MIN_HEAD, shape.width * ARROW_HEAD))
    return outline(shape).boundingRect().adjusted(-extra, -extra, extra, extra)


def paint_shape(painter: QPainter, shape: Shape) -> None:
    if shape.is_empty:
        return
    color = QColor(*shape.color)
    cap = Qt.PenCapStyle.RoundCap
    join = Qt.PenJoinStyle.RoundJoin
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if shape.kind == "pen":
        painter.setPen(QPen(color, shape.width, Qt.PenStyle.SolidLine, cap, join))
        painter.drawPath(_smooth_path(shape.points))
    elif shape.kind == "highlight":
        color.setAlphaF(HIGHLIGHT_ALPHA)
        # One path is stroked once, so overlapping parts of a stroke do not get darker.
        painter.setPen(QPen(color, shape.width * 3, Qt.PenStyle.SolidLine, cap, join))
        painter.drawPath(_smooth_path(shape.points))
    elif shape.kind in ("rect", "ellipse"):
        painter.setPen(QPen(color, shape.width, Qt.PenStyle.SolidLine, cap, join))
        a, b = _two_points(shape)
        rect = QRectF(a, b).normalized()
        if shape.kind == "rect":
            painter.drawRect(rect)
        else:
            painter.drawEllipse(rect)
    elif shape.kind == "line":
        painter.setPen(QPen(color, shape.width, Qt.PenStyle.SolidLine, cap, join))
        painter.drawLine(*_two_points(shape))
    elif shape.kind == "arrow":
        a, b = _two_points(shape)
        shaft_end, head = _arrow_head(a, b, shape.width)
        painter.setPen(QPen(color, shape.width, Qt.PenStyle.SolidLine, cap, join))
        painter.drawLine(a, shaft_end)
        if not head.isEmpty():
            painter.setPen(
                QPen(color, max(1.0, shape.width * 0.35), Qt.PenStyle.SolidLine, cap, join)
            )
            painter.setBrush(QBrush(color))
            painter.drawPolygon(head)
    else:
        _paint_text(painter, shape, color)
    painter.restore()


def _paint_text(painter: QPainter, shape: Shape, color: QColor) -> None:
    font = text_font(shape.width)
    metrics = QFontMetricsF(font)
    x, y = shape.points[0]
    path = QPainterPath()
    for n, line in enumerate(text_lines(shape.text)):
        path.addText(QPointF(x, y + metrics.ascent() + n * metrics.lineSpacing()), font, line)
    halo = QPen(_contrast(shape.color), max(1.0, shape.width * TEXT_OUTLINE))
    halo.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.strokePath(path, halo)  # a soft outline keeps text readable on any background
    painter.fillPath(path, QBrush(color))


def paint_shapes(painter: QPainter, shapes: Sequence[Shape]) -> None:
    """Paint ``shapes`` in order; the painter's transform maps image pixels."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    for shape in shapes:
        paint_shape(painter, shape)


# --- Pillow <-> Qt --------------------------------------------------------------------------


def to_qimage(im: Image.Image) -> QImage:
    """A copy of ``im`` as a ``QImage``: RGBA8888 with transparency, RGB888 without (so it is
    not drawn over a transparency checkerboard)."""
    if im.mode == "RGB":
        data = im.tobytes("raw", "RGB")
        return QImage(data, im.width, im.height, im.width * 3, QImage.Format.Format_RGB888).copy()
    rgba = im if im.mode == "RGBA" else im.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    return QImage(
        data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888
    ).copy()


def from_qimage(image: QImage) -> Image.Image:
    """``image`` as an RGBA Pillow image (a copy)."""
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = image.width(), image.height()
    data = bytes(image.constBits())[: image.bytesPerLine() * h]
    return Image.frombuffer("RGBA", (w, h), data, "raw", "RGBA", image.bytesPerLine(), 1).copy()


def annotate(im: Image.Image, shapes: Sequence[Shape]) -> Image.Image:
    """``im`` with ``shapes`` painted on it (same mode as ``im``: RGB or RGBA)."""
    if not shapes:
        return im
    canvas = to_qimage(im if im.mode == "RGBA" else im.convert("RGBA"))
    painter = QPainter(canvas)
    try:
        paint_shapes(painter, shapes)
    finally:
        painter.end()
    result = from_qimage(canvas)
    return result if im.mode == "RGBA" else result.convert("RGB")


def default_width(size: tuple[int, int], fraction: float) -> float:
    """A stroke width that looks alike on small and large images: ``fraction`` of the
    shorter side, at least 2 pixels."""
    return max(2.0, round(min(size) * fraction, 1))


def snap_angle(a: Point, b: Point, step_degrees: float = 45.0) -> Point:
    """``b`` moved so the line from ``a`` points at a multiple of ``step_degrees`` (Shift)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return b
    step = math.radians(step_degrees)
    angle = round(math.atan2(dy, dx) / step) * step
    return a[0] + length * math.cos(angle), a[1] + length * math.sin(angle)


def snap_square(a: Point, b: Point) -> Point:
    """``b`` moved so the box from ``a`` is a square (Shift on boxes and ellipses)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    side = max(abs(dx), abs(dy))
    return a[0] + math.copysign(side, dx or 1), a[1] + math.copysign(side, dy or 1)
