"""Split, in the PDF Editor: save the pages as several PDFs in a folder ``name-parts/``
(``name-parts-01.pdf``, ...; "Save Parts…", the editor stays open).

The parts begin at the selected pages, have a fixed number of pages, or are typed as page
ranges (``1-3, 4-8, 9-``; ranges may overlap, pages in no range are left out). Page numbers
are those shown in the grid, after the other changes. The grid labels the first page of each
part, draws a cut before it and dims the pages that are left out.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QSpinBox

from quince.backends import pdfedit
from quince.ui.editors.base import Mode, separator, tool_button, toolbar_row
from quince.ui.editors.pdf._grid import Overlay

METHODS = (
    ("selected", "At the selected pages"),
    ("every", "Every"),
    ("ranges", "Page ranges"),
)
RANGES_HINT = "e.g. 1-3, 4-8, 9-"
HINTS = {
    "selected": "Select the pages where a new file starts",
    "every": "Every file gets this many pages",
    "ranges": "One file per range; pages left out are dimmed",
}


class SplitPart:
    def __init__(self, editor) -> None:
        self.editor = editor
        widget, row = toolbar_row()
        self.method_combo = QComboBox()
        for key, label in METHODS:
            self.method_combo.addItem(label, key)
        self.method_combo.currentIndexChanged.connect(lambda _i: self.sync())
        row.addWidget(self.method_combo)
        self.every_spin = QSpinBox()
        self.every_spin.setRange(1, 9999)
        self.every_spin.setValue(1)
        self.every_spin.setKeyboardTracking(False)
        self.every_spin.valueChanged.connect(lambda _v: self.sync())
        row.addWidget(self.every_spin)
        self.ranges_edit = QLineEdit()
        self.ranges_edit.setPlaceholderText(RANGES_HINT)
        self.ranges_edit.setMinimumWidth(220)
        self.ranges_edit.setToolTip("One file per range; pages count as shown in the grid")
        self.ranges_edit.textChanged.connect(lambda _t: self.sync())
        row.addWidget(self.ranges_edit)
        row.addWidget(separator())
        self.save_button = tool_button(
            "Save Parts…", icon="document-save", tip="Save the parts as PDFs in a folder"
        )
        self.save_button.clicked.connect(editor.save_parts)
        row.addWidget(self.save_button)
        row.addSpacing(10)
        self.label = QLabel()
        self.label.setEnabled(False)
        row.addWidget(self.label)
        row.addStretch(1)
        self.mode = Mode(
            "split", "Split", icon="edit-cut", tip="Save the pages as several PDFs",
            toolbar=widget, hint=HINTS["selected"],
        )  # fmt: skip
        self.sync()

    @property
    def method(self) -> str:
        return self.method_combo.currentData()

    def parts(self) -> list[tuple[int, ...]]:
        """The parts (indices into the editor's pages); ``ValueError`` for wrong ranges."""
        count = len(self.editor.pages)
        method = self.method
        if method == "every":
            return pdfedit.parts_every(count, self.every_spin.value())
        if method == "ranges":
            return pdfedit.parse_ranges(self.ranges_edit.text(), count)
        return pdfedit.parts_at(count, self.editor.grid.selected_indices())

    def usable(self, parts: list[tuple[int, ...]]) -> bool:
        least = 1 if self.method == "ranges" else 2
        return least <= len(parts) <= pdfedit.MAX_PARTS

    def sync(self) -> None:
        method = self.method
        self.every_spin.setVisible(method == "every")
        self.ranges_edit.setVisible(method == "ranges")
        self.every_spin.setSuffix(" page" if self.every_spin.value() == 1 else " pages")
        try:
            parts = self.parts()
            error = ""
        except ValueError as exc:
            parts, error = [], str(exc)
        self.save_button.setEnabled(not error and self.usable(parts))
        if error:
            self.label.setText(error)
        elif method == "ranges" and not parts:
            self.label.setText("Type the page ranges, one file each")
        elif method == "selected" and len(parts) < 2:
            self.label.setText("Select the pages where a new file starts")
        elif len(parts) > pdfedit.MAX_PARTS:
            self.label.setText(f"Too many parts (at most {pdfedit.MAX_PARTS})")
        else:
            self.label.setText(f"{len(parts)} part{'s' if len(parts) != 1 else ''}")
        self.mode.hint = HINTS[method]
        if self.editor.mode is self.mode:
            self.editor.set_hint(self.mode.hint)
            self.editor.grid.set_overlay(overlay(parts, len(self.editor.pages)))


def overlay(parts: list[tuple[int, ...]], count: int) -> Overlay:
    starts: dict[int, list[int]] = {}
    covered: set[int] = set()
    for n, part in enumerate(parts, 1):
        starts.setdefault(part[0], []).append(n)
        covered.update(part)
    labels = {
        i: ("Part " if len(ns) == 1 else "Parts ") + ", ".join(map(str, ns))
        for i, ns in starts.items()
    }
    cuts = frozenset(i for i in starts if i > 0 and i - 1 in covered)
    return Overlay(labels, frozenset(range(count)) - covered, cuts)
