"""The picture area of an image editor: shows a preview fitted to the window and hands mouse
and key events to the editor's current ``CanvasTool`` in image coordinates.

The canvas shows a ``QImage`` (the preview, often smaller than the original) stretched over a
*logical* size, the size of the result in original pixels. Tools work in those original pixels,
so what they produce applies to the full image unchanged.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, QSizeF, Qt
from PySide6.QtGui import (
    QColor,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

MARGIN = 20
MAX_ZOOM = 4.0  # screen pixels per original pixel, for tiny images
CHECKER = 8


class CanvasTool:
    """What a mouse does on the canvas. Positions are in image (original pixel) coordinates.

    ``paint`` draws on top of the picture in widget coordinates (use ``canvas.to_widget``).
    """

    def press(self, pos: QPointF, event: QMouseEvent) -> None: ...

    def move(self, pos: QPointF, event: QMouseEvent) -> None: ...

    def release(self, pos: QPointF, event: QMouseEvent) -> None: ...

    def double_click(self, pos: QPointF, event: QMouseEvent) -> None: ...

    def key(self, event: QKeyEvent) -> bool:
        """Handle a key press; True when used."""
        return False

    def cursor(self, pos: QPointF) -> Qt.CursorShape:
        return Qt.CursorShape.ArrowCursor

    def paint(self, painter: QPainter, canvas: ImageCanvas) -> None: ...


class ImageCanvas(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(320, 240)
        self.tool: CanvasTool | None = None
        self._image = QImage()
        self._logical = QSizeF(1, 1)
        self._compare: tuple[QImage, QSizeF] | None = None
        self._cache: QPixmap | None = None
        self._cache_key: tuple = ()

    def sizeHint(self) -> QSize:
        return QSize(900, 640)

    # --- what is shown ----------------------------------------------------------------

    def set_image(self, image: QImage, logical: QSizeF | tuple[float, float]) -> None:
        self._image = image
        self._logical = QSizeF(*logical) if isinstance(logical, tuple) else QSizeF(logical)
        self._cache = None
        self.update()

    def set_compare(self, compare: tuple[QImage, QSizeF | tuple[float, float]] | None) -> None:
        """Show another picture instead (Original / Preview), without the tool's overlay."""
        if compare is not None:
            image, logical = compare
            compare = (image, QSizeF(*logical) if isinstance(logical, tuple) else logical)
        self._compare = compare
        self._cache = None
        self.update()

    @property
    def image(self) -> QImage:
        """The picture shown (not the compare one)."""
        return self._image

    @property
    def comparing(self) -> bool:
        return self._compare is not None

    @property
    def logical_size(self) -> QSizeF:
        return self._shown()[1]

    def _shown(self) -> tuple[QImage, QSizeF]:
        return self._compare if self._compare is not None else (self._image, self._logical)

    # --- coordinates ------------------------------------------------------------------

    def image_rect(self) -> QRectF:
        """Where the picture is drawn, in widget coordinates."""
        logical = self._shown()[1]
        lw, lh = max(1.0, logical.width()), max(1.0, logical.height())
        room_w = max(1.0, self.width() - 2 * MARGIN)
        room_h = max(1.0, self.height() - 2 * MARGIN)
        scale = min(room_w / lw, room_h / lh, MAX_ZOOM)
        w, h = lw * scale, lh * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    @property
    def scale(self) -> float:
        """Widget pixels per image pixel."""
        return self.image_rect().width() / max(1.0, self._shown()[1].width())

    def to_image(self, point: QPointF) -> QPointF:
        rect = self.image_rect()
        s = self.scale
        return QPointF((point.x() - rect.x()) / s, (point.y() - rect.y()) / s)

    def to_widget(self, point: QPointF) -> QPointF:
        rect = self.image_rect()
        s = self.scale
        return QPointF(rect.x() + point.x() * s, rect.y() + point.y() * s)

    def rect_to_widget(self, rect: QRectF) -> QRectF:
        top_left = self.to_widget(rect.topLeft())
        s = self.scale
        return QRectF(top_left.x(), top_left.y(), rect.width() * s, rect.height() * s)

    # --- painting ---------------------------------------------------------------------

    def _pixmap(self, rect: QRectF) -> QPixmap:
        image = self._shown()[0]
        key = (image.cacheKey(), round(rect.width()), round(rect.height()))
        if self._cache is None or self._cache_key != key:
            size = QSize(max(1, round(rect.width())), max(1, round(rect.height())))
            scaled = image.scaled(
                size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._cache = QPixmap.fromImage(scaled)
            self._cache_key = key
        return self._cache

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        background = self.palette().color(QPalette.ColorRole.Window).darker(118)
        painter.fillRect(self.rect(), background)
        image = self._shown()[0]
        if image.isNull():
            return
        rect = self.image_rect()
        if image.hasAlphaChannel():
            _checkerboard(painter, rect)
        painter.drawPixmap(rect.topLeft(), self._pixmap(rect))
        if self.tool is not None and not self.comparing:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self.tool.paint(painter, self)

    # --- events -----------------------------------------------------------------------

    def _forward(self, name: str, event: QMouseEvent) -> None:
        if self.tool is None or self.comparing:
            return
        pos = self.to_image(event.position())
        getattr(self.tool, name)(pos, event)
        self.setCursor(self.tool.cursor(pos))
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus()
        if event.button() == Qt.MouseButton.LeftButton:
            self._forward("press", event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._forward("move", event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._forward("release", event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._forward("double_click", event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.tool is not None and not self.comparing and self.tool.key(event):
            self.update()
            return
        super().keyPressEvent(event)


def _checkerboard(painter: QPainter, rect: QRectF) -> None:
    painter.save()
    painter.setClipRect(rect)
    painter.fillRect(rect, QColor(250, 250, 250))
    dark = QColor(214, 214, 214)
    x0, y0 = int(rect.left()), int(rect.top())
    for y in range(y0, int(rect.bottom()) + 1, CHECKER):
        for x in range(
            x0 + ((y - y0) // CHECKER % 2) * CHECKER, int(rect.right()) + 1, 2 * CHECKER
        ):
            painter.fillRect(x, y, CHECKER, CHECKER, dark)
    painter.restore()
