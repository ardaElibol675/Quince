"""The page grid of the PDF Editor: every page as a picture in reading order, selected and
moved with the mouse or the keyboard, with files dropped in to add their pages.

Mouse: click selects a page, Ctrl+click adds or removes one, Shift+click selects a run, a
drag from empty space selects every page it touches; a click between pages clears the
selection (only a page and its number hit). Dragging selected pages moves them: a small
see-through copy follows the pointer (a stack with their number when several move), a line
shows where they go, and the grid scrolls at its edges. Ctrl+wheel zooms. Dropping PDFs
or pictures from the file manager adds their pages where the line is (always as a copy).

Keys (only while the grid has the focus, so typing elsewhere is never taken): arrows, Home,
End, Page Up/Down move (Shift extends the selection, Ctrl moves without selecting,
Ctrl+Space selects or unselects), Ctrl+A selects all. The editing keys are sent to the
editor as ``command`` names: Delete / Backspace ``delete``, R ``rotate_right``, Shift+R
``rotate_left``, Ctrl+D ``duplicate``, B ``blank``, Alt+←/→ ``earlier`` / ``later``,
Alt+Home / Alt+End ``first`` / ``last``.

``GridLayout`` is the pure geometry: where each page goes for a width, which page or gap is
under a point.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFontMetrics,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea, QToolTip, QWidget

from quince.backends import pdfedit
from quince.backends.pdfedit import Page, Source
from quince.ui.editors.pdf._thumbs import Thumbnails

MARGIN = 18
GAP = 30
LABEL = 30  # the page number under a page
HIT_PAD = 4  # a click this close to a page still hits it
SELECT_PAD = 5  # the selection is drawn this far around a page
GHOST_SIDE = 110  # the longer side of the picture following the pointer while moving pages
GHOST_OPACITY = 0.72
DEFAULT_CELL = 170
CELL_RANGE = (90, 480)
ZOOM_STEP = 1.15
EDGE = 40  # pixels at the top and bottom of the view that scroll while dragging
SCROLL_TICK_MS = 30
EMPTY_TEXT = "No pages — drop PDFs or pictures here, or press Ctrl+O to add pages"


class GridLayout:
    """Square places of ``cell`` pixels (and a label row) in rows across ``width``.

    With ``sizes`` (each page's shown size), a page is drawn fitted into its place, standing
    on its bottom edge with its number right below, and only the page and its number hit:
    the space around it belongs to no page, so a click there clears the selection.
    """

    def __init__(
        self,
        width: float,
        count: int,
        cell: int,
        *,
        margin: int = MARGIN,
        gap: int = GAP,
        sizes: Sequence[tuple[float, float]] | None = None,
    ) -> None:
        self.count = max(0, count)
        self.cell = cell
        self.sizes = sizes
        self.gap = gap
        self.step_x = cell + gap
        self.step_y = cell + LABEL + gap
        self.columns = max(1, int((width - 2 * margin + gap) // self.step_x))
        self.rows = math.ceil(self.count / self.columns)
        used = self.columns * self.step_x - gap
        self.left = max(float(margin), (width - used) / 2)
        self.top = float(margin)
        self.height = int(2 * margin + max(0, self.rows * self.step_y - gap))

    def box(self, index: int) -> tuple[float, float, float, float]:
        """The square of the page picture (x, y, w, h)."""
        row, col = divmod(index, self.columns)
        return (self.left + col * self.step_x, self.top + row * self.step_y, self.cell, self.cell)

    def cell_rect(self, index: int) -> tuple[float, float, float, float]:
        x, y, w, h = self.box(index)
        return (x, y, w, h + LABEL)

    def picture(self, index: int) -> tuple[float, float, float, float]:
        """Where the page itself is drawn: fitted into its place, on the place's bottom."""
        x, y, cell, _ = self.box(index)
        if self.sizes is None:
            return (x, y, cell, cell)
        width, height = self.sizes[index]
        scale = min(cell / max(width, 1.0), cell / max(height, 1.0))
        pw, ph = width * scale, height * scale
        return (x + (cell - pw) / 2, y + cell - ph, pw, ph)

    def hit_rect(self, index: int) -> tuple[float, float, float, float]:
        """What a click must touch to hit a page: the page and its number below it."""
        if self.sizes is None:
            return self.cell_rect(index)
        x, y, w, h = self.picture(index)
        return (x - HIT_PAD, y - HIT_PAD, w + 2 * HIT_PAD, h + HIT_PAD + LABEL)

    def index_at(self, x: float, y: float) -> int | None:
        col = math.floor((x - self.left + self.gap / 2) / self.step_x)
        row = math.floor((y - self.top + self.gap / 2) / self.step_y)
        if not (0 <= col < self.columns and 0 <= row < self.rows):
            return None
        index = row * self.columns + col
        if index >= self.count:
            return None
        hx, hy, hw, hh = self.hit_rect(index)
        return index if hx <= x < hx + hw and hy <= y < hy + hh else None

    def insertion_at(self, x: float, y: float) -> tuple[int, int]:
        """The gap nearest to a point: (index 0 … count, the row it is shown in)."""
        if self.count == 0:
            return 0, 0
        row = min(max(math.floor((y - self.top + self.gap / 2) / self.step_y), 0), self.rows - 1)
        slot = math.floor((x - self.left - self.cell / 2) / self.step_x) + 1
        slot = min(max(slot, 0), self.columns)
        return min(row * self.columns + slot, self.count), row

    def marker(self, index: int, row: int) -> tuple[float, float, float]:
        """The line showing gap ``index`` in ``row``: (x, top, height)."""
        col = index - row * self.columns
        if not 0 <= col <= self.columns:
            row, col = divmod(index, self.columns)
        x = self.left + col * self.step_x - self.gap / 2
        return (x, self.top + row * self.step_y, float(self.cell))

    def visible(self, top: float, bottom: float) -> range:
        first = max(0, math.floor((top - self.top) / self.step_y))
        last = math.floor((bottom - self.top) / self.step_y)
        return range(min(first * self.columns, self.count), min((last + 1) * self.columns,
                                                                 self.count))  # fmt: skip

    def touching(self, rect: tuple[float, float, float, float]) -> list[int]:
        """The pages whose hit area meets ``rect`` (x, y, w, h)."""
        x, y, w, h = rect
        found = []
        for i in self.visible(y - self.gap, y + h + self.gap):
            cx, cy, cw, ch = self.hit_rect(i)
            if cx < x + w and x < cx + cw and cy < y + h and y < cy + ch:
                found.append(i)
        return found

    def moved_index(self, index: int, key: str, page_rows: int = 1) -> int:
        """The page reached from ``index`` by a navigation key."""
        last = self.count - 1
        step = {
            "left": -1,
            "right": 1,
            "up": -self.columns,
            "down": self.columns,
            "page_up": -self.columns * page_rows,
            "page_down": self.columns * page_rows,
        }
        if key == "home":
            return 0
        if key == "end":
            return last
        target = index + step[key]
        if key in ("up", "page_up") and target < 0:
            return index % self.columns if key == "up" else 0
        return min(max(target, 0), last)


@dataclass(frozen=True)
class Overlay:
    """What Split shows on the pages: a label on the first page of each part, the pages left
    out dimmed, a cut line before each part."""

    labels: dict[int, str] = field(default_factory=dict)
    dimmed: frozenset[int] = frozenset()
    cuts: frozenset[int] = frozenset()


_NAV_KEYS = {
    Qt.Key.Key_Left: "left",
    Qt.Key.Key_Right: "right",
    Qt.Key.Key_Up: "up",
    Qt.Key.Key_Down: "down",
    Qt.Key.Key_Home: "home",
    Qt.Key.Key_End: "end",
    Qt.Key.Key_PageUp: "page_up",
    Qt.Key.Key_PageDown: "page_down",
}
_MOVE_KEYS = {
    Qt.Key.Key_Left: "earlier",
    Qt.Key.Key_Up: "earlier",
    Qt.Key.Key_Right: "later",
    Qt.Key.Key_Down: "later",
    Qt.Key.Key_Home: "first",
    Qt.Key.Key_End: "last",
}


def local_paths(mime) -> list[Path]:
    if not mime.hasUrls():
        return []
    return [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]


def describe(page: Page, sources: Sequence[Source]) -> str:
    if page.blank:
        text = "Blank page"
    else:
        source = sources[page.source]
        if source.data is not None and source.page_count == 1:
            text = source.path.name
        else:
            text = f"Page {page.number + 1} of {source.path.name}"
    if page.turns:
        text += f" · turned {page.turns * 90}°"
    return text


class PageGrid(QWidget):
    selection_changed = Signal()
    move_requested = Signal(object, int)  # uids in order, the gap they go to
    files_dropped = Signal(object, int)  # paths, the gap their pages go to
    command = Signal(str)
    menu_requested = Signal(QPoint)
    zoomed = Signal(int)

    def __init__(self, sources: Sequence[Source], thumbnails: Thumbnails) -> None:
        super().__init__()
        self.sources = sources
        self.thumbnails = thumbnails
        thumbnails.ready.connect(self.update)
        self.scroll_area: QScrollArea | None = None
        self.pages: tuple[Page, ...] = ()
        self._index: dict[int, int] = {}
        self.selected: set[int] = set()
        self.current: int | None = None
        self.anchor: int | None = None
        self.cell = DEFAULT_CELL
        self.overlay: Overlay | None = None
        self.marker: tuple[int, int] | None = None  # (gap, row) while moving or dropping
        self.layout_ = GridLayout(self.width(), 0, self.cell)
        self._press: tuple[QPointF, int, bool] | None = None  # where, page uid, select on release
        self._dragging = False
        self._band: tuple[QPointF, set[int]] | None = None
        self._band_rect: QRectF | None = None
        self._last_pos = QPointF()
        self._scroll = QTimer(self)
        self._scroll.setInterval(SCROLL_TICK_MS)
        self._scroll.timeout.connect(self._autoscroll)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.setMinimumSize(240, 200)

    # --- contents -----------------------------------------------------------------------

    def set_pages(
        self,
        pages: Sequence[Page],
        select: Iterable[int] | None = None,
        current: int | None = None,
    ) -> None:
        """Show ``pages``; ``select`` (uids) replaces the selection, else the pages still
        there stay selected."""
        self.pages = tuple(pages)
        self._index = {p.uid: i for i, p in enumerate(self.pages)}
        if select is not None:
            self.selected = set(select) & self._index.keys()
            if current is None and self.selected:
                current = min(self.selected, key=self._index.__getitem__)
            self.current = self.anchor = current if current in self._index else None
        else:
            self.selected &= self._index.keys()
            if self.current not in self._index:
                self.current = None
            if self.anchor not in self._index:
                self.anchor = self.current
        self._relayout()
        self.update()
        self.selection_changed.emit()
        if self.current is not None:
            self.ensure_visible(self._index[self.current])

    def index_of(self, uid: int) -> int | None:
        return self._index.get(uid)

    def selected_uids(self) -> list[int]:
        return [p.uid for p in self.pages if p.uid in self.selected]

    def selected_indices(self) -> list[int]:
        return [i for i, p in enumerate(self.pages) if p.uid in self.selected]

    def set_overlay(self, overlay: Overlay | None) -> None:
        self.overlay = overlay
        self.update()

    def set_cell(self, cell: int) -> None:
        cell = int(min(max(cell, CELL_RANGE[0]), CELL_RANGE[1]))
        if cell == self.cell:
            return
        self.cell = cell
        self._relayout()
        self.update()
        if self.current is not None:
            self.ensure_visible(self._index[self.current])
        self.zoomed.emit(cell)

    def _relayout(self) -> None:
        sizes = [pdfedit.shown_size(page, self.sources) for page in self.pages]
        self.layout_ = GridLayout(self.width(), len(self.pages), self.cell, sizes=sizes)
        self.setMinimumHeight(self.layout_.height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    def ensure_visible(self, index: int) -> None:
        if self.scroll_area is None or not 0 <= index < len(self.pages):
            return
        x, y, w, h = self.layout_.cell_rect(index)
        self.scroll_area.ensureVisible(int(x + w / 2), int(y + h / 2), int(w / 2) + GAP,
                                       int(h / 2) + GAP)  # fmt: skip

    # --- selection ----------------------------------------------------------------------

    def _set_selection(self, uids: Iterable[int], current: int | None = None) -> None:
        new = set(uids)
        changed = new != self.selected or (current is not None and current != self.current)
        self.selected = new
        if current is not None:
            self.current = current
        self.update()
        if changed:
            self.selection_changed.emit()

    def select_only(self, uid: int) -> None:
        self.anchor = uid
        self._set_selection({uid}, uid)

    def select_all(self) -> None:
        self._set_selection(self._index.keys())

    def toggle(self, uid: int) -> None:
        self.anchor = uid
        self._set_selection(self.selected ^ {uid}, uid)

    def select_run(self, uid: int, *, add: bool = False) -> None:
        start = self._index.get(self.anchor) if self.anchor is not None else None
        end = self._index[uid]
        if start is None:
            start = end
            self.anchor = uid
        lo, hi = sorted((start, end))
        run = {p.uid for p in self.pages[lo : hi + 1]}
        self._set_selection((self.selected | run) if add else run, uid)

    # --- keys ---------------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        if key == Qt.Key.Key_Escape and (self._dragging or self._band is not None):
            self._end_gesture()
            return
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.select_all()
            return
        name = None
        if alt and not ctrl and key in _MOVE_KEYS:
            name = _MOVE_KEYS[Qt.Key(key)]
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not (ctrl or alt):
            name = "delete"
        elif key == Qt.Key.Key_R and not (ctrl or alt):
            name = "rotate_left" if shift else "rotate_right"
        elif key == Qt.Key.Key_D and ctrl and not (shift or alt):
            name = "duplicate"
        elif key == Qt.Key.Key_B and not (ctrl or alt or shift):
            name = "blank"
        if name is not None:
            self.command.emit(name)
            return
        if key == Qt.Key.Key_Space and ctrl and self.current is not None:
            self.toggle(self.current)
            return
        if key in _NAV_KEYS and not alt and self.pages:
            self._navigate(_NAV_KEYS[Qt.Key(key)], shift=shift, ctrl=ctrl)
            return
        event.ignore()  # Enter saves, Esc closes: the window's keys

    def _navigate(self, key: str, *, shift: bool, ctrl: bool) -> None:
        here = self._index.get(self.current) if self.current is not None else None
        if here is None:
            target = 0 if key not in ("end",) else len(self.pages) - 1
        else:
            rows = 1
            if self.scroll_area is not None:
                rows = max(1, self.scroll_area.viewport().height() // self.layout_.step_y)
            target = self.layout_.moved_index(here, key, rows)
        uid = self.pages[target].uid
        if shift:
            self.select_run(uid, add=ctrl)
        elif ctrl:
            self.current = uid
            self.update()
        else:
            self.select_only(uid)
        self.ensure_visible(target)

    # --- mouse --------------------------------------------------------------------------

    def _page_at(self, pos: QPointF) -> int | None:
        index = self.layout_.index_at(pos.x(), pos.y())
        return None if index is None else self.pages[index].uid

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus()
        pos = event.position()
        self._last_pos = pos
        uid = self._page_at(pos)
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if event.button() == Qt.MouseButton.RightButton:
            if uid is not None and uid not in self.selected:
                self.select_only(uid)
            self.menu_requested.emit(event.globalPosition().toPoint())
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if uid is None:
            keep = set(self.selected) if (ctrl or shift) else set()
            if not (ctrl or shift):
                self._set_selection(())
            self._band = (pos, keep)
            return
        later = False
        if ctrl:
            self.toggle(uid)
        elif shift:
            self.select_run(uid)
        elif uid in self.selected:
            later = True  # a click (not a drag) on a selected page selects only it
            self.current = uid
            self.update()
        else:
            self.select_only(uid)
        self._press = (pos, uid, later)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        self._last_pos = pos
        if self._band is not None:
            self._update_band(pos)
            self._scroll.start()
            return
        if self._press is None:
            return
        start, uid, _later = self._press
        if not self._dragging:
            far = (pos - start).manhattanLength() >= QApplication.startDragDistance()
            if not far or uid not in self.selected:
                return
            self._dragging = True
            self.setCursor(Qt.CursorShape.DragMoveCursor)
        self.marker = self.layout_.insertion_at(pos.x(), pos.y())
        self._scroll.start()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._band is not None:
            self._end_gesture()
            return
        press, self._press = self._press, None
        if self._dragging:
            gap = self.marker[0] if self.marker is not None else None
            self._end_gesture()
            if gap is not None:
                self.move_requested.emit(self.selected_uids(), gap)
        elif press is not None and press[2]:
            self.select_only(press[1])

    def _end_gesture(self) -> None:
        self._dragging = False
        self._press = None
        self._band = None
        self._band_rect = None
        self.marker = None
        self._scroll.stop()
        self.unsetCursor()
        self.update()

    def _update_band(self, pos: QPointF) -> None:
        assert self._band is not None
        origin, keep = self._band
        rect = QRectF(origin, pos).normalized()
        self._band_rect = rect
        touched = self.layout_.touching((rect.x(), rect.y(), rect.width(), rect.height()))
        self._set_selection(keep | {self.pages[i].uid for i in touched})
        if touched:
            self.current = self.pages[touched[-1]].uid
        self.update()

    def _autoscroll(self) -> None:
        if self.scroll_area is None or not (self._dragging or self._band is not None):
            self._scroll.stop()
            return
        viewport = self.scroll_area.viewport()
        y = self.mapTo(viewport, self._last_pos.toPoint()).y()
        if y < EDGE:
            dy = -max(4, (EDGE - y) // 2)
        elif y > viewport.height() - EDGE:
            dy = max(4, (y - viewport.height() + EDGE) // 2)
        else:
            return
        bar = self.scroll_area.verticalScrollBar()
        before = bar.value()
        bar.setValue(before + dy)
        moved = bar.value() - before
        if not moved:
            return
        self._last_pos = QPointF(self._last_pos.x(), self._last_pos.y() + moved)
        if self._band is not None:
            self._update_band(self._last_pos)
        else:
            self.marker = self.layout_.insertion_at(self._last_pos.x(), self._last_pos.y())
            self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = event.angleDelta().y() / 120
            if steps:
                self.set_cell(round(self.cell * ZOOM_STEP**steps))
            event.accept()
            return
        event.ignore()  # the scroll area scrolls

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            uid = self._page_at(QPointF(event.pos()))  # a QHelpEvent
            if uid is not None:
                QToolTip.showText(event.globalPos(), describe(self.pages[self._index[uid]],
                                                              self.sources), self)  # fmt: skip
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    # --- files dropped in ---------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if local_paths(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)  # the source never deletes them
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if not local_paths(event.mimeData()):
            event.ignore()
            return
        pos = event.position()
        self.marker = self.layout_.insertion_at(pos.x(), pos.y())
        self.update()
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event) -> None:
        self.marker = None
        self.update()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = local_paths(event.mimeData())
        pos = event.position()
        gap = self.layout_.insertion_at(pos.x(), pos.y())[0]
        self.marker = None
        self.update()
        if not paths:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self.files_dropped.emit(paths, gap)

    # --- painting -----------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        palette = self.palette()
        painter.fillRect(event.rect(), palette.color(QPalette.ColorRole.Window).darker(112))
        if not self.pages:
            painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
            painter.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, EMPTY_TEXT)
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        area = event.rect()
        for index in self.layout_.visible(area.top() - LABEL, area.bottom() + LABEL):
            self._paint_page(painter, index)
        highlight = palette.color(QPalette.ColorRole.Highlight)
        if self.marker is not None:
            x, top, height = self.layout_.marker(*self.marker)
            painter.setPen(QPen(highlight, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(x, top - 4), QPointF(x, top + height + 4))
        if self._band_rect is not None:
            fill = QColor(highlight)
            fill.setAlpha(50)
            painter.setPen(QPen(highlight, 1))
            painter.setBrush(fill)
            painter.drawRect(self._band_rect)
        if self._dragging and self._press is not None:
            self._paint_ghost(painter)

    def _draw_page(self, painter: QPainter, page: Page, target: QRectF) -> None:
        """The page's picture (or a blank sheet) in ``target``, turned as it will be."""
        painter.fillRect(target, QColor(255, 255, 255))
        if page.blank:
            painter.setPen(QColor(0, 0, 0, 70))
            painter.drawText(target, Qt.AlignmentFlag.AlignCenter, "Blank")
            return
        pixels = max(target.width(), target.height()) * self.devicePixelRatioF()
        image = self.thumbnails.image(page.source, page.number, pixels)
        if image is not None:
            pw, ph = target.width(), target.height()
            upright = (pw, ph) if page.turns % 2 == 0 else (ph, pw)
            painter.save()
            painter.translate(target.center())
            painter.rotate(90 * page.turns)
            painter.drawImage(QRectF(-upright[0] / 2, -upright[1] / 2, *upright), image)
            painter.restore()
        elif self.thumbnails.failed(page.source, page.number):
            painter.setPen(QColor(180, 40, 40))
            painter.drawText(target, Qt.AlignmentFlag.AlignCenter, "Cannot show")

    def _paint_page(self, painter: QPainter, index: int) -> None:
        page = self.pages[index]
        palette = self.palette()
        highlight = palette.color(QPalette.ColorRole.Highlight)
        x, y, cell, _ = self.layout_.box(index)
        target = QRectF(*self.layout_.picture(index))
        chosen = page.uid in self.selected
        if chosen:
            fill = QColor(highlight)
            fill.setAlpha(80)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            pad = SELECT_PAD
            painter.drawRoundedRect(target.adjusted(-pad, -pad, pad, pad), 6, 6)
        painter.fillRect(target.translated(1.5, 2.0), QColor(0, 0, 0, 50))
        self._draw_page(painter, page, target)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(highlight, 2) if chosen else QPen(QColor(0, 0, 0, 60), 1))
        painter.drawRect(target)
        overlay = self.overlay
        if overlay is not None and index in overlay.dimmed:
            veil = palette.color(QPalette.ColorRole.Window)
            veil.setAlpha(170)
            painter.fillRect(target, veil)
        self._paint_number(painter, index, page, target, chosen)
        if page.uid == self.current and self.hasFocus():
            pen = QPen(palette.color(QPalette.ColorRole.WindowText), 1, Qt.PenStyle.DotLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pad = SELECT_PAD + 3
            painter.drawRoundedRect(target.adjusted(-pad, -pad, pad, pad), 7, 7)
        if overlay is not None:
            self._paint_overlay(painter, index, x, y, cell, target, overlay)

    def _paint_number(self, painter, index: int, page: Page, target: QRectF, chosen: bool):
        palette = self.palette()
        text = str(index + 1)
        if page.turns:
            text += f"  ↻{page.turns * 90}°"
        metrics = QFontMetrics(painter.font())
        room = self.layout_.cell + GAP - 8
        text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(room))
        width = min(room, metrics.horizontalAdvance(text) + 14)
        pill = QRectF(target.center().x() - width / 2, target.bottom() + SELECT_PAD + 5, width,
                      metrics.height() + 2)  # fmt: skip
        if chosen:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(palette.color(QPalette.ColorRole.Highlight))
            painter.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
            painter.setPen(palette.color(QPalette.ColorRole.HighlightedText))
        else:
            painter.setPen(palette.color(QPalette.ColorRole.WindowText))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_ghost(self, painter: QPainter) -> None:
        """A small, see-through copy of the page being moved, next to the pointer; a stack
        with the number of pages when several move."""
        assert self._press is not None
        index = self._index.get(self._press[1])
        if index is None:
            return
        page = self.pages[index]
        count = len(self.selected)
        width, height = pdfedit.shown_size(page, self.sources)
        side = min(GHOST_SIDE, self.layout_.cell * 0.75)
        scale = side / max(width, height, 1.0)
        front = QRectF(self._last_pos + QPointF(18, 14), QSizeF(width * scale, height * scale))
        painter.save()
        painter.setOpacity(GHOST_OPACITY)
        for depth in range(min(count, 3) - 1, 0, -1):  # the sheets behind, if several
            sheet = front.translated(5 * depth, 5 * depth)
            painter.fillRect(sheet, QColor(255, 255, 255))
            painter.setPen(QPen(QColor(0, 0, 0, 90), 1))
            painter.drawRect(sheet)
        painter.fillRect(front.translated(2, 3), QColor(0, 0, 0, 60))
        self._draw_page(painter, page, front)
        highlight = self.palette().color(QPalette.ColorRole.Highlight)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(highlight, 2))
        painter.drawRect(front)
        painter.restore()
        if count > 1:
            metrics = QFontMetrics(painter.font())
            text = str(count)
            d = max(metrics.height() + 6, metrics.horizontalAdvance(text) + 12)
            badge = QRectF(front.right() - d / 2, front.top() - d / 2, d, d)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(highlight)
            painter.drawEllipse(badge)
            painter.setPen(self.palette().color(QPalette.ColorRole.HighlightedText))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_overlay(self, painter, index, x, y, cell, target, overlay: Overlay) -> None:
        accent = self.palette().color(QPalette.ColorRole.Highlight)
        if index in overlay.cuts:
            painter.setPen(QPen(accent, 3, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x - GAP / 2, y - 4), QPointF(x - GAP / 2, y + cell + 4))
        label = overlay.labels.get(index)
        if label:
            metrics = QFontMetrics(painter.font())
            w = metrics.horizontalAdvance(label) + 12
            badge = QRectF(target.left() + 4, target.top() + 4, w, metrics.height() + 4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(self.palette().color(QPalette.ColorRole.HighlightedText))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)


class GridView(QScrollArea):
    """The grid in a scroll area; the focus goes to the grid."""

    def __init__(self, grid: PageGrid) -> None:
        super().__init__()
        self.grid = grid
        grid.scroll_area = self
        self.setWidget(grid)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusProxy(grid)
        self.verticalScrollBar().setSingleStep(40)
