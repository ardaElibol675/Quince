"""Redact, in the Image Editor: hide areas behind a black box, a heavy blur or big pixels.

Drag to cover an area; click an area to pick it, drag it to move, drag its edges to resize,
Delete removes it. The style buttons (keys 1, 2, 3 in this mode) set the style of new areas
and of the picked one. A redacted picture is saved without EXIF metadata: camera notes may
hold a preview of the original. A black box is the only style that leaves nothing to recover.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import QButtonGroup

from quince.backends.imageedit import Redaction
from quince.ui.editors import rects
from quince.ui.editors.base import Mode, separator, tool_button, toolbar_row
from quince.ui.editors.boxes import BoxGesture, handle_cursor, handle_under, paint_box
from quince.ui.editors.canvas import CanvasTool, ImageCanvas

STYLES = (
    ("solid", "Black Box", "1", "Covers the area completely (safest)"),
    ("blur", "Blur", "2", "Blurs the area heavily"),
    ("pixelate", "Pixelate", "3", "Turns the area into large blocks"),
)
NEW_MIN_PX = 4


class RedactTool(CanvasTool):
    def __init__(self, part: RedactPart) -> None:
        self.editor = part
        self.gesture: BoxGesture | None = None
        self.index: int | None = None  # the area being dragged

    @property
    def canvas(self) -> ImageCanvas:
        return self.editor.canvas

    def press(self, pos: QPointF, event: QMouseEvent) -> None:
        part = self.editor
        point = (pos.x(), pos.y())
        bounds = part.bounds
        selected = part.selected
        if selected is not None:
            box = part.items[selected].box
            handle = handle_under(self.canvas, box, event.position())
            if handle:
                self.gesture = BoxGesture("resize", point, box, bounds, handle)
                self.index = selected
                return
        index = part.item_at(point)
        if index is not None:
            part.select(index)
            self.gesture = BoxGesture("move", point, part.items[index].box, bounds)
            self.index = index
            return
        part.select(None)
        start = rects.clamp_point(point, bounds)
        self.gesture = BoxGesture("new", start, (*start, *start), bounds)
        self.index = None

    def move(self, pos: QPointF, event: QMouseEvent) -> None:
        if self.gesture is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        box = self.gesture.update((pos.x(), pos.y()), None, max(1.0, 6 / self.canvas.scale))
        part = self.editor
        if self.index is None:
            if rects.is_tiny(box, NEW_MIN_PX / self.canvas.scale):
                return
            part.items = (*part.items, Redaction(box, part.style_key()))
            self.index = len(part.items) - 1
            part.select(self.index)
        else:
            part.replace_item(self.index, replace(part.items[self.index], box=box))

    def release(self, pos: QPointF, event: QMouseEvent) -> None:
        gesture, self.gesture = self.gesture, None
        index, self.index = self.index, None
        if gesture is None or index is None:
            return
        self.editor.commit()

    def key(self, event: QKeyEvent) -> bool:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            return self.editor.delete_selected()
        if event.key() == Qt.Key.Key_Escape and self.editor.selected is not None:
            self.editor.select(None)
            return True
        return False

    def cursor(self, pos: QPointF) -> Qt.CursorShape:
        if self.gesture is not None:
            if self.gesture.mode == "resize":
                return handle_cursor(self.gesture.handle)
            if self.gesture.mode == "move":
                return Qt.CursorShape.ClosedHandCursor
            return Qt.CursorShape.CrossCursor
        part = self.editor
        if part.selected is not None:
            box = part.items[part.selected].box
            handle = handle_under(self.canvas, box, self.canvas.to_widget(pos))
            if handle:
                return handle_cursor(handle)
        if part.item_at((pos.x(), pos.y())) is not None:
            return Qt.CursorShape.OpenHandCursor
        return Qt.CursorShape.CrossCursor

    def paint(self, painter: QPainter, canvas: ImageCanvas) -> None:
        part = self.editor
        painter.save()
        painter.setClipRect(canvas.image_rect().adjusted(-6, -6, 6, 6))
        items = part.items
        for n, item in enumerate(items):
            if n != part.selected:
                paint_box(painter, canvas, item.box, handles=False, dashed=True)
        if part.selected is not None and part.selected < len(items):
            paint_box(painter, canvas, items[part.selected].box)
        painter.restore()


class RedactPart:
    """The Redact mode. On the canvas, areas are in pixels of the cropped and turned
    picture; the edit keeps them in pixels of the original."""

    def __init__(self, editor) -> None:
        self.editor = editor
        self.selected: int | None = None
        widget, row = toolbar_row()
        self.styles = QButtonGroup(widget)
        self.styles.setExclusive(True)
        self.mode = Mode(
            "redact",
            "Redact",
            icon="quince-redact",
            tip="Hide areas",
            toolbar=widget,
            hint="Drag to cover an area · Del removes the picked one",
        )
        for key, label, shortcut, tip in STYLES:
            button = tool_button(label, tip=f"{tip}  {shortcut}", checkable=True)
            button.setProperty("quince_style", key)
            button.setChecked(key == "solid")
            self.styles.addButton(button)
            row.addWidget(button)
            editor.mode_key(self.mode, shortcut, button.click)
        self.styles.buttonClicked.connect(lambda _b: self._on_style())
        row.addWidget(separator())
        self.delete_button = tool_button("Delete", icon="edit-delete", tip="Delete  Del")
        self.delete_button.clicked.connect(self.delete_selected)
        row.addWidget(self.delete_button)
        row.addStretch(1)
        self.tool = RedactTool(self)
        self.mode.tool = self.tool
        self.select(None)

    @property
    def canvas(self) -> ImageCanvas:
        return self.editor.canvas

    def _geometry(self):
        return self.editor.edit.geometry(self.editor.image_size)

    @property
    def bounds(self):
        return self._geometry().bounds

    @property
    def items(self) -> tuple[Redaction, ...]:
        geometry = self._geometry()
        stored = self.editor.edit.redactions
        return tuple(replace(i, box=geometry.forward_box(i.box)) for i in stored)

    @items.setter
    def items(self, items) -> None:
        geometry = self._geometry()
        stored = tuple(replace(i, box=geometry.inverse_box(i.box)) for i in items)
        self.editor.change(replace(self.editor.edit, redactions=stored), record=False)

    def style_key(self) -> str:
        button = self.styles.checkedButton()
        return button.property("quince_style") if button is not None else "solid"

    def commit(self, merge: str | None = None) -> None:
        self.editor.change(self.editor.edit, merge=merge)

    def sync(self) -> None:
        if self.selected is not None and self.selected >= len(self.editor.edit.redactions):
            self.selected = None
        self.select(self.selected)

    def item_at(self, point: tuple[float, float]) -> int | None:
        items = self.items
        for index in range(len(items) - 1, -1, -1):
            if rects.contains(items[index].box, point):
                return index
        return None

    def select(self, index: int | None) -> None:
        self.selected = index
        self.delete_button.setEnabled(index is not None)
        if index is not None:
            style = self.editor.edit.redactions[index].style
            for button in self.styles.buttons():
                if button.property("quince_style") == style:
                    button.setChecked(True)
        self.canvas.update()

    def replace_item(self, index: int, item: Redaction) -> None:
        items = list(self.items)
        items[index] = item
        self.items = tuple(items)

    def delete_selected(self) -> bool:
        if self.selected is None:
            return False
        items = list(self.items)
        del items[self.selected]
        self.selected = None
        self.items = tuple(items)
        self.select(None)
        self.commit()
        return True

    def _on_style(self) -> None:
        if self.selected is not None:
            item = self.items[self.selected]
            self.replace_item(self.selected, replace(item, style=self.style_key()))
            self.commit()
