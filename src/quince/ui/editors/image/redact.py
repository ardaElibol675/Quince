"""Redact image: hide areas behind a black box, a heavy blur or big pixels.

Drag to cover an area; click an area to pick it, drag it to move, drag its edges to resize,
Delete removes it. The style buttons set the style of new areas and of the picked one.
The saved copy (``name-redacted``) carries no EXIF metadata: camera notes may hold a preview
of the original picture. A black box is the only style that leaves nothing to recover.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence, QMouseEvent, QPainter, QShortcut
from PySide6.QtWidgets import QButtonGroup

from quince.backends import imageedit
from quince.backends.imageedit import Redaction
from quince.core.operations import JobRequest
from quince.ui.editors import rects
from quince.ui.editors.base import debounced, separator, tool_button
from quince.ui.editors.boxes import BoxGesture, handle_cursor, handle_under, paint_box
from quince.ui.editors.canvas import CanvasTool, ImageCanvas
from quince.ui.editors.image._common import ImageEditor

STYLES = (
    ("solid", "Black Box", "1", "Covers the area completely (safest)"),
    ("blur", "Blur", "2", "Blurs the area heavily"),
    ("pixelate", "Pixelate", "3", "Turns the area into large blocks"),
)
NEW_MIN_PX = 4
NOTE = "EXIF metadata was removed from the redacted copy"


class RedactTool(CanvasTool):
    def __init__(self, editor: RedactEditor) -> None:
        self.editor = editor
        self.gesture: BoxGesture | None = None
        self.index: int | None = None  # the area being dragged

    @property
    def canvas(self) -> ImageCanvas:
        return self.editor.canvas

    def press(self, pos: QPointF, event: QMouseEvent) -> None:
        editor = self.editor
        point = (pos.x(), pos.y())
        bounds = editor.bounds
        selected = editor.selected
        if selected is not None:
            box = editor.items[selected].box
            handle = handle_under(self.canvas, box, event.position())
            if handle:
                self.gesture, self.index = (
                    BoxGesture("resize", point, box, bounds, handle),
                    selected,
                )
                return
        index = editor.item_at(point)
        if index is not None:
            editor.select(index)
            self.gesture = BoxGesture("move", point, editor.items[index].box, bounds)
            self.index = index
            return
        editor.select(None)
        start = rects.clamp_point(point, bounds)
        self.gesture = BoxGesture("new", start, (*start, *start), bounds)
        self.index = None

    def move(self, pos: QPointF, event: QMouseEvent) -> None:
        if self.gesture is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        box = self.gesture.update((pos.x(), pos.y()), None, max(1.0, 6 / self.canvas.scale))
        editor = self.editor
        if self.index is None:
            if rects.is_tiny(box, NEW_MIN_PX / self.canvas.scale):
                return
            editor.items = (*editor.items, Redaction(box, editor.style_key()))
            self.index = len(editor.items) - 1
            editor.select(self.index)
        else:
            editor.replace_item(self.index, replace(editor.items[self.index], box=box))
        editor.schedule_refresh()

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
            return (
                Qt.CursorShape.ClosedHandCursor
                if self.gesture.mode == "move"
                else (Qt.CursorShape.CrossCursor)
            )
        editor = self.editor
        if editor.selected is not None:
            handle = handle_under(
                self.canvas, editor.items[editor.selected].box, self.canvas.to_widget(pos)
            )
            if handle:
                return handle_cursor(handle)
        if editor.item_at((pos.x(), pos.y())) is not None:
            return Qt.CursorShape.OpenHandCursor
        return Qt.CursorShape.CrossCursor

    def paint(self, painter: QPainter, canvas: ImageCanvas) -> None:
        editor = self.editor
        for n, item in enumerate(editor.items):
            if n != editor.selected:
                paint_box(painter, canvas, item.box, handles=False, dashed=True)
        if editor.selected is not None:
            paint_box(painter, canvas, editor.items[editor.selected].box)


class RedactEditor(ImageEditor):
    name = "image.redact"
    title = "Redact Image"
    save_label = "Save Redacted"

    def __init__(self, selection, data, parent=None) -> None:
        super().__init__(selection, data, parent)
        w, h = self.image_size
        self.bounds = (0.0, 0.0, float(w), float(h))
        self.items: tuple[Redaction, ...] = ()
        self.selected: int | None = None
        self._refresh = debounced(self, self.refresh, 10)

        self.styles = QButtonGroup(self)
        self.styles.setExclusive(True)
        for key, label, shortcut, tip in STYLES:
            button = tool_button(label, tip=f"{tip}  {shortcut}", checkable=True)
            button.setProperty("quince_style", key)
            button.setChecked(key == "solid")
            self.styles.addButton(button)
            self.add_tool(button)
            QShortcut(
                QKeySequence(shortcut),
                self,
                lambda b=button: b.click(),
                context=Qt.ShortcutContext.WindowShortcut,
            )
        self.styles.buttonClicked.connect(lambda _b: self._on_style())
        self.add_tool(separator())
        self.delete_button = tool_button("Delete", icon="edit-delete", tip="Delete  Del")
        self.delete_button.clicked.connect(self.delete_selected)
        self.add_tool(self.delete_button)
        self.toolbar.addStretch(1)

        self.canvas.tool = RedactTool(self)
        self.set_hint("Drag to cover an area · Del removes the picked one")
        self.refresh()
        self.select(None)
        self.start_history()

    def style_key(self) -> str:
        button = self.styles.checkedButton()
        return button.property("quince_style") if button is not None else "solid"

    # --- state ------------------------------------------------------------------------

    def state(self) -> tuple[Redaction, ...]:
        return self.items

    def set_state(self, state: tuple[Redaction, ...]) -> None:
        self.items = state
        if self.selected is not None and self.selected >= len(state):
            self.selected = None
        self.select(self.selected)
        self.refresh()

    def can_save(self) -> bool:
        return bool(self.items) and super().can_save()

    def item_at(self, point: tuple[float, float]) -> int | None:
        for index in range(len(self.items) - 1, -1, -1):
            if rects.contains(self.items[index].box, point):
                return index
        return None

    def select(self, index: int | None) -> None:
        self.selected = index
        self.delete_button.setEnabled(index is not None)
        if index is not None:
            style = self.items[index].style
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
        self.items = tuple(items)
        self.select(None)
        self.refresh()
        self.commit()
        return True

    def _on_style(self) -> None:
        if self.selected is not None:
            self.replace_item(
                self.selected, replace(self.items[self.selected], style=self.style_key())
            )
            self.refresh()
            self.commit()

    # --- result -------------------------------------------------------------------------

    def schedule_refresh(self) -> None:
        self._refresh.start()

    def refresh(self) -> None:
        scale = self.loaded.scale
        preview = imageedit.redact(self.loaded.preview, [i.scaled(scale) for i in self.items])
        self.show_preview(preview, self.image_size)
        count = len(self.items)
        self.info.setText(f"{count} area{'s' if count != 1 else ''}" if count else "")

    def build_job(self) -> JobRequest:
        items = tuple(self.items)
        return self.job(
            lambda im: imageedit.redact(im, items), "redacted", keep_exif=False, note=NOTE
        )


EDITOR = RedactEditor
