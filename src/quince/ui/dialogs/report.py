"""The report window: sections of (name, value) rows, e.g. metadata or decoded QR codes.

Ctrl+C or the context menu copies the selected values; "Copy All" copies the report's text.
Extra buttons (e.g. "Remove Metadata…") close the window and return their key.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from quince.core.operations import Report

VALUE_ROLE = Qt.ItemDataRole.UserRole
MAX_SHOWN_CHARS = 300


def _one_line(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= MAX_SHOWN_CHARS else line[: MAX_SHOWN_CHARS - 1] + "…"


class ReportDialog(QDialog):
    def __init__(self, report: Report, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.report = report
        self.chosen: str | None = None
        self.setWindowTitle(report.title)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(620, 460)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(("Name", "Value"))
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        bold = QFont(self.tree.font())
        bold.setBold(True)
        for section in report.sections:
            head = QTreeWidgetItem([section.title])
            head.setFont(0, bold)
            self.tree.addTopLevelItem(head)
            head.setFirstColumnSpanned(True)  # only works once the item is in the tree
            if not section.rows:
                empty = QTreeWidgetItem(["", section.empty])
                empty.setDisabled(True)
                head.addChild(empty)
            for name, value in section.rows:
                row = QTreeWidgetItem([name, _one_line(value)])
                row.setData(1, VALUE_ROLE, value)
                row.setToolTip(1, value[:2000])
                head.addChild(row)
            head.setExpanded(True)

        layout = QVBoxLayout(self)
        if report.note:
            note = QLabel(report.note)
            note.setWordWrap(True)
            layout.addWidget(note)
        layout.addWidget(self.tree, 1)
        buttons = QHBoxLayout()
        self.copy_all_button = QPushButton("Copy All")
        self.copy_all_button.clicked.connect(lambda: self._copy(report.copy_all))
        self.copy_all_button.setVisible(bool(report.copy_all))
        buttons.addWidget(self.copy_all_button)
        buttons.addStretch(1)
        self.action_buttons: dict[str, QPushButton] = {}
        for action in report.actions:
            button = QPushButton(action.label)
            button.clicked.connect(lambda _c=False, key=action.key: self._choose(key))
            self.action_buttons[action.key] = button
            buttons.addWidget(button)
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        QShortcut(QKeySequence.StandardKey.Copy, self.tree, self.copy_selected)

    def selected_text(self) -> str:
        values = [i.data(1, VALUE_ROLE) for i in self.tree.selectedItems()]
        return "\n".join(v for v in values if v)

    def copy_selected(self) -> None:
        if text := self.selected_text():
            self._copy(text)

    def _copy(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)

    def _choose(self, key: str) -> None:
        self.chosen = key
        self.accept()

    def _menu(self, pos) -> None:
        if not self.tree.selectedItems():
            return
        menu = QMenu(self)
        menu.addAction("Copy", self.copy_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))
