"""Change speed: play a video or a sound faster or slower (0.25x to 4x), saved as
``name-2x``. The pitch stays the same unless "Keep pitch" is off (then a faster sound is
higher, like a tape). Playback in the editor runs at the chosen speed. Subtitles are left out,
since their timing would no longer match.
"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QLabel

from quince.backends import mediaedit
from quince.core.formats import Category
from quince.core.operations import JobRequest
from quince.ui.editors.base import separator, tool_button
from quince.ui.editors.media import marks as m
from quince.ui.editors.media._common import MediaEditor

PRESETS = (0.5, 0.75, 1.25, 1.5, 2.0, 3.0)


def suffix(factor: float) -> str:
    return f"{factor:g}x"


class SpeedEditor(MediaEditor):
    name = "media.speed"
    title = "Change Speed"
    save_label = "Save"
    categories = frozenset({Category.VIDEO, Category.AUDIO})

    def __init__(self, selection, data, parent=None) -> None:
        super().__init__(selection, data, parent)
        self.add_tool(QLabel("Speed"))
        low, high = mediaedit.SPEED_RANGE
        self.spin = QDoubleSpinBox()
        self.spin.setRange(low, high)
        self.spin.setSingleStep(0.05)
        self.spin.setDecimals(2)
        self.spin.setSuffix("×")
        self.spin.setValue(1.0)
        self.spin.setKeyboardTracking(False)
        self.spin.valueChanged.connect(lambda _v: self._changed("speed"))
        self.add_tool(self.spin)
        for factor in PRESETS:
            button = tool_button(f"{factor:g}×", tip=f"{factor:g} times the speed")
            button.clicked.connect(lambda _c=False, f=factor: self.spin.setValue(f))
            self.add_tool(button)
        self.add_tool(separator())
        self.pitch_box = QCheckBox("Keep pitch")
        self.pitch_box.setChecked(True)
        self.pitch_box.setToolTip("Off: a faster sound is also higher, a slower one lower")
        self.pitch_box.toggled.connect(lambda _on: self._changed())
        self.add_tool(self.pitch_box)
        self.toolbar.addStretch(1)
        self.media_hint("Choose the speed; playback follows it")
        self._refresh()
        self.start_history()

    @property
    def factor(self) -> float:
        return round(self.spin.value(), 2)

    def state(self) -> tuple:
        return (self.factor, self.pitch_box.isChecked())

    def set_state(self, state: tuple) -> None:
        factor, pitch = state
        for widget in (self.spin, self.pitch_box):
            widget.blockSignals(True)
        self.spin.setValue(factor)
        self.pitch_box.setChecked(pitch)
        for widget in (self.spin, self.pitch_box):
            widget.blockSignals(False)
        self._refresh()

    def can_save(self) -> bool:
        return abs(self.factor - 1.0) > 1e-6

    def _changed(self, merge: str | None = None) -> None:
        self._refresh()
        self.commit(merge)

    def _refresh(self) -> None:
        self.player.set_rate(self.factor)
        after = self.duration / self.factor
        self.info.setText(f"{m.clock(self.duration)} → {m.clock(after)}")
        self.update_buttons()

    def build_job(self) -> JobRequest:
        source, factor, pitch = self.source, self.factor, self.pitch_box.isChecked()
        return self.job(
            lambda ctx, out: mediaedit.speed(ctx, source, out, factor, keep_pitch=pitch),
            suffix(factor),
        )


EDITOR = SpeedEditor
