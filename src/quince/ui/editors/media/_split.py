"""Split, in the Video Editor: cut the kept part into pieces and save them as a folder
``name-parts/`` with ``name-parts-01.mp4``, ... ("Save Parts…"; the editor stays open and the
other edits are applied to every part).

The cut points come from the markers (M), from a number of equal parts, or from a fixed
length; the timeline shows them as dashed lines. Parts are cut exactly; with "Fast" (only
when nothing else changes and the format stays) every stream is copied and every cut moves
back to its keyframe, so the parts still follow each other without a gap.
"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QLabel, QSpinBox

from quince.backends import mediaedit
from quince.ui.editors.base import Mode, separator, tool_button, toolbar_row
from quince.ui.editors.media._trim import FAST_TIP

METHODS = (("markers", "At the markers"), ("parts", "Into equal parts"), ("every", "Every"))


class SplitPart:
    def __init__(self, editor) -> None:
        self.editor = editor
        widget, row = toolbar_row()
        self.method_combo = QComboBox()
        for key, label in METHODS:
            self.method_combo.addItem(label, key)
        self.method_combo.currentIndexChanged.connect(lambda _i: self.sync())
        row.addWidget(self.method_combo)
        self.parts_spin = QSpinBox()
        self.parts_spin.setRange(2, 100)
        self.parts_spin.setValue(2)
        self.parts_spin.setSuffix(" parts")
        self.parts_spin.setKeyboardTracking(False)
        self.parts_spin.valueChanged.connect(lambda _v: self.sync())
        row.addWidget(self.parts_spin)
        self.every_spin = QDoubleSpinBox()
        self.every_spin.setRange(1.0, 36000.0)
        self.every_spin.setDecimals(1)
        self.every_spin.setValue(min(60.0, max(1.0, round(editor.duration / 2))))
        self.every_spin.setSuffix(" s")
        self.every_spin.setKeyboardTracking(False)
        self.every_spin.valueChanged.connect(lambda _v: self.sync())
        row.addWidget(self.every_spin)
        row.addWidget(separator())
        self.fast_box = QCheckBox("Fast (no re-encoding)")
        self.fast_box.setToolTip(FAST_TIP.replace("the part starts", "each part starts"))
        self.fast_box.toggled.connect(lambda _on: self.sync())
        row.addWidget(self.fast_box)
        row.addWidget(separator())
        self.save_button = tool_button(
            "Save Parts…", icon="document-save", tip="Save the parts in a folder"
        )
        self.save_button.clicked.connect(editor.save_parts)
        row.addWidget(self.save_button)
        row.addSpacing(10)
        self.label = QLabel()
        self.label.setEnabled(False)
        row.addWidget(self.label)
        row.addStretch(1)
        self.mode = Mode(
            "split", "Split", icon="edit-cut", tip="Cut into parts", toolbar=widget,
            hint="M sets a cut point · Save Parts writes them",
        )  # fmt: skip
        self.sync()

    @property
    def method(self) -> str:
        return self.method_combo.currentData()

    def parts(self, *, fast: bool | None = None) -> tuple[tuple[float, float], ...]:
        editor = self.editor
        method = self.method
        fast = self.fast_box.isChecked() if fast is None else fast
        return editor.part_spans(
            markers=editor.marks.markers if method == "markers" else (),
            parts=self.parts_spin.value() if method == "parts" else 0,
            every=self.every_spin.value() if method == "every" else 0.0,
            fast=fast,
        )

    def sync(self) -> None:
        editor = self.editor
        method = self.method
        self.parts_spin.setVisible(method == "parts")
        self.every_spin.setVisible(method == "every")
        self.fast_box.setEnabled(editor.fast_allowed() or self.fast_box.isChecked())
        parts = self.parts()
        if editor.mode is self.mode:
            editor.timeline.set_cuts([start for start, _end in parts[1:]])
        ok = 2 <= len(parts) <= mediaedit.MAX_PARTS
        self.save_button.setEnabled(ok)
        if method == "markers" and not editor.marks.markers:
            self.label.setText("Add markers with M where the video should be cut")
        elif len(parts) > mediaedit.MAX_PARTS:
            self.label.setText(f"Too many parts (at most {mediaedit.MAX_PARTS})")
        else:
            self.label.setText(f"{len(parts)} part{'s' if len(parts) != 1 else ''}")
