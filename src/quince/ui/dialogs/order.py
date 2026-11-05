"""The order dialog: put files in order before they are merged or joined.

Drag rows, or move the selected row with Alt+Up / Alt+Down (or the buttons). "Sort by Name"
restores the natural name order (2 before 10). "Leave Out" (Delete, or a double-click) greys a
file out instead of removing it, so "Put Back" can include it again; at least ``min_items``
files must stay included. Enter accepts, Esc cancels.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from quince.core.forms import natural_order
from quince.ui.files import file_icon

PATH_ROLE = Qt.ItemDataRole.UserRole
LEFT_OUT_ROLE = Qt.ItemDataRole.UserRole + 1
LEAVE_OUT, PUT_BACK = "Leave Out", "Put Back"


class OrderDialog(QDialog):
    def __init__(
        self,
        title: str,
        paths: Sequence[Path],
        *,
        accept_label: str,
        min_items: int = 2,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(460, 380)
        self.min_items = min_items
        hint = QLabel(
            "Drag to reorder, or Alt+↑ / Alt+↓. Delete or a double-click leaves a file out "
            "(and puts it back)."
        )
        hint.setWordWrap(True)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._fill(paths)
        self.list.setCurrentRow(0)

        up = QPushButton("Move Up")
        up.clicked.connect(lambda: self.move(-1))
        down = QPushButton("Move Down")
        down.clicked.connect(lambda: self.move(1))
        by_name = QPushButton("Sort by Name")
        by_name.clicked.connect(self.sort_by_name)
        self.remove_button = QPushButton(LEAVE_OUT)
        self.remove_button.clicked.connect(self.remove_current)
        side = QVBoxLayout()
        for button in (up, down, by_name, self.remove_button):
            side.addWidget(button)
        side.addStretch(1)
        middle = QHBoxLayout()
        middle.addWidget(self.list, 1)
        middle.addLayout(side)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(accept_label)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addLayout(middle)
        layout.addWidget(self.buttons)

        QShortcut(QKeySequence("Alt+Up"), self, lambda: self.move(-1))
        QShortcut(QKeySequence("Alt+Down"), self, lambda: self.move(1))
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, self.remove_current)
        # connected last: they update the buttons, which exist only now
        self.list.currentItemChanged.connect(lambda *_: self._update())
        self.list.itemDoubleClicked.connect(self.toggle)
        self._update()

    def _fill(self, paths: Sequence[Path], left_out: set[Path] = frozenset()) -> None:
        self.list.clear()
        for path in paths:
            item = QListWidgetItem(file_icon(path), path.name)
            item.setData(PATH_ROLE, str(path))
            self.list.addItem(item)
            self._show_state(item, path in left_out)

    @staticmethod
    def _path(item: QListWidgetItem) -> Path:
        return Path(item.data(PATH_ROLE))

    def _items(self) -> list[QListWidgetItem]:
        return [self.list.item(i) for i in range(self.list.count())]

    def all_paths(self) -> list[Path]:
        """Every file in the list, in order, left out or not."""
        return [self._path(item) for item in self._items()]

    def left_out(self) -> list[Path]:
        return [self._path(item) for item in self._items() if item.data(LEFT_OUT_ROLE)]

    def paths(self) -> list[Path]:
        """The included files, in order: what the tool gets."""
        return [self._path(item) for item in self._items() if not item.data(LEFT_OUT_ROLE)]

    def move(self, step: int) -> None:
        row = self.list.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)

    def sort_by_name(self) -> None:
        current = self.list.currentItem()
        keep = current.data(PATH_ROLE) if current else None
        self._fill(natural_order(self.all_paths()), set(self.left_out()))
        for i in range(self.list.count()):
            if self.list.item(i).data(PATH_ROLE) == keep:
                self.list.setCurrentRow(i)
        self._update()

    def toggle(self, item: QListWidgetItem | None) -> None:
        """Leave ``item`` out, or put it back; never fewer than ``min_items`` included."""
        if item is None:
            return
        if item.data(LEFT_OUT_ROLE):
            self._show_state(item, False)
        elif len(self.paths()) > self.min_items:
            self._show_state(item, True)
        self._update()

    def remove_current(self) -> None:
        self.toggle(self.list.currentItem())

    def _show_state(self, item: QListWidgetItem, left_out: bool) -> None:
        item.setData(LEFT_OUT_ROLE, left_out)
        font = QFont(self.list.font())
        font.setItalic(left_out)
        item.setFont(font)
        group = QPalette.ColorGroup.Disabled if left_out else QPalette.ColorGroup.Active
        item.setForeground(self.list.palette().brush(group, QPalette.ColorRole.Text))
        tip = str(self._path(item))
        item.setToolTip(f"{tip}\n(left out; double-click to put it back)" if left_out else tip)

    def _update(self) -> None:
        current = self.list.currentItem()
        if current is not None and current.data(LEFT_OUT_ROLE):
            self.remove_button.setText(PUT_BACK)
            self.remove_button.setEnabled(True)
        else:
            self.remove_button.setText(LEAVE_OUT)
            self.remove_button.setEnabled(
                current is not None and len(self.paths()) > self.min_items
            )
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(len(self.paths()) >= self.min_items)
