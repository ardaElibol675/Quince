"""Widgets and helpers shared by the Home and Library pages: file lists that drag out as Copy."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QFileInfo, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QIcon, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileIconProvider,
    QListWidget,
    QListWidgetItem,
    QTreeWidget,
    QWidget,
)

from quince.ui.shelf.window import default_open, mime_for

log = logging.getLogger(__name__)

PATH_ROLE = Qt.ItemDataRole.UserRole
ENTER_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)

_icons: QFileIconProvider | None = None


def file_icon(path: Path) -> QIcon:
    global _icons
    if _icons is None:
        _icons = QFileIconProvider()
    return _icons.icon(QFileInfo(str(path)))


def copy_drag(source: QWidget, paths: Sequence[Path]) -> Qt.DropAction:
    """Drag ``paths`` out of Quince. Copy only: the target never moves the originals."""
    drag = QDrag(source)
    drag.setMimeData(mime_for(paths))
    drag.setPixmap(file_icon(paths[0]).pixmap(QSize(32, 32), source.devicePixelRatioF()))
    return drag.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)


def _rect_center(view: QAbstractItemView, index) -> QPoint:
    rect = view.visualRect(index)
    if not rect.isValid() or not view.viewport().rect().intersects(rect):
        rect = view.viewport().rect()
    return view.viewport().mapToGlobal(rect.center())


class FileList(QListWidget):
    """A list of files: Enter asks for the wheel, double-click opens, dragging copies."""

    wheel_requested = Signal(list, QPoint)  # paths, global point to open the wheel at
    delete_pressed = Signal()

    def __init__(
        self,
        *,
        open_file: Callable[[Path], None] = default_open,
        start_drag: Callable[[QWidget, Sequence[Path]], Qt.DropAction] = copy_drag,
    ) -> None:
        super().__init__()
        self._open_file = open_file
        self._start_drag = start_drag
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setIconSize(QSize(24, 24))
        self.setUniformItemSizes(True)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.itemDoubleClicked.connect(lambda item: self._open_file(Path(item.data(PATH_ROLE))))

    def set_paths(self, rows: Sequence[tuple[Path, str]], *, select: Sequence[Path] = ()) -> None:
        """Show ``rows`` of (path, tooltip); keep or set the selection."""
        keep = set(select) or set(self.selected_paths())
        self.clear()
        for path, tip in rows:
            item = QListWidgetItem(file_icon(path), path.name or str(path))
            item.setData(PATH_ROLE, str(path))
            item.setToolTip(tip or str(path))
            self.addItem(item)
            if path in keep:
                item.setSelected(True)
                self.setCurrentItem(item, self.selectionModel().SelectionFlag.NoUpdate)

    @property
    def paths(self) -> list[Path]:
        return [Path(self.item(r).data(PATH_ROLE)) for r in range(self.count())]

    def selected_paths(self) -> list[Path]:
        rows = sorted(self.row(i) for i in self.selectedItems())
        return [Path(self.item(r).data(PATH_ROLE)) for r in rows]

    def wheel_point(self) -> QPoint:
        """Where a wheel for the selection opens: the current row."""
        return _rect_center(self, self.currentIndex())

    def request_wheel(self) -> bool:
        paths = self.selected_paths()
        if paths:
            self.wheel_requested.emit(paths, self.wheel_point())
        return bool(paths)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in ENTER_KEYS and self.request_wheel():
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_pressed.emit()
            return
        super().keyPressEvent(event)

    def startDrag(self, supported_actions) -> None:
        paths = [p for p in self.selected_paths() if p.exists()]
        if paths:
            self._start_drag(self, paths)


class FileTree(QTreeWidget):
    """The Library table: like ``FileList`` with columns."""

    wheel_requested = Signal(list, QPoint)
    delete_pressed = Signal()

    def __init__(
        self,
        *,
        open_file: Callable[[Path], None] = default_open,
        start_drag: Callable[[QWidget, Sequence[Path]], Qt.DropAction] = copy_drag,
    ) -> None:
        super().__init__()
        self._open_file = open_file
        self._start_drag = start_drag
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAllColumnsShowFocus(True)
        self.setIconSize(QSize(20, 20))
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.itemDoubleClicked.connect(self._on_double_click)

    def selected_items_in_order(self):
        return sorted(self.selectedItems(), key=lambda i: self.indexOfTopLevelItem(i))

    def selected_paths(self) -> list[Path]:
        return [Path(i.data(0, PATH_ROLE)) for i in self.selected_items_in_order()]

    def wheel_point(self) -> QPoint:
        return _rect_center(self, self.currentIndex())

    def request_wheel(self) -> bool:
        paths = [p for p in self.selected_paths() if p.exists()]
        if paths:
            self.wheel_requested.emit(paths, self.wheel_point())
        return bool(paths)

    def _on_double_click(self, item, _column: int) -> None:
        path = Path(item.data(0, PATH_ROLE))
        if path.exists():
            self._open_file(path)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in ENTER_KEYS and self.request_wheel():
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_pressed.emit()
            return
        super().keyPressEvent(event)

    def startDrag(self, supported_actions) -> None:
        paths = [p for p in self.selected_paths() if p.exists()]
        if paths:
            self._start_drag(self, paths)
