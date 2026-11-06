"""The Float shelf: a small always-on-top window that holds files for later.

- Drop files on it (from Nautilus or anything else); they are kept as references, the files
  themselves are not touched. Drops are answered with Copy, so the source never deletes them.
- Drag items out of it into another app. The drag offers **Copy only**, so Nautilus copies
  instead of moving (Hard Rule 4). "Drag all" drags every item.
- Remove items (Delete, context menu), clear the shelf, or close it (which also empties it).
- Double-click opens an item with its default application.

Items that disappear from disk drop off the shelf by themselves. The shelf is not kept when
Quince restarts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QFileInfo, QMimeData, QPoint, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QCursor,
    QDrag,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileIconProvider,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from quince.integrations.files import show_in_files
from quince.ui.shelf.model import ShelfItems
from quince.ui.theme.colors import WheelColors, wheel_colors

log = logging.getLogger(__name__)

SHELF_WIDTH = 280
ROW_HEIGHT = 34
MAX_VISIBLE_ROWS = 8
EMPTY_HEIGHT = 90
SCREEN_MARGIN = 24
PRUNE_MS = 2000
PATH_ROLE = Qt.ItemDataRole.UserRole


def _rgba(color) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"


def mime_for(paths: Sequence[Path]) -> QMimeData:
    """Drag data for files: ``text/uri-list`` only."""
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    return data


def dropped_paths(data: QMimeData) -> list[str]:
    return [u.toLocalFile() for u in data.urls() if u.isLocalFile() and u.toLocalFile()]


def default_open(path: Path) -> None:
    from quince.backends import desktop
    from quince.core.jobs import JobError

    try:
        desktop.spawn(["xdg-open", str(path)])
    except JobError as exc:
        log.warning("cannot open %s: %s", path, exc.reason)


class _ShelfList(QListWidget):
    """The item list; a drag source offering Copy only."""

    drag_requested = Signal(list)  # paths

    def __init__(self) -> None:
        super().__init__()
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setIconSize(QSize(24, 24))
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setFrameShape(QFrame.Shape.NoFrame)

    def selected_paths(self) -> list[Path]:
        rows = sorted(self.row(i) for i in self.selectedItems())
        return [Path(self.item(r).data(PATH_ROLE)) for r in rows]

    def startDrag(self, supported_actions) -> None:
        paths = self.selected_paths()
        if paths:
            self.drag_requested.emit(paths)


class _DragHandle(QLabel):
    """ "Drag all": dragging this label drags every item."""

    drag_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("handle")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._press: QPoint | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press is None:
            return
        moved = (event.position().toPoint() - self._press).manhattanLength()
        if moved >= QApplication.startDragDistance():
            self._press = None
            self.drag_requested.emit()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._press = None


class _Header(QWidget):
    """The title bar; dragging it moves the window."""

    def mousePressEvent(self, event: QMouseEvent) -> None:
        handle = self.window().windowHandle()
        if event.button() == Qt.MouseButton.LeftButton and handle is not None:
            handle.startSystemMove()


class ShelfWindow(QWidget):
    changed = Signal(int)  # item count

    def __init__(
        self,
        *,
        scheme: str = "system",
        open_file: Callable[[Path], None] = default_open,
        show_files: Callable[[Sequence[Path]], None] = show_in_files,
        start_drag: Callable[[QWidget, list[Path]], Qt.DropAction] | None = None,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowTitle("Float")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.items = ShelfItems()
        self._open_file = open_file
        self._show_files = show_files
        self._start_drag = start_drag or self._exec_drag
        self._placed = False
        self._dragging = False
        self._icons = QFileIconProvider()

        self.frame = QFrame(self)
        self.frame.setObjectName("shelf")
        self.scheme = scheme
        self._apply_colors(wheel_colors(scheme))
        app = QGuiApplication.instance()
        if app is not None:
            app.styleHints().colorSchemeChanged.connect(
                lambda *_a: self._apply_colors(wheel_colors(self.scheme))
            )

        header = _Header()
        self.title = QLabel("Float")
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.count = QLabel()
        self.count.setObjectName("dim")
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear)
        self.close_button = QPushButton("\u00d7")  # multiplication sign as a close glyph
        self.close_button.setObjectName("close")
        self.close_button.setToolTip("Close and empty the shelf")
        self.close_button.clicked.connect(self.close_shelf)
        top = QHBoxLayout(header)
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.title)
        top.addWidget(self.count, 1)
        top.addWidget(self.clear_button)
        top.addWidget(self.close_button)

        self.list = _ShelfList()
        self.list.drag_requested.connect(self.drag_out)
        self.list.itemDoubleClicked.connect(
            lambda item: self._open_file(Path(item.data(PATH_ROLE)))
        )
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.empty = QLabel("Drop files here")
        self.empty.setObjectName("dim")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setFixedHeight(EMPTY_HEIGHT)
        self.handle = _DragHandle()
        self.handle.drag_requested.connect(lambda: self.drag_out(self.items.paths))

        inner = QVBoxLayout(self.frame)
        inner.setContentsMargins(12, 8, 8, 10)
        inner.setSpacing(6)
        inner.addWidget(header)
        inner.addWidget(self.list)
        inner.addWidget(self.empty)
        inner.addWidget(self.handle)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.frame)

        self._prune_timer = QTimer(self)
        self._prune_timer.setInterval(PRUNE_MS)
        self._prune_timer.timeout.connect(self.prune)
        self._refresh()

    # --- public API ----------------------------------------------------------

    @property
    def paths(self) -> list[Path]:
        return self.items.paths

    def add(self, paths: Sequence[str | Path]) -> list[Path]:
        """Put ``paths`` on the shelf and show it."""
        added = self.items.add(paths)
        if added:
            log.info("float: added %s", ", ".join(str(p) for p in added))
        self._refresh()
        self.show_shelf()
        return added

    def remove(self, paths: Sequence[str | Path]) -> None:
        if self.items.remove(paths):
            self._refresh()

    def remove_selected(self) -> None:
        self.remove(self.list.selected_paths())

    def clear(self) -> None:
        self.items.clear()
        self._refresh()

    def prune(self) -> None:
        if not self._dragging and (gone := self.items.prune()):
            log.info("float: %s no longer exist", ", ".join(str(p) for p in gone))
            self._refresh()

    def show_shelf(self) -> None:
        if not self._placed:
            self._place()
        self.show()
        self.raise_()
        self._prune_timer.start()

    def close_shelf(self) -> None:
        self.clear()
        self.hide()
        self._prune_timer.stop()

    def drag_out(self, paths: Sequence[Path]) -> None:
        self.prune()
        paths = [p for p in paths if p in self.items]
        if not paths:
            return
        self._dragging = True
        try:
            action = self._start_drag(self.list, list(paths))
            log.info("float: dragged out %d item(s): %s", len(paths), action)
        finally:
            self._dragging = False

    # --- internals ---------------------------------------------------------------

    def _exec_drag(self, source: QWidget, paths: list[Path]) -> Qt.DropAction:
        drag = QDrag(source)
        drag.setMimeData(mime_for(paths))
        icon = self._icons.icon(QFileInfo(str(paths[0])))
        drag.setPixmap(icon.pixmap(QSize(32, 32), self.devicePixelRatioF()))
        # Copy only: the target must never move (or delete) the originals.
        return drag.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)

    def _apply_colors(self, colors: WheelColors) -> None:
        self.frame.setStyleSheet(
            f"""
            #shelf {{ background: {_rgba(colors.ring)};
                      border: 1px solid {_rgba(colors.ring_border)}; border-radius: 12px; }}
            QLabel {{ color: {_rgba(colors.text)}; background: transparent; }}
            QLabel#dim {{ color: {_rgba(colors.text_dim)}; }}
            QLabel#handle {{ color: {_rgba(colors.text_dim)};
                             border: 1px dashed {_rgba(colors.ring_border)}; border-radius: 6px;
                             padding: 4px; }}
            QPushButton {{ color: {_rgba(colors.text)}; background: transparent;
                           border: 1px solid {_rgba(colors.ring_border)}; border-radius: 6px;
                           padding: 2px 8px; }}
            QPushButton:hover {{ background: {_rgba(colors.separator)}; }}
            QPushButton#close {{ border: none; padding: 0 4px; font-size: 15px; }}
            QListWidget {{ background: transparent; color: {_rgba(colors.text)};
                           outline: none; }}
            QListWidget::item {{ border-radius: 6px; padding: 2px; }}
            QListWidget::item:selected {{ background: {_rgba(colors.accent)};
                                          color: {_rgba(colors.accent_text)}; }}
            QListWidget::item:hover:!selected {{ background: {_rgba(colors.separator)}; }}
            """
        )

    def _refresh(self) -> None:
        selected = set(self.list.selected_paths())
        self.list.clear()
        for path in self.items:
            item = QListWidgetItem(self._icons.icon(QFileInfo(str(path))), path.name or str(path))
            item.setData(PATH_ROLE, str(path))
            item.setToolTip(str(path))
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            self.list.addItem(item)
            item.setSelected(path in selected)
        count = len(self.items)
        self.count.setText("" if count == 0 else f"· {count} item{'s' if count != 1 else ''}")
        self.list.setVisible(count > 0)
        self.empty.setVisible(count == 0)
        self.handle.setVisible(count > 1)
        self.handle.setText(f"Drag all {count}")
        self.clear_button.setEnabled(count > 0)
        self.list.setFixedHeight(min(count, MAX_VISIBLE_ROWS) * ROW_HEIGHT + 4)
        self.setFixedWidth(SHELF_WIDTH)
        self.layout().activate()
        self.frame.layout().activate()
        self.setFixedHeight(self.sizeHint().height())
        self.changed.emit(count)

    def _place(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = area.right() - SCREEN_MARGIN - self.width()
        y = area.center().y() - self.height() // 2
        self.move(x, y)
        self._placed = True

    def _context_menu(self, pos: QPoint) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        if not item.isSelected():
            self.list.clearSelection()
            item.setSelected(True)
        paths = self.list.selected_paths()
        menu = QMenu(self)
        menu.addAction("Open", lambda: [self._open_file(p) for p in paths])
        menu.addAction("Show in Files", lambda: self._show_files(paths))
        menu.addSeparator()
        menu.addAction("Remove from Shelf", lambda: self.remove(paths))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    # --- events ----------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_selected()
            return
        super().keyPressEvent(event)

    def _accept_files(self, event: QDragEnterEvent | QDragMoveEvent | QDropEvent) -> bool:
        if event.source() is self.list or not dropped_paths(event.mimeData()):
            event.ignore()
            return False
        # Copy, never Move: the drag source must not delete what it dropped here.
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        return True

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        self._accept_files(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        self._accept_files(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if self._accept_files(event):
            self.add(dropped_paths(event.mimeData()))
