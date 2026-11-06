"""Redact, in the Video Editor: hide areas of the picture for a stretch of time.

Drag on the picture to cover an area; it is hidden between In and Out (the whole video
without them), shown as a red band on the timeline. Click an area or its band to pick it, drag
it to move, drag its edges to resize, Delete removes it, "Use In-Out" gives it the marked
time. Styles: a black box (nothing left to recover), a heavy blur or big pixels. An area stays
where it is drawn; it does not follow a moving object. The copy carries no metadata.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QKeyEvent, QKeySequence, QMouseEvent, QPainter
from PySide6.QtWidgets import QButtonGroup, QLabel

from quince.backends import mediaedit
from quince.backends.mediaedit import Region
from quince.ui.editors import rects
from quince.ui.editors.base import Mode, separator, tool_button, toolbar_row
from quince.ui.editors.boxes import BoxGesture, handle_cursor, handle_under, paint_box, widget_box
from quince.ui.editors.canvas import CanvasTool, ImageCanvas
from quince.ui.editors.image._redact import NEW_MIN_PX, STYLES
from quince.ui.editors.media import marks as m

BLUR_PREVIEW = 10  # preview blur: shrink the area this many times and stretch it back


def paint_effect(painter: QPainter, canvas: ImageCanvas, region: Region) -> None:
    """A preview of the region's style on the frame shown."""
    x0, y0, x1, y1 = widget_box(canvas, region.box)
    target = QRectF(x0, y0, x1 - x0, y1 - y0)
    if region.style == "solid":
        painter.fillRect(target, QColor(0, 0, 0))
        return
    frame = canvas.image
    logical = canvas.logical_size
    if frame.isNull() or logical.width() <= 0:
        return
    s = frame.width() / logical.width()
    bx0, by0, bx1, by1 = region.box
    part = frame.copy(QRectF(bx0 * s, by0 * s, (bx1 - bx0) * s, (by1 - by0) * s).toAlignedRect())
    if part.isNull():
        return
    if region.style == "blur":
        size = QSize(max(1, part.width() // BLUR_PREVIEW), max(1, part.height() // BLUR_PREVIEW))
        small = part.scaled(size, Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)  # fmt: skip
        smooth = True
    else:
        bw, bh = mediaedit.pixel_blocks(part.width(), part.height())
        small = part.scaled(QSize(bw, bh), Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)  # fmt: skip
        smooth = False
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, smooth)
    painter.drawImage(target, small)
    painter.restore()


class RegionTool(CanvasTool):
    def __init__(self, editor: VideoRedactPart) -> None:
        self.editor = editor
        self.gesture: BoxGesture | None = None
        self.index: int | None = None

    @property
    def canvas(self) -> ImageCanvas:
        return self.editor.canvas

    def press(self, pos: QPointF, event: QMouseEvent) -> None:
        editor = self.editor
        point = (pos.x(), pos.y())
        bounds = editor.bounds
        selected = editor.selected
        if selected is not None and editor.regions[selected].visible(editor.position):
            box = editor.regions[selected].box
            handle = handle_under(self.canvas, box, event.position())
            if handle:
                self.gesture = BoxGesture("resize", point, box, bounds, handle)
                self.index = selected
                return
        index = editor.region_at(point)
        if index is not None:
            editor.select(index)
            self.gesture = BoxGesture("move", point, editor.regions[index].box, bounds)
            self.index = index
            return
        editor.select(None)
        start = rects.clamp_point(point, bounds)
        self.gesture = BoxGesture("new", start, (*start, *start), bounds)
        self.index = None

    def move(self, pos: QPointF, event: QMouseEvent) -> None:
        if self.gesture is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        box = self.gesture.update((pos.x(), pos.y()), None, max(2.0, 6 / self.canvas.scale))
        editor = self.editor
        if self.index is None:
            if rects.is_tiny(box, NEW_MIN_PX / self.canvas.scale):
                return
            start, end = editor.marks.span(editor.editor.duration)
            editor.regions = (*editor.regions, Region(box, start, end, editor.style_key()))
            self.index = len(editor.regions) - 1
            editor.select(self.index)
        else:
            editor.replace_region(self.index, replace(editor.regions[self.index], box=box))

    def release(self, pos: QPointF, event: QMouseEvent) -> None:
        gesture, self.gesture = self.gesture, None
        index, self.index = self.index, None
        if gesture is not None and index is not None:
            self.editor.commit()

    def key(self, event: QKeyEvent) -> bool:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            return self.editor.delete_selected()
        return False

    def cursor(self, pos: QPointF) -> Qt.CursorShape:
        if self.gesture is not None:
            if self.gesture.mode == "resize":
                return handle_cursor(self.gesture.handle)
            if self.gesture.mode == "move":
                return Qt.CursorShape.ClosedHandCursor
            return Qt.CursorShape.CrossCursor
        editor = self.editor
        if editor.selected is not None and editor.regions[editor.selected].visible(editor.position):
            box = editor.regions[editor.selected].box
            handle = handle_under(self.canvas, box, self.canvas.to_widget(pos))
            if handle:
                return handle_cursor(handle)
        if editor.region_at((pos.x(), pos.y())) is not None:
            return Qt.CursorShape.OpenHandCursor
        return Qt.CursorShape.CrossCursor

    def paint(self, painter: QPainter, canvas: ImageCanvas) -> None:
        editor = self.editor
        for n, region in enumerate(editor.regions):
            if region.visible(editor.position):
                paint_effect(painter, canvas, region)
                if n != editor.selected:
                    paint_box(painter, canvas, region.box, handles=False, dashed=True)
        if editor.selected is not None:
            region = editor.regions[editor.selected]
            visible = region.visible(editor.position)
            paint_box(painter, canvas, region.box, handles=visible, dashed=not visible)


class VideoRedactPart:
    def __init__(self, editor) -> None:
        self.editor = editor
        w, h = editor.source.size
        self.bounds = (0.0, 0.0, float(w), float(h))
        self.selected: int | None = None
        widget, row = toolbar_row()
        self.mode = Mode(
            "redact", "Redact", icon="quince-redact", tip="Hide areas of the picture",
            toolbar=widget, hint="Drag to cover an area; it is hidden between In and Out",
        )  # fmt: skip
        self.styles = QButtonGroup(widget)
        self.styles.setExclusive(True)
        for key, label, shortcut, tip in STYLES:
            button = tool_button(label, tip=f"{tip}  {shortcut}", checkable=True)
            button.setProperty("quince_style", key)
            button.setChecked(key == "solid")
            self.styles.addButton(button)
            row.addWidget(button)
            editor.mode_key(self.mode, shortcut, button.click)
        self.styles.buttonClicked.connect(lambda _b: self._on_style())
        row.addWidget(separator())
        self.span_button = tool_button("Use In–Out", tip="Hide the picked area between In and Out")
        self.span_button.clicked.connect(self.use_marks)
        row.addWidget(self.span_button)
        self.delete_button = tool_button("Delete", icon="edit-delete", tip="Delete  Del")
        self.delete_button.clicked.connect(self.delete_selected)
        row.addWidget(self.delete_button)
        row.addSpacing(10)
        self.label = QLabel()
        self.label.setEnabled(False)
        row.addWidget(self.label)
        row.addStretch(1)
        editor.mode_key(self.mode, QKeySequence(Qt.Key.Key_Delete), self.delete_selected)
        self.tool = RegionTool(self)
        self.mode.tool = self.tool
        self.select(None)

    # --- what RegionTool needs ----------------------------------------------------------

    @property
    def canvas(self) -> ImageCanvas:
        return self.editor.canvas

    @property
    def marks(self):
        return self.editor.marks

    @property
    def position(self) -> float:
        return self.editor.position

    @property
    def regions(self) -> tuple[Region, ...]:
        return self.editor.edit.regions

    @regions.setter
    def regions(self, regions) -> None:
        self.editor.change(replace(self.editor.edit, regions=tuple(regions)), record=False)

    def style_key(self) -> str:
        button = self.styles.checkedButton()
        return button.property("quince_style") if button is not None else "solid"

    def commit(self, merge: str | None = None) -> None:
        self.editor.change(self.editor.edit, merge=merge)

    def region_at(self, point: tuple[float, float]) -> int | None:
        regions = self.regions
        for index in range(len(regions) - 1, -1, -1):
            region = regions[index]
            if region.visible(self.position) and rects.contains(region.box, point):
                return index
        return None

    def select(self, index: int | None) -> None:
        self.selected = index
        self.delete_button.setEnabled(index is not None)
        self.span_button.setEnabled(index is not None)
        if index is not None:
            style = self.regions[index].style
            for button in self.styles.buttons():
                if button.property("quince_style") == style:
                    button.setChecked(True)
        self.sync()

    def replace_region(self, index: int, region: Region) -> None:
        regions = list(self.regions)
        regions[index] = region
        self.regions = tuple(regions)
        self.sync()

    def delete_selected(self) -> bool:
        if self.selected is None:
            return False
        regions = list(self.regions)
        del regions[self.selected]
        self.selected = None
        self.regions = tuple(regions)
        self.select(None)
        self.commit()
        return True

    def use_marks(self) -> None:
        if self.selected is None:
            return
        start, end = self.marks.span(self.editor.duration)
        region = replace(self.regions[self.selected], start=start, end=end)
        self.replace_region(self.selected, region)
        self.commit()

    def _on_style(self) -> None:
        if self.selected is not None:
            region = replace(self.regions[self.selected], style=self.style_key())
            self.replace_region(self.selected, region)
            self.commit()

    # --- timeline -----------------------------------------------------------------------

    def timeline_pressed(self, t: float) -> None:
        hits = [n for n, r in enumerate(self.regions) if r.start <= t <= r.end]
        if hits:
            self.select(self.selected if self.selected in hits else hits[-1])

    def sync(self) -> None:
        regions = self.regions
        if self.selected is not None and self.selected >= len(regions):
            self.selected = None
        if self.editor.mode is self.mode:
            self.editor.timeline.set_spans([(r.start, r.end) for r in regions], self.selected)
        count = len(regions)
        text = f"{count} area{'s' if count != 1 else ''}" if count else "No areas"
        if self.selected is not None:
            region = regions[self.selected]
            text += f" · picked: {m.clock(region.start)} – {m.clock(region.end)}"
        self.label.setText(text)
        self.canvas.update()
