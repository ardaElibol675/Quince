"""The options dialog: one ``quince.core.forms.Form`` as a small window.

Enter accepts, Esc cancels. Fields hidden by ``Form.shown_when`` follow the choices live.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from quince.core.forms import Choice, Form, Number, Text, Values

MIN_WIDTH = 360


class FormDialog(QDialog):
    def __init__(self, form: Form, values: Values, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.form = form
        self.setWindowTitle(form.title)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(MIN_WIDTH)
        self.widgets: dict[str, QWidget] = {}
        layout = QVBoxLayout(self)
        if form.note:
            note = QLabel(form.note)
            note.setWordWrap(True)
            layout.addWidget(note)
        self.rows = QFormLayout()
        layout.addLayout(self.rows)
        for f in form.fields:
            widget = self._widget(f, values[f.key])
            self.widgets[f.key] = widget
            self.rows.addRow(f.label, widget)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(form.accept_label)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_shown()

    def _widget(self, f, value: Any) -> QWidget:
        if isinstance(f, Choice):
            combo = QComboBox()
            for option, label in f.options:
                combo.addItem(label, option)
            combo.setCurrentIndex(f.values.index(value))
            combo.currentIndexChanged.connect(lambda _i: self._update_shown())
            return combo
        if isinstance(f, Number):
            spin = QDoubleSpinBox() if f.decimals else QSpinBox()
            if f.decimals:
                spin.setDecimals(f.decimals)
            spin.setRange(f.minimum, f.maximum)
            spin.setValue(value)
            if f.suffix:
                spin.setSuffix(f" {f.suffix}")
            return spin
        if isinstance(f, Text):
            edit = QLineEdit(value)
            edit.setMaxLength(f.max_length)
            edit.setPlaceholderText(f.default)
            return edit
        raise TypeError(f"unknown field {f!r}")

    def values(self) -> Values:
        raw: Values = {}
        for f in self.form.fields:
            widget = self.widgets[f.key]
            if isinstance(widget, QComboBox):
                raw[f.key] = widget.currentData()
            elif isinstance(widget, QSpinBox | QDoubleSpinBox):
                raw[f.key] = widget.value()
            else:
                raw[f.key] = widget.text()
        return self.form.clean(raw)

    def set_value(self, key: str, value: Any) -> None:
        widget = self.widgets[key]
        if isinstance(widget, QComboBox):
            widget.setCurrentIndex(widget.findData(value))
        elif isinstance(widget, QSpinBox | QDoubleSpinBox):
            widget.setValue(value)
        else:
            widget.setText(value)

    def _update_shown(self) -> None:
        values = self.values()
        for f in self.form.fields:
            self.rows.setRowVisible(self.widgets[f.key], self.form.is_shown(f.key, values))
        self.adjustSize()
