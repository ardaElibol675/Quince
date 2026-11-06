"""Crop, in the Image Editor (and the box tool the Video Editor shares).

Drag outside the box (or anywhere while it still covers the whole picture) to draw a new one,
inside it to move it, on an edge or corner to resize it. Shift keeps the box's proportions;
the Ratio list fixes them. Arrow keys move the box by a pixel (Shift: ten). The size fields
set the box size in pixels. The box is shown on the turned picture; it is kept in pixels of
the original, so turning the picture later keeps the same part.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPainterPath
from PySide6.QtWidgets import QComboBox, QLabel, QSpinBox

from quince.backends import imageedit
from quince.ui.editors import rects
from quince.ui.editors.base import Mode, tool_button, toolbar_row
from quince.ui.editors.boxes import BoxGesture, handle_cursor, handle_under, paint_box
from quince.ui.editors.canvas import CanvasTool, ImageCanvas
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
NEW_MIN = 4.0  # screen pixels


def ratio_combo() -> QComboBox:
    combo = QComboBox()
    for key, label, _value in RATIOS:
        combo.addItem(label, key)
    return combo


def ratio_value(key: str, size: tuple[float, float]) -> float | None:
    if key == "original":
        return rects.aspect(size)
    return dict((k, v) for k, _label, v in RATIOS)[key]


class CropTool(CanvasTool):
    """The crop box on the canvas. ``owner`` provides ``canvas``, ``box``, ``bounds``,
    ``ratio()``, ``set_box(box)`` and ``commit(merge)``."""

    def __init__(self, owner) -> None:
        self.editor = owner
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


class CropPart:
    """The Crop mode of the Image Editor. Coordinates on the canvas are pixels of the turned
    (not yet cropped) picture."""

    def __init__(self, editor) -> None:
        self.editor = editor
        self._updating = False
        widget, row = toolbar_row()
        row.addWidget(QLabel("Ratio"))
        self.ratio_combo = ratio_combo()
        self.ratio_combo.currentIndexChanged.connect(self._on_ratio)
        row.addWidget(self.ratio_combo)
        row.addSpacing(12)
        row.addWidget(QLabel("Size"))
        self.width_spin = self._spin("width")
        row.addWidget(self.width_spin)
        row.addWidget(QLabel("×"))
        self.height_spin = self._spin("height")
        row.addWidget(self.height_spin)
        row.addWidget(QLabel("px"))
        row.addSpacing(12)
        self.reset_button = tool_button("Whole Picture", tip="Keep the whole picture")
        self.reset_button.clicked.connect(self.reset)
        row.addWidget(self.reset_button)
        row.addStretch(1)
        self.tool = CropTool(self)
        self.mode = Mode(
            "crop",
            "Crop",
            icon="quince-crop",
            tip="Crop",
            toolbar=widget,
            tool=self.tool,
            hint="Drag to choose the area · Shift keeps the shape · Arrows move it",
        )
        self.sync()

    def _spin(self, side: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, 1)
        spin.setKeyboardTracking(False)
        spin.valueChanged.connect(lambda _v: self._on_size(side))
        return spin

    # --- what CropTool needs ------------------------------------------------------------

    @property
    def canvas(self) -> ImageCanvas:
        return self.editor.canvas

    def _geometry(self):
        return self.editor.edit.geometry(self.editor.image_size, crop=False)

    @property
    def bounds(self) -> Box:
        return self._geometry().bounds

    @property
    def box(self) -> Box:
        crop = self.editor.edit.crop
        if crop is None:
            return self.bounds
        return self._geometry().forward_box(crop)

    def ratio(self) -> float | None:
        return ratio_value(self.ratio_combo.currentData(), self._geometry().out_size)

    def set_box(self, box: Box) -> None:
        crop = self._geometry().inverse_box(box)
        self.editor.change(replace(self.editor.edit, crop=crop), record=False)
        self.sync()

    def commit(self, merge: str | None = None) -> None:
        self.editor.change(self.editor.edit, merge=merge)

    # --- controls -----------------------------------------------------------------------

    def reset(self) -> None:
        self.editor.change(replace(self.editor.edit, crop=None))

    def sync(self) -> None:
        """Show the current box in the size fields."""
        bw, bh = self._geometry().out_size
        x0, y0, x1, y1 = self.box
        self._updating = True
        for spin, maximum, value in (
            (self.width_spin, bw, x1 - x0),
            (self.height_spin, bh, y1 - y0),
        ):
            spin.setRange(1, max(1, round(maximum)))
            spin.setValue(max(1, round(value)))
        self._updating = False
        self.reset_button.setEnabled(self.editor.edit.crop is not None)

    def _on_ratio(self, _index: int) -> None:
        if self._updating:
            return
        ratio = self.ratio()
        if ratio:
            self.set_box(rects.fit_ratio(self.box, ratio, self.bounds))
            self.commit()

    def _on_size(self, side: str) -> None:
        if self._updating:
            return
        ratio = self.ratio()
        bounds = self.bounds
        w, h = float(self.width_spin.value()), float(self.height_spin.value())
        if ratio:
            if side == "width":
                h = w / ratio
            else:
                w = h * ratio
            factor = min(1.0, rects.width(bounds) / w, rects.height(bounds) / h)
            w, h = w * factor, h * factor
        cx, cy = rects.center(self.box)
        box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        self.set_box(rects.moved(box, 0, 0, bounds))
        self.commit("size")


def rounded_crop(crop: Box | None, size: tuple[int, int]) -> Box | None:
    """``crop`` in whole pixels; None when it keeps the whole picture."""
    if crop is None:
        return None
    box = imageedit.pixel_box(crop, size)
    if box == (0, 0, *size):
        return None
    return tuple(float(v) for v in box)  # type: ignore[return-value]
