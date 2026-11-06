"""Split video: cut a video into parts, saved in a folder ``name/`` as ``name-01.mp4``, ...

The cut points come from the markers (M), from a number of equal parts, or from a fixed
length; the timeline shows them as dashed lines. Like Trim, parts are cut exactly (the
picture is encoded again) or, with "Fast", copied with every cut moved back to its keyframe,
so the parts still follow each other without a gap.
"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox

from quince.backends import mediaedit
from quince.core.jobs import JobContext
from quince.core.operations import JobRequest
from quince.ui.editors.base import separator
from quince.ui.editors.media._common import MediaEditor
from quince.ui.editors.media.trim import FAST_TIP

MODES = (
    ("markers", "At the markers"),
    ("parts", "Into equal parts"),
    ("every", "Every"),
)


class SplitVideoEditor(MediaEditor):
    name = "video.split"
    title = "Split Video"
    save_label = "Split"
    needs_video = True
    wants_keyframes = True
    marks_are_state = True

    def __init__(self, selection, data, parent=None) -> None:
        super().__init__(selection, data, parent)
        self.mode_combo = QComboBox()
        for key, label in MODES:
            self.mode_combo.addItem(label, key)
        self.mode_combo.currentIndexChanged.connect(lambda _i: self._changed())
        self.add_tool(self.mode_combo)
        self.parts_spin = QSpinBox()
        self.parts_spin.setRange(2, 100)
        self.parts_spin.setValue(2)
        self.parts_spin.setSuffix(" parts")
        self.parts_spin.setKeyboardTracking(False)
        self.parts_spin.valueChanged.connect(lambda _v: self._changed("parts"))
        self.add_tool(self.parts_spin)
        self.every_spin = QDoubleSpinBox()
        self.every_spin.setRange(1.0, 36000.0)
        self.every_spin.setDecimals(1)
        self.every_spin.setValue(min(60.0, max(1.0, round(self.duration / 2))))
        self.every_spin.setSuffix(" s")
        self.every_spin.setKeyboardTracking(False)
        self.every_spin.valueChanged.connect(lambda _v: self._changed("every"))
        self.add_tool(self.every_spin)
        self.add_tool(separator())
        self.fast_box = QCheckBox("Fast (no re-encoding)")
        self.fast_box.setToolTip(FAST_TIP.replace("the part starts", "each part starts"))
        self.fast_box.toggled.connect(lambda _on: self._changed())
        self.add_tool(self.fast_box)
        self.toolbar.addStretch(1)
        self.media_hint("M sets a cut point")
        self._refresh()
        self.start_history()

    @property
    def mode(self) -> str:
        return self.mode_combo.currentData()

    def _controls(self):
        return (self.mode_combo, self.parts_spin, self.every_spin, self.fast_box)

    def state(self) -> tuple:
        markers = self.marks if self.mode == "markers" else None
        return (
            markers,
            self.mode,
            self.parts_spin.value(),
            round(self.every_spin.value(), 1),
            self.fast_box.isChecked(),
        )

    def set_state(self, state: tuple) -> None:
        marks, mode, parts, every, fast = state
        for widget in self._controls():
            widget.blockSignals(True)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(mode))
        self.parts_spin.setValue(parts)
        self.every_spin.setValue(every)
        self.fast_box.setChecked(fast)
        for widget in self._controls():
            widget.blockSignals(False)
        if marks is not None:
            self.set_marks(marks, record=False)
        self._refresh()

    def set_marks(self, marks, *, record: bool = True) -> None:
        # Markers only matter (and are only undo steps) when they are the cut points.
        if self.history is not None and self.mode != "markers":
            record = False
        super().set_marks(marks, record=record)

    def points(self) -> tuple[float, ...]:
        mode = self.mode
        return mediaedit.split_points(
            self.duration,
            markers=self.marks.markers if mode == "markers" else (),
            parts=self.parts_spin.value() if mode == "parts" else 0,
            every=self.every_spin.value() if mode == "every" else 0.0,
        )

    def parts(self) -> tuple[tuple[float, float], ...]:
        keys = self.media.keyframes if self.fast_box.isChecked() else None
        return mediaedit.segments(self.duration, self.points(), keys)

    def can_save(self) -> bool:
        return 2 <= len(self.parts()) <= mediaedit.MAX_PARTS

    def marks_changed(self) -> None:
        self._refresh()

    def _changed(self, merge: str | None = None) -> None:
        self._refresh()
        self.commit(merge)

    def _refresh(self) -> None:
        mode = self.mode
        self.parts_spin.setVisible(mode == "parts")
        self.every_spin.setVisible(mode == "every")
        parts = self.parts()
        self.timeline.set_cuts([start for start, _end in parts[1:]])
        if mode == "markers" and not self.marks.markers:
            self.info.setText("Add markers with M where the video should be cut")
        elif len(parts) > mediaedit.MAX_PARTS:
            self.info.setText(f"Too many parts (at most {mediaedit.MAX_PARTS})")
        else:
            self.info.setText(f"{len(parts)} part{'s' if len(parts) != 1 else ''}")
        self.update_buttons()

    def build_job(self) -> JobRequest:
        source, parts, fast = self.source, self.parts(), self.fast_box.isChecked()

        def run(ctx: JobContext) -> None:
            folder = ctx.new_output(source.path, extension="", directory=True)
            mediaedit.split(ctx, source, folder, parts, fast=fast)
            if fast:
                ctx.note("Cut without re-encoding (no quality loss)")

        return JobRequest(run)


EDITOR = SplitVideoEditor
