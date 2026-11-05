"""The Library page: every file Quince made, searchable.

- Type to search (Ctrl+F jumps here): every word must occur in the name, the folder, what made
  it ("Convert to JPG") or a source file name.
- Enter (or "Use in Quince") opens the wheel for the selected outputs, so they can go into a
  new operation; double-click opens a file; dragging copies it out.
- "Show in Files" selects the files in the file manager. "Remove from Library" (Delete) and
  "Clear Library" only forget entries; the files stay.
- Files that no longer exist are shown dimmed until they are removed.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QFont, QPalette
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from quince.core.history import Entry
from quince.integrations.files import show_in_files
from quince.ui.files import PATH_ROLE, FileTree, file_icon
from quince.ui.library.model import LibraryModel
from quince.ui.shelf.window import default_open

SEARCH_DELAY_MS = 150
MAX_ROWS = 1000
ID_ROLE = Qt.ItemDataRole.UserRole + 1
COLUMNS = ("Name", "Made with", "From", "Date", "Folder")
COLUMN_WIDTHS = (190, 150, 130, 115)  # the folder takes the rest


def format_date(created: float, now: float | None = None) -> str:
    """ "Today 14:32", "Yesterday 09:05", or "2026-11-02 18:40"."""
    when = datetime.fromtimestamp(created)
    today = datetime.fromtimestamp(time.time() if now is None else now).date()
    days = (today - when.date()).days
    if days == 0:
        return f"Today {when:%H:%M}"
    if days == 1:
        return f"Yesterday {when:%H:%M}"
    return f"{when:%Y-%m-%d %H:%M}"


def sources_text(entry: Entry) -> str:
    names = [s.name for s in entry.sources]
    if len(names) <= 2:
        return ", ".join(names)
    return f"{names[0]} +{len(names) - 1} more"


class LibraryPage(QWidget):
    wheel_requested = Signal(list, QPoint)  # paths, global point

    def __init__(
        self,
        model: LibraryModel,
        *,
        open_file: Callable[[Path], None] = default_open,
        show_files: Callable[[Sequence[Path]], None] = show_in_files,
        confirm: Callable[[QWidget, str], bool] | None = None,
    ) -> None:
        super().__init__()
        self.model = model
        self._open_file = open_file
        self._show_files = show_files
        self._confirm = confirm or _confirm
        self._dirty = True

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the Library (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        self.search.returnPressed.connect(self._focus_results)

        self.tree = FileTree(open_file=open_file)
        self.tree.setHeaderLabels(COLUMNS)
        header = self.tree.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate(COLUMN_WIDTHS):
            self.tree.setColumnWidth(column, width)
        self.tree.wheel_requested.connect(self.wheel_requested)
        self.tree.delete_pressed.connect(self.remove_selected)
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)

        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.count = QLabel()
        self.use_button = QPushButton("Use in Quince")
        self.use_button.setToolTip("Open the wheel for the selected files (Enter)")
        self.use_button.clicked.connect(self.tree.request_wheel)
        self.files_button = QPushButton("Show in Files")
        self.files_button.clicked.connect(self.show_selected)
        self.remove_button = QPushButton("Remove from Library")
        self.remove_button.setToolTip("Forget the selected entries; the files stay (Delete)")
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button = QPushButton("Clear Library…")
        self.clear_button.clicked.connect(self.clear)

        buttons = QHBoxLayout()
        buttons.addWidget(self.count, 1)
        for button in (self.use_button, self.files_button, self.remove_button, self.clear_button):
            buttons.addWidget(button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.empty, 1)
        layout.addLayout(buttons)

        model.changed.connect(self._on_changed)
        self.refresh()

    # --- public API ---------------------------------------------------------------

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def refresh(self) -> None:
        self._dirty = False
        query = self.search.text().strip()
        entries = self.model.entries(query, limit=MAX_ROWS)
        selected = {i.data(0, ID_ROLE) for i in self.tree.selectedItems()}
        self.tree.clear()
        dim = self.palette().brush(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        for entry in entries:
            item = self._item(entry, dim)
            self.tree.addTopLevelItem(item)
            item.setSelected(entry.id in selected)
        has_rows = bool(entries)
        self.tree.setVisible(has_rows)
        self.empty.setVisible(not has_rows)
        if query:
            self.empty.setText(f"Nothing in the Library matches “{query}”.")
            self.count.setText(f"{len(entries)} match{'es' if len(entries) != 1 else ''}")
        else:
            self.empty.setText(
                "Nothing here yet.\nFiles you convert or make with a tool are listed here."
            )
            self.count.setText(f"{len(entries)} file{'s' if len(entries) != 1 else ''}")
        self._update_buttons()

    def selected_entries(self) -> list[tuple[int, Path]]:
        return [
            (int(i.data(0, ID_ROLE)), Path(i.data(0, PATH_ROLE)))
            for i in self.tree.selected_items_in_order()
        ]

    def show_selected(self) -> None:
        paths = [p for _id, p in self.selected_entries() if p.exists()]
        if paths:
            self._show_files(paths)

    def remove_selected(self) -> None:
        ids = [i for i, _p in self.selected_entries()]
        if ids:
            self.model.remove(ids)
            self.refresh()

    def clear(self) -> None:
        if self._confirm(self, "Forget every file in the Library? The files themselves stay."):
            self.model.clear()
            self.refresh()

    # --- internals ---------------------------------------------------------------

    def _item(self, entry: Entry, dim: QBrush) -> QTreeWidgetItem:
        texts = (
            entry.name,
            entry.title,
            sources_text(entry),
            format_date(entry.created),
            str(entry.path.parent),
        )
        item = QTreeWidgetItem(list(texts))
        item.setIcon(0, file_icon(entry.path))
        item.setData(0, PATH_ROLE, str(entry.path))
        item.setData(0, ID_ROLE, entry.id)
        sources = "\n".join(str(s) for s in entry.sources)
        tip = f"{entry.path}\n{entry.title}" + (f" from\n{sources}" if sources else "")
        if not entry.exists:
            tip += "\n(the file no longer exists)"
            font = QFont(item.font(0))
            font.setItalic(True)
            for column in range(len(COLUMNS)):
                item.setForeground(column, dim)
                item.setFont(column, font)
        for column in range(len(COLUMNS)):
            item.setToolTip(column, tip)
        return item

    def _on_changed(self) -> None:
        if self.isVisible():
            self.refresh()
        else:
            self._dirty = True

    def showEvent(self, event) -> None:
        if self._dirty:
            self.refresh()
        super().showEvent(event)

    def _update_buttons(self) -> None:
        entries = self.selected_entries()
        existing = any(p.exists() for _i, p in entries)
        self.use_button.setEnabled(existing)
        self.files_button.setEnabled(existing)
        self.remove_button.setEnabled(bool(entries))
        self.clear_button.setEnabled(self.tree.topLevelItemCount() > 0 or bool(self.search.text()))

    def _focus_results(self) -> None:
        if self.tree.topLevelItemCount():
            self.tree.setFocus()
            if not self.tree.selectedItems():
                self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def _context_menu(self, pos: QPoint) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        if not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
            self.tree.setCurrentItem(item)
        existing = [p for _i, p in self.selected_entries() if p.exists()]
        menu = QMenu(self)
        use = menu.addAction("Use in Quince", self.tree.request_wheel)
        menu.addAction("Open", lambda: [self._open_file(p) for p in existing]).setEnabled(
            bool(existing)
        )
        menu.addAction("Show in Files", self.show_selected).setEnabled(bool(existing))
        menu.addSeparator()
        menu.addAction("Remove from Library", self.remove_selected)
        use.setEnabled(bool(existing))
        menu.exec(self.tree.viewport().mapToGlobal(pos))


def _confirm(parent: QWidget, text: str) -> bool:
    answer = QMessageBox.question(
        parent,
        "Clear Library",
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    return answer == QMessageBox.StandardButton.Yes
