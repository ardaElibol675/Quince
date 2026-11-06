"""Annotate, in the Image Editor: draw with a pen or highlighter, add lines, arrows, boxes,
ellipses and text.

Tools (keys in this mode): Select V, Pen P, Highlighter H, Line L, Arrow A, Box R, Ellipse E,
Text T.
Shift makes lines and arrows snap to 45° and boxes and ellipses square. With Select, click a
shape to pick it, drag to move it, Delete removes it; double-click a text to change it. A color
or size chosen while a shape is picked changes that shape. Text: click to place, type, Enter
to finish, Esc to drop it. Shapes are kept in pixels of the original, so they stay on the
same part of the picture when the crop or the turns change.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFocusEvent, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QButtonGroup, QLineEdit, QWidget

from quince.backends import painting
from quince.backends.painting import Shape
from quince.core import forms
from quince.ui.editors.base import Mode, Swatches, separator, tool_button, toolbar_row
from quince.ui.editors.canvas import CanvasTool, ImageCanvas

REMEMBER_KEY = "editor.annotate"
TOOLS = (
    ("select", "Select", "V"),
    ("pen", "Pen", "P"),
    ("highlight", "Highlighter", "H"),
    ("line", "Line", "L"),
    ("arrow", "Arrow", "A"),
    ("rect", "Box", "R"),
    ("ellipse", "Ellipse", "E"),
    ("text", "Text", "T"),
)
COLORS = [
    ("Red", (229, 57, 53, 255)),
    ("Orange", (251, 140, 0, 255)),
    ("Yellow", (253, 216, 53, 255)),
    ("Green", (67, 160, 71, 255)),
    ("Blue", (30, 136, 229, 255)),
    ("Black", (33, 33, 33, 255)),
    ("White", (255, 255, 255, 255)),
]
# Stroke width and font size as fractions of the picture's shorter side.
SIZES = {"s": (0.003, 0.025), "m": (0.006, 0.04), "l": (0.012, 0.065)}
SIZE_LABELS = (("s", "S", "Thin"), ("m", "M", "Medium"), ("l", "L", "Thick"))
PICK_PX = 6  # click tolerance on screen
MIN_SHAPE_PX = 3


class TextEntry(QLineEdit):
    """The text being typed, placed over the picture. Enter finishes, Esc drops it."""

    def __init__(self, parent: QWidget, finish, cancel) -> None:
        super().__init__(parent)
        self._finish = finish
        self._cancel = cancel
        self.setFrame(False)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()  # never reaches the window, which would save
            self._finish()
            return
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self._cancel()
            return
        super().keyPressEvent(event)
        self.adjustSize()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self._finish()


class AnnotateTool(CanvasTool):
    def __init__(self, editor: AnnotatePart) -> None:
        self.editor = editor
        self.drawing: Shape | None = None
        self.drag_start: tuple[float, float] | None = None
        self.drag_shape: Shape | None = None
        self.moved = False

    @property
    def canvas(self) -> ImageCanvas:
        return self.editor.canvas

    def press(self, pos: QPointF, event: QMouseEvent) -> None:
        editor = self.editor
        point = (pos.x(), pos.y())
        tool = editor.tool_name
        if tool == "select":
            index = editor.shape_at(pos)
            editor.select(index)
            if index is not None:
                self.drag_start = point
                self.drag_shape = editor.shapes[index]
                self.moved = False
            return
        editor.select(None)
        if tool == "text":
            index = editor.shape_at(pos, kinds=("text",))
            if index is not None:
                editor.edit_text(index)
            else:
                editor.new_text(point)
            return
        points = (point,) if tool in ("pen", "highlight") else (point, point)
        self.drawing = Shape(tool, points, editor.color(), editor.stroke_width())

    def move(self, pos: QPointF, event: QMouseEvent) -> None:
        if not event.buttons() & Qt.MouseButton.LeftButton:
            return
        point = (pos.x(), pos.y())
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if self.drag_shape is not None and self.drag_start is not None:
            dx, dy = point[0] - self.drag_start[0], point[1] - self.drag_start[1]
            self.moved = self.moved or abs(dx) + abs(dy) > 0
            self.editor.replace_shape(self.editor.selected, self.drag_shape.moved(dx, dy))
            return
        shape = self.drawing
        if shape is None:
            return
        if shape.kind in ("pen", "highlight"):
            last = shape.points[-1]
            if abs(point[0] - last[0]) + abs(point[1] - last[1]) >= 1.0 / self.canvas.scale:
                self.drawing = replace(shape, points=(*shape.points, point))
            return
        start = shape.points[0]
        if shift:
            if shape.kind in ("line", "arrow"):
                point = painting.snap_angle(start, point)
            else:
                point = painting.snap_square(start, point)
        self.drawing = replace(shape, points=(start, point))

    def release(self, pos: QPointF, event: QMouseEvent) -> None:
        if self.drag_shape is not None:
            self.drag_shape = self.drag_start = None
            if self.moved:
                self.editor.commit()
            return
        shape, self.drawing = self.drawing, None
        if shape is None:
            return
        if shape.kind in painting.TWO_POINT_KINDS:
            (x0, y0), (x1, y1) = shape.points[0], shape.points[-1]
            if max(abs(x1 - x0), abs(y1 - y0)) * self.canvas.scale < MIN_SHAPE_PX:
                return  # a click, not a drag
        self.editor.add_shape(shape)

    def double_click(self, pos: QPointF, event: QMouseEvent) -> None:
        if self.editor.tool_name == "select":
            index = self.editor.shape_at(pos, kinds=("text",))
            if index is not None:
                self.editor.edit_text(index)

    def key(self, event: QKeyEvent) -> bool:
        editor = self.editor
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            return editor.delete_selected()
        if event.key() == Qt.Key.Key_Escape and editor.selected is not None:
            editor.select(None)
            return True
        return False

    def cursor(self, pos: QPointF) -> Qt.CursorShape:
        tool = self.editor.tool_name
        if tool == "select":
            if self.drag_shape is not None:
                return Qt.CursorShape.ClosedHandCursor
            if self.editor.shape_at(pos) is not None:
                return Qt.CursorShape.OpenHandCursor
            return Qt.CursorShape.ArrowCursor
        if tool == "text":
            return Qt.CursorShape.IBeamCursor
        return Qt.CursorShape.CrossCursor

    def paint(self, painter: QPainter, canvas: ImageCanvas) -> None:
        editor = self.editor
        rect = canvas.image_rect()
        painter.save()
        painter.setClipRect(rect)
        painter.translate(rect.topLeft())
        painter.scale(canvas.scale, canvas.scale)
        all_shapes = editor.shapes
        shapes = [s for n, s in enumerate(all_shapes) if n != editor.editing]
        if self.drawing is not None:
            shapes.append(self.drawing)
        painting.paint_shapes(painter, shapes)
        painter.restore()
        if editor.selected is not None and editor.selected != editor.editing:
            box = painting.bounds(all_shapes[editor.selected])
            widget = canvas.rect_to_widget(box).adjusted(-3, -3, 3, 3)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 150), 3))
            painter.drawRect(widget)
            pen = QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(widget)


class AnnotatePart:
    """The Annotate mode. On the canvas, shapes are in pixels of the cropped and turned
    picture; the edit keeps them in pixels of the original."""

    def __init__(self, editor) -> None:
        self.editor = editor
        self.selected: int | None = None
        self.editing: int | None = None  # the text being typed again
        self.entry: TextEntry | None = None
        self._entry_point: tuple[float, float] = (0.0, 0.0)
        self._before_edit: Shape | None = None
        saved = forms.load_section(REMEMBER_KEY)
        widget, row = toolbar_row()
        self.mode = Mode(
            "annotate",
            "Annotate",
            icon="draw-freehand",
            tip="Draw, arrows, boxes, text",
            toolbar=widget,
            hint="Shift snaps angles and squares · Del removes the picked shape",
        )

        self.tools = QButtonGroup(widget)
        self.tools.setExclusive(True)
        for key, label, shortcut in TOOLS:
            button = tool_button(label, tip=f"{label}  {shortcut}", checkable=True)
            button.setProperty("quince_tool", key)
            self.tools.addButton(button)
            row.addWidget(button)
            editor.mode_key(self.mode, shortcut, button.click)
        self.tools.buttonClicked.connect(lambda _b: self._on_tool())
        row.addWidget(separator())

        color = saved.get("color")
        valid = isinstance(color, list) and len(color) == 4
        self.swatches = Swatches(COLORS, tuple(color) if valid else COLORS[0][1])
        self.swatches.picked.connect(self._on_color)
        row.addWidget(self.swatches)
        row.addWidget(separator())

        self.sizes = QButtonGroup(widget)
        self.sizes.setExclusive(True)
        size = saved.get("size") if saved.get("size") in SIZES else "m"
        for key, text, tip in SIZE_LABELS:
            button = tool_button(text, tip=tip, checkable=True)
            button.setProperty("quince_size", key)
            button.setChecked(key == size)
            self.sizes.addButton(button)
            row.addWidget(button)
        self.sizes.buttonClicked.connect(lambda _b: self._on_size())
        row.addWidget(separator())
        self.delete_button = tool_button("Delete", icon="edit-delete", tip="Delete  Del")
        self.delete_button.clicked.connect(self.delete_selected)
        row.addWidget(self.delete_button)
        row.addStretch(1)

        tool = saved.get("tool") if saved.get("tool") in dict((t[0], t) for t in TOOLS) else "arrow"
        self.set_tool(tool)
        self.tool = AnnotateTool(self)
        self.mode.tool = self.tool
        self._update_selection()

    @property
    def canvas(self) -> ImageCanvas:
        return self.editor.canvas

    def _geometry(self):
        return self.editor.edit.geometry(self.editor.image_size)

    @property
    def shapes(self) -> tuple[Shape, ...]:
        geometry = self._geometry()
        return tuple(geometry.forward_shape(s) for s in self.editor.edit.shapes)

    @shapes.setter
    def shapes(self, shapes) -> None:
        geometry = self._geometry()
        stored = tuple(geometry.inverse_shape(s) for s in shapes)
        self.editor.change(replace(self.editor.edit, shapes=stored), record=False)

    def commit(self, merge: str | None = None) -> None:
        self.editor.change(self.editor.edit, merge=merge)

    # --- settings -----------------------------------------------------------------------

    @property
    def tool_name(self) -> str:
        button = self.tools.checkedButton()
        return button.property("quince_tool") if button is not None else "select"

    def set_tool(self, key: str) -> None:
        for button in self.tools.buttons():
            if button.property("quince_tool") == key:
                button.setChecked(True)
        self._on_tool()

    def size_key(self) -> str:
        button = self.sizes.checkedButton()
        return button.property("quince_size") if button is not None else "m"

    def color(self) -> tuple[int, int, int]:
        return tuple(self.swatches.color()[:3])  # type: ignore[return-value]

    def stroke_width(self, kind: str = "") -> float:
        stroke, text = SIZES[self.size_key()]
        kind = kind or self.tool_name
        w, h = self._geometry().out_size
        size = (max(1, round(w)), max(1, round(h)))
        return painting.default_width(size, text if kind == "text" else stroke)

    def remember(self) -> None:
        forms.save_section(
            REMEMBER_KEY,
            {"color": list(self.swatches.color()), "size": self.size_key(), "tool": self.tool_name},
        )

    # --- state ------------------------------------------------------------------------

    def sync(self) -> None:
        """After undo / redo."""
        self._close_entry(keep=False)
        if self.selected is not None and self.selected >= len(self.editor.edit.shapes):
            self.selected = None
        self._update_selection()

    def shape_at(self, pos: QPointF, kinds: tuple[str, ...] = ()) -> int | None:
        tolerance = PICK_PX / self.canvas.scale
        shapes = self.shapes
        for index in range(len(shapes) - 1, -1, -1):
            shape = shapes[index]
            if kinds and shape.kind not in kinds:
                continue
            if painting.hit_area(shape, tolerance).contains(pos):
                return index
        return None

    def select(self, index: int | None) -> None:
        self.selected = index
        self._update_selection()

    def _update_selection(self) -> None:
        self.delete_button.setEnabled(self.selected is not None)
        self.canvas.update()

    def add_shape(self, shape: Shape) -> None:
        self.shapes = (*self.shapes, shape)
        self.commit()
        self.canvas.update()

    def replace_shape(self, index: int | None, shape: Shape) -> None:
        if index is None:
            return
        shapes = list(self.shapes)
        shapes[index] = shape
        self.shapes = tuple(shapes)
        self.canvas.update()

    def delete_selected(self) -> bool:
        if self.selected is None:
            return False
        shapes = list(self.shapes)
        del shapes[self.selected]
        self.selected = None
        self.shapes = tuple(shapes)
        self._update_selection()
        self.commit()
        return True

    # --- toolbar ------------------------------------------------------------------------

    def _on_tool(self) -> None:
        self._close_entry(keep=True)
        if self.tool_name != "select":
            self.select(None)

    def _on_color(self, _rgba) -> None:
        if self.selected is not None:
            shape = self.shapes[self.selected]
            self.replace_shape(self.selected, replace(shape, color=self.color()))
            self.commit()
        if self.editing is not None:  # changes the text being typed again
            shape = self.shapes[self.editing]
            self.replace_shape(self.editing, replace(shape, color=self.color()))
        self._style_entry()

    def _on_size(self) -> None:
        if self.selected is not None:
            shape = self.shapes[self.selected]
            self.replace_shape(self.selected, replace(shape, width=self.stroke_width(shape.kind)))
            self.commit()
        if self.editing is not None:
            shape = self.shapes[self.editing]
            self.replace_shape(self.editing, replace(shape, width=self.stroke_width("text")))
        self._style_entry()

    # --- text ---------------------------------------------------------------------------

    def new_text(self, point: tuple[float, float]) -> None:
        self._open_entry(point, "", None)

    def edit_text(self, index: int) -> None:
        shape = self.shapes[index]
        self.select(None)
        self._open_entry(shape.points[0], shape.text, index)

    def _open_entry(self, point: tuple[float, float], text: str, index: int | None) -> None:
        self._close_entry(keep=True)
        self.editing = index
        self._before_edit = self.editor.edit.shapes[index] if index is not None else None
        self._entry_point = point
        self.entry = TextEntry(
            self.canvas,
            finish=lambda: self._close_entry(keep=True),
            cancel=lambda: self._close_entry(keep=False),
        )
        self.entry.setText(text)
        self.entry.textChanged.connect(lambda _t: self._place_entry())
        self._style_entry()
        self.entry.show()
        self.entry.setFocus()
        self.canvas.update()

    def _entry_size(self) -> float:
        if self.editing is not None:
            return self.shapes[self.editing].width
        return self.stroke_width("text")

    def _style_entry(self) -> None:
        if self.entry is None:
            return
        font = painting.text_font(self._entry_size() * self.canvas.scale)
        self.entry.setFont(font)
        r, g, b = self.shapes[self.editing].color if self.editing is not None else self.color()
        self.entry.setStyleSheet(
            f"QLineEdit {{ background: rgba(255,255,255,40); color: rgb({r},{g},{b});"
            " border: 1px dashed rgba(127,127,127,200); padding: 0px; }"
        )
        self._place_entry()

    def _place_entry(self) -> None:
        if self.entry is None:
            return
        top_left = self.canvas.to_widget(QPointF(*self._entry_point))
        metrics = self.entry.fontMetrics()
        width = max(metrics.horizontalAdvance(self.entry.text() + "MM"), 40)
        self.entry.setGeometry(
            QRectF(top_left.x(), top_left.y(), width, metrics.height() + 4).toRect()
        )

    def finish_text(self) -> None:
        """Keep the text being typed (before saving or leaving the mode)."""
        self._close_entry(keep=True)

    def _close_entry(self, *, keep: bool) -> None:
        entry, self.entry = self.entry, None
        if entry is None:
            return
        index, self.editing = self.editing, None
        text = entry.text()
        entry.hide()
        entry.deleteLater()
        self.canvas.setFocus()
        if not keep:
            if index is not None and self._before_edit is not None:
                stored = list(self.editor.edit.shapes)
                stored[index] = self._before_edit  # the old text shows again
                self.editor.change(replace(self.editor.edit, shapes=tuple(stored)), record=False)
            return
        if index is not None:
            if text.strip():
                self.replace_shape(index, replace(self.shapes[index], text=text))
            else:
                shapes = list(self.shapes)
                del shapes[index]
                self.shapes = tuple(shapes)
            self.commit()
        elif text.strip():
            point = self._entry_point
            self.add_shape(Shape("text", (point,), self.color(), self.stroke_width("text"), text))
        self.canvas.update()

    def on_resize(self) -> None:
        if self.entry is not None:
            self._style_entry()
