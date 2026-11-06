"""The Save window of the editors: the name, the format and the folder of the new file.

The name is typed without its extension (the format adds it). The folder starts at the
source's folder; "Change…" opens the destination chooser with the recent folders. A name
that is already taken is never overwritten: the window says which free name will be used
(``name (1).ext``). A format that loses something of the edit (transparency, sound) says so
below the list. Enter saves, Esc cancels.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from quince.core import formats, naming, recent

MIN_WIDTH = 460


@dataclass(frozen=True)
class OutputChoice:
    """One format in the list: ``note`` says what it loses or changes (shown when picked)."""

    format_id: str
    label: str = ""
    note: str = ""

    @property
    def text(self) -> str:
        return self.label or formats.get(self.format_id).label


@dataclass(frozen=True)
class SaveTarget:
    folder: Path
    stem: str
    format_id: str

    @property
    def extension(self) -> str:
        return formats.get(self.format_id).extension


ChooseFolder = Callable[[Path], Path | None]


def choose_folder_dialog(parent: QWidget | None, start: Path) -> Path | None:
    from quince.ui.host import _exec_dialog, folder_dialog

    dialog = folder_dialog(
        "Save In", accept_label="Choose", start=start, recent=recent.destinations()
    )
    try:
        return _exec_dialog(dialog)
    finally:
        dialog.deleteLater()


class SaveDialog(QDialog):
    def __init__(
        self,
        title: str,
        source: Path,
        choices: Sequence[OutputChoice],
        *,
        format_id: str,
        stem: str,
        folder: Path,
        folder_output: bool = False,
        accept_label: str = "Save",
        choose_folder: ChooseFolder | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not choices:
            raise ValueError("a save dialog needs at least one format")
        self.setWindowTitle(title)
        self.setMinimumWidth(MIN_WIDTH)
        self.source = Path(source)
        self.choices = tuple(choices)
        self.folder = Path(folder)
        self.folder_output = folder_output  # the name is a folder (parts, frames)
        self._choose_folder = choose_folder or (lambda start: choose_folder_dialog(self, start))

        layout = QVBoxLayout(self)
        rows = QFormLayout()
        name_row = QHBoxLayout()
        self.name_edit = QLineEdit(stem)
        self.name_edit.selectAll()
        self.name_edit.textChanged.connect(lambda _t: self._update())
        name_row.addWidget(self.name_edit, 1)
        self.extension_label = QLabel()
        self.extension_label.setEnabled(False)
        name_row.addWidget(self.extension_label)
        rows.addRow("Name", name_row)

        self.format_combo = QComboBox()
        for choice in self.choices:
            self.format_combo.addItem(choice.text, choice.format_id)
        index = self.format_combo.findData(format_id)
        self.format_combo.setCurrentIndex(max(0, index))
        self.format_combo.currentIndexChanged.connect(lambda _i: self._update())
        rows.addRow("Format", self.format_combo)

        folder_row = QHBoxLayout()
        self.folder_label = QLabel()
        self.folder_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        folder_row.addWidget(self.folder_label, 1)
        self.change_button = QPushButton("Change…")
        self.change_button.setAutoDefault(False)
        self.change_button.clicked.connect(self.change_folder)
        folder_row.addWidget(self.change_button)
        rows.addRow("Folder", folder_row)
        layout.addLayout(rows)

        self.note_label = QLabel()
        self.note_label.setWordWrap(True)
        layout.addWidget(self.note_label)
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setEnabled(False)
        layout.addWidget(self.result_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText(accept_label)
        self.save_button.setDefault(True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._update()

    # --- values -------------------------------------------------------------------------

    @property
    def format_id(self) -> str:
        return self.format_combo.currentData()

    @property
    def stem(self) -> str:
        return naming.clean_stem(self.name_edit.text())

    def target(self) -> SaveTarget:
        return SaveTarget(self.folder, self.stem, self.format_id)

    def final_path(self) -> Path:
        """The path the output will really get (a taken name gets ``(1)``)."""
        extension = "" if self.folder_output else formats.get(self.format_id).extension
        return naming.output_path(
            self.source, extension=extension, directory=self.folder, stem=self.stem
        )

    # --- actions ------------------------------------------------------------------------

    def change_folder(self) -> None:
        chosen = self._choose_folder(self.folder)
        if chosen is not None:
            self.folder = Path(chosen)
            self._update()

    def accept(self) -> None:
        if not self.stem:
            self.name_edit.setFocus()
            return
        recent.remember(self.folder)
        super().accept()

    def _update(self) -> None:
        extension = formats.get(self.format_id).extension
        self.extension_label.setText("(folder)" if self.folder_output else f".{extension}")
        self.folder_label.setText(str(self.folder))
        self.folder_label.setToolTip(str(self.folder))
        choice = self.choices[max(0, self.format_combo.currentIndex())]
        self.note_label.setText(choice.note)
        self.note_label.setVisible(bool(choice.note))
        self.save_button.setEnabled(bool(self.stem))
        if not self.stem:
            self.result_label.setText("Type a name")
            return
        final = self.final_path()
        wanted = self.stem + ("" if self.folder_output else f".{extension}")
        if final.name != wanted:
            self.result_label.setText(f"“{wanted}” exists; it will be saved as “{final.name}”")
        else:
            self.result_label.setText(f"Saved as {final}")


def run_save_dialog(dialog: SaveDialog) -> SaveTarget | None:
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    dialog.name_edit.setFocus()
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return dialog.target() if accepted else None
