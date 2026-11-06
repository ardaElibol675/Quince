"""Crop image: drag a box on the picture; the part inside is saved as ``name-cropped``.

Drag outside the box (or anywhere while it still covers the whole picture) to draw a new one,
inside it to move it, on an edge or corner to resize it. Shift keeps the box's proportions;
the Ratio list fixes them. Arrow keys move the box by a pixel (Shift: ten). The size fields
set the box size in pixels.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPainterPath
from PySide6.QtWidgets import QComboBox, QLabel, QSpinBox

from quince.backends import imageedit
from quince.core.operations import JobRequest
from quince.ui.editors import rects
from quince.ui.editors.boxes import BoxGesture, handle_cursor, handle_under, paint_box
from quince.ui.editors.canvas import CanvasTool, ImageCanvas
from quince.ui.editors.image._common import ImageEditor, size_text
from quince.ui.editors.rects import Box

RATIOS: tuple[tuple[str, str, float | None], ...] = (
    ("free", "Free", None),
    ("original", "Original", None),  # the picture's own shape
    ("1:1", "Square 1:1", 1.0),
    ("4:3", "4:3", 4 / 3),
    ("3:4", "3:4", 3 / 4),
    ("3:2", "3:2", 3 / 2),
    ("2:3", "2:3", 2 / 3),
    ("16:9", "16:9", 16 / 9),
    ("9:16", "9:16", 9 / 16),
    ("4:5", "4:5", 4 / 5),
)
SHADE = QColor(0, 0, 0, 135)


class CropTool(CanvasTool):
    def __init__(self, editor: CropEditor) -> None:
        self.editor = editor
        self.gesture: BoxGesture | None = None
        self.shift_ratio: float | None = None

    @property
    def canvas(self) -> ImageCanvas:
        return self.editor.canvas

    def _ratio(self, event) -> float | None:
        fixed = self.editor.ratio()
        if fixed:
            return fixed
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            if self.shift_ratio is None:
                box = self.editor.box
                self.shift_ratio = rects.width(box) / max(1e-6, rects.height(box))
            return self.shift_ratio
        self.shift_ratio = None
        return None

    def press(self, pos: QPointF, event: QMouseEvent) -> None:
        box = self.editor.box
        point = (pos.x(), pos.y())
        bounds = self.editor.bounds
        handle = handle_under(self.canvas, box, event.position())
        if handle:
            self.gesture = BoxGesture("resize", point, box, bounds, handle)
        elif rects.contains(box, point) and not rects.same(box, bounds):
            self.gesture = BoxGesture("move", point, box, bounds)
        else:
            self.gesture = BoxGesture("new", rects.clamp_point(point, bounds), box, bounds)
        self.shift_ratio = None

    def move(self, pos: QPointF, event: QMouseEvent) -> None:
        if self.gesture is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        if self.gesture.mode == "new":
            self.shift_ratio = None  # a new box with Shift is a square
            ratio = self.editor.ratio() or (
                1.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else None
            )
        else:
            ratio = self._ratio(event)
        min_size = max(1.0, 8 / self.canvas.scale)
        self.editor.set_box(self.gesture.update((pos.x(), pos.y()), ratio, min_size))

    def release(self, pos: QPointF, event: QMouseEvent) -> None:
        gesture, self.gesture = self.gesture, None
        if gesture is None:
            return
        if gesture.mode == "new" and rects.is_tiny(self.editor.box, NEW_MIN / self.canvas.scale):
            self.editor.set_box(gesture.box)  # a click, not a drag: keep the old box
            return
        self.editor.commit()

    def key(self, event: QKeyEvent) -> bool:
        steps = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1),
            Qt.Key.Key_Down: (0, 1),
        }
        if event.key() not in steps:
            return False
        dx, dy = steps[event.key()]
        step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        box = rects.moved(self.editor.box, dx * step, dy * step, self.editor.bounds)
        self.editor.set_box(box)
        self.editor.commit("nudge")
        return True

    def cursor(self, pos: QPointF) -> Qt.CursorShape:
        if self.gesture is not None:
            if self.gesture.mode == "resize":
                return handle_cursor(self.gesture.handle)
            if self.gesture.mode == "move":
                return Qt.CursorShape.ClosedHandCursor
            return Qt.CursorShape.CrossCursor
        widget_pos = self.canvas.to_widget(pos)
        handle = handle_under(self.canvas, self.editor.box, widget_pos)
        if handle:
            return handle_cursor(handle)
        box = self.editor.box
        if rects.contains(box, (pos.x(), pos.y())) and not rects.same(box, self.editor.bounds):
            return Qt.CursorShape.OpenHandCursor
        return Qt.CursorShape.CrossCursor

    def paint(self, painter: QPainter, canvas: ImageCanvas) -> None:
        box = self.editor.box
        inside = canvas.rect_to_widget(QRectF(QPointF(box[0], box[1]), QPointF(box[2], box[3])))
        shade = QPainterPath()
        shade.addRect(canvas.image_rect())
        hole = QPainterPath()
        hole.addRect(inside)
        painter.fillPath(shade.subtracted(hole), SHADE)
        paint_box(painter, canvas, box, thirds=self.gesture is not None)


NEW_MIN = 4.0  # screen pixels


class CropEditor(ImageEditor):
    name = "image.crop"
    title = "Crop Image"
    compare_label = "Preview"
    save_label = "Crop"

    def __init__(self, selection, data, parent=None) -> None:
        super().__init__(selection, data, parent)
        w, h = self.image_size
        self.bounds: Box = (0.0, 0.0, float(w), float(h))
        self.box: Box = self.bounds
        self._updating = False

        self.add_tool(QLabel("Ratio"))
        self.ratio_combo = QComboBox()
        for key, label, _value in RATIOS:
            self.ratio_combo.addItem(label, key)
        self.ratio_combo.currentIndexChanged.connect(self._on_ratio)
        self.add_tool(self.ratio_combo)
        self.toolbar.addSpacing(12)
        self.add_tool(QLabel("Size"))
        self.width_spin = self._spin(w)
        self.add_tool(self.width_spin)
        self.add_tool(QLabel("×"))
        self.height_spin = self._spin(h)
        self.add_tool(self.height_spin)
        self.add_tool(QLabel("px"))
        self.toolbar.addStretch(1)

        self.canvas.set_image(self.original, self.full_size())
        self.canvas.tool = CropTool(self)
        self.set_hint("Drag to choose the area · Shift keeps the shape · Arrows move it")
        self._sync_controls()
        self.start_history()

    def _spin(self, maximum: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, maximum)
        spin.setValue(maximum)
        spin.setKeyboardTracking(False)
        spin.valueChanged.connect(self._on_size)
        return spin

    # --- state ------------------------------------------------------------------------

    def state(self) -> tuple:
        return (imageedit.pixel_box(self.box, self.image_size), self.ratio_combo.currentData())

    def set_state(self, state: tuple) -> None:
        box, ratio = state
        self._updating = True
        self.ratio_combo.setCurrentIndex(self.ratio_combo.findData(ratio))
        self._updating = False
        self.set_box(tuple(float(v) for v in box))

    def can_save(self) -> bool:
        return imageedit.pixel_box(self.box, self.image_size) != (0, 0, *self.image_size)

    def ratio(self) -> float | None:
        key = self.ratio_combo.currentData()
        if key == "original":
            return rects.aspect(self.image_size)
        return dict((k, v) for k, _label, v in RATIOS)[key]

    def set_box(self, box: Box) -> None:
        self.box = box
        self._sync_controls()
        self.canvas.update()
        self.update_buttons()

    def _sync_controls(self) -> None:
        x0, y0, x1, y1 = imageedit.pixel_box(self.box, self.image_size)
        self._updating = True
        self.width_spin.setValue(x1 - x0)
        self.height_spin.setValue(y1 - y0)
        self._updating = False
        self.info.setText(f"{size_text(self.image_size)} → {size_text((x1 - x0, y1 - y0))}")

    # --- controls -----------------------------------------------------------------------

    def _on_ratio(self, _index: int) -> None:
        if self._updating:
            return
        ratio = self.ratio()
        if ratio:
            self.set_box(rects.fit_ratio(self.box, ratio, self.bounds))
        self.commit()

    def _on_size(self, _value: int) -> None:
        if self._updating:
            return
        ratio = self.ratio()
        w, h = float(self.width_spin.value()), float(self.height_spin.value())
        if ratio:
            if self.sender() is self.width_spin:
                h = w / ratio
            else:
                w = h * ratio
            bw, bh = rects.width(self.bounds), rects.height(self.bounds)
            factor = min(1.0, bw / w, bh / h)
            w, h = w * factor, h * factor
        cx, cy = rects.center(self.box)
        box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        self.set_box(rects.moved(box, 0, 0, self.bounds))
        self.commit("size")

    # --- result -------------------------------------------------------------------------

    def compare_image(self) -> tuple[QImage, QSizeF]:
        x0, y0, x1, y1 = imageedit.pixel_box(self.box, self.image_size)
        s = self.loaded.scale
        part = QRectF(x0 * s, y0 * s, (x1 - x0) * s, (y1 - y0) * s).toAlignedRect()
        return self.original.copy(part), QSizeF(x1 - x0, y1 - y0)

    def build_job(self) -> JobRequest:
        box = imageedit.pixel_box(self.box, self.image_size)
        return self.job(lambda im: imageedit.crop(im, box), "cropped")


EDITOR = CropEditor
