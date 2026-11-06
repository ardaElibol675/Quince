"""Speed, in the Video and Sound Editors: faster or slower (0.25x to 4x). The pitch stays the
same unless "Keep pitch" is off (then a faster sound is higher, like a tape). Playback in the
editor runs at the chosen speed. Subtitles are left out, since their timing would no longer
match."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QLabel

from quince.backends import mediaedit
from quince.ui.editors.base import Mode, separator, tool_button, toolbar_row
from quince.ui.editors.media import marks as m

PRESETS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)


class SpeedPart:
    def __init__(self, editor) -> None:
        self.editor = editor
        widget, row = toolbar_row()
        row.addWidget(QLabel("Speed"))
        low, high = mediaedit.SPEED_RANGE
        self.spin = QDoubleSpinBox()
        self.spin.setRange(low, high)
        self.spin.setSingleStep(0.05)
        self.spin.setDecimals(2)
        self.spin.setSuffix("×")
        self.spin.setValue(1.0)
        self.spin.setKeyboardTracking(False)
        self.spin.valueChanged.connect(lambda v: self._change(merge="speed", speed=round(v, 2)))
        row.addWidget(self.spin)
        for factor in PRESETS:
            button = tool_button(f"{factor:g}×", tip=f"{factor:g} times the speed")
            button.clicked.connect(lambda _c=False, f=factor: self.spin.setValue(f))
            row.addWidget(button)
        row.addWidget(separator())
        self.pitch_box = QCheckBox("Keep pitch")
        self.pitch_box.setChecked(True)
        self.pitch_box.setToolTip("Off: a faster sound is also higher, a slower one lower")
        self.pitch_box.toggled.connect(lambda on: self._change(keep_pitch=on))
        row.addWidget(self.pitch_box)
        row.addSpacing(10)
        self.label = QLabel()
        self.label.setEnabled(False)
        row.addWidget(self.label)
        row.addStretch(1)
        self.mode = Mode(
            "speed", "Speed", icon="media-seek-forward", tip="Faster or slower", toolbar=widget,
            hint="Choose the speed; playback follows it",
        )  # fmt: skip
        self.sync()

    def _change(self, merge: str | None = None, **changes) -> None:
        self.editor.change(replace(self.editor.edit, **changes), merge=merge)

    def sync(self) -> None:
        edit = self.editor.edit
        for widget in (self.spin, self.pitch_box):
            widget.blockSignals(True)
        self.spin.setValue(edit.speed)
        self.pitch_box.setChecked(edit.keep_pitch)
        for widget in (self.spin, self.pitch_box):
            widget.blockSignals(False)
        start, end = edit.span(self.editor.duration)
        length = end - start
        self.label.setText(f"{m.clock(length)} → {m.clock(length / edit.speed)}")
