"""Sound, in the Video and Sound Editors: channels, bleeps, even loudness and (videos) no
sound at all.

- **Channels** (the first sound track): stereo → mono, one side on both sides, swap, mute one;
  mono → stereo; more channels → stereo or mono. The waveform shows the result.
- **Bleep**: mark a part with I and O and press A (Add): it is covered with a 1 kHz tone or
  silence; parts are red bands on the timeline (click one to pick it, Delete removes it).
  While playing, those parts are muted. Every sound track is changed.
- **Normalize**: even loudness (-16 LUFS, EBU R128, measured first).
- **Remove sound** (videos): the result has no sound tracks.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QButtonGroup, QCheckBox, QComboBox, QHBoxLayout, QLabel, QRadioButton

from quince.backends import mediaedit
from quince.ui.editors.base import Mode, heading, side_panel, tool_button
from quince.ui.editors.media import marks as m

BLEEP_STYLES = (("bleep", "Bleep (1 kHz tone)"), ("silence", "Silence"))


def channels_text(count: int) -> str:
    return {1: "Mono", 2: "Stereo"}.get(count, f"{count} channels")


class SoundPart:
    def __init__(self, editor, *, removable: bool) -> None:
        self.editor = editor
        self.selected: int | None = None
        has_sound = bool(editor.source.info.audio)
        panel, side = side_panel()
        self.remove_box = QCheckBox("Remove the sound")
        self.remove_box.toggled.connect(lambda on: self._change(remove_audio=on))
        self.remove_box.setVisible(removable)
        side.addWidget(self.remove_box)

        self.channels_heading = heading("Channels")
        side.addWidget(self.channels_heading)
        self.channels_combo = QComboBox()
        self.channels_combo.addItem("No change", None)
        for mode in mediaedit.channel_modes(editor.source.channels):
            self.channels_combo.addItem(mediaedit.CHANNEL_MODES[mode][0], mode)
        self.channels_combo.currentIndexChanged.connect(
            lambda _i: self._change(channels=self.channels_combo.currentData())
        )
        side.addWidget(self.channels_combo)
        self.channels_label = QLabel()
        self.channels_label.setEnabled(False)
        side.addWidget(self.channels_label)

        side.addSpacing(8)
        self.bleep_heading = heading("Bleep")
        side.addWidget(self.bleep_heading)
        self.bleep_styles = QButtonGroup(panel)
        self.bleep_styles.setExclusive(True)
        self.bleep_buttons: list[QRadioButton] = []
        for key, label in BLEEP_STYLES:
            button = QRadioButton(label)
            button.setProperty("quince_style", key)
            button.setChecked(key == "bleep")
            self.bleep_styles.addButton(button)
            self.bleep_buttons.append(button)
            side.addWidget(button)
        self.bleep_styles.buttonClicked.connect(
            lambda b: self._change(bleep_style=b.property("quince_style"))
        )
        buttons = QHBoxLayout()
        self.add_button = tool_button("Add In–Out", icon="list-add", raised=True,
                                      tip="Cover the part from In to Out  A")  # fmt: skip
        self.add_button.clicked.connect(self.add_span)
        buttons.addWidget(self.add_button)
        self.delete_button = tool_button("Delete", icon="edit-delete", raised=True,
                                         tip="Delete the picked part  Del")  # fmt: skip
        self.delete_button.clicked.connect(self.delete_selected)
        buttons.addWidget(self.delete_button)
        side.addLayout(buttons)
        self.bleep_label = QLabel()
        self.bleep_label.setEnabled(False)
        self.bleep_label.setWordWrap(True)
        side.addWidget(self.bleep_label)

        side.addSpacing(8)
        self.loudness_heading = heading("Loudness")
        side.addWidget(self.loudness_heading)
        self.normalize_box = QCheckBox("Even loudness (-16 LUFS)")
        self.normalize_box.setToolTip("Measures the sound and brings it to -16 LUFS (EBU R128)")
        self.normalize_box.toggled.connect(lambda on: self._change(normalize=on))
        side.addWidget(self.normalize_box)
        side.addStretch(1)
        if not has_sound:
            note = QLabel("This file has no sound.")
            note.setWordWrap(True)
            side.insertWidget(0, note)
        self.has_sound = has_sound

        self.mode = Mode(
            "sound", "Sound", icon="audio-volume-high", tip="Channels, bleeps, loudness",
            panel=panel, hint="I / O mark a part, A covers it with a bleep",
        )  # fmt: skip
        editor.mode_key(self.mode, "A", self.add_span)
        editor.mode_key(self.mode, QKeySequence(Qt.Key.Key_Delete), self.delete_selected)
        self.sync()

    def _change(self, **changes) -> None:
        self.editor.change(replace(self.editor.edit, **changes))

    # --- bleeps -------------------------------------------------------------------------

    def add_span(self) -> None:
        editor = self.editor
        if not editor.marks.has_range:
            editor.info.setText("Mark the part with I and O first")
            return
        start, end = editor.marks.span(editor.duration)
        spans = mediaedit.merge_spans((*editor.edit.bleeps, (start, end)))
        self.selected = None
        editor.set_marks(m.clear_range(editor.marks))
        self._change(bleeps=spans)

    def delete_selected(self) -> None:
        if self.selected is None:
            return
        spans = list(self.editor.edit.bleeps)
        del spans[self.selected]
        self.selected = None
        self._change(bleeps=tuple(spans))

    def timeline_pressed(self, t: float) -> None:
        hits = [n for n, (a, b) in enumerate(self.editor.edit.bleeps) if a <= t <= b]
        self.selected = hits[-1] if hits else None
        self.sync()

    # --- showing ------------------------------------------------------------------------

    def sync(self) -> None:
        editor = self.editor
        edit = editor.edit
        if self.selected is not None and self.selected >= len(edit.bleeps):
            self.selected = None
        widgets = (self.remove_box, self.channels_combo, self.normalize_box, *self.bleep_buttons)
        for widget in widgets:
            widget.blockSignals(True)
        self.remove_box.setChecked(edit.remove_audio)
        self.channels_combo.setCurrentIndex(max(0, self.channels_combo.findData(edit.channels)))
        self.normalize_box.setChecked(edit.normalize)
        for button in self.bleep_buttons:
            button.setChecked(button.property("quince_style") == edit.bleep_style)
        for widget in widgets:
            widget.blockSignals(False)
        usable = self.has_sound and not edit.remove_audio
        self.remove_box.setEnabled(self.has_sound)
        for widget in (
            self.channels_heading, self.channels_combo, self.channels_label, self.bleep_heading,
            *self.bleep_buttons, self.add_button, self.bleep_label, self.loudness_heading,
            self.normalize_box,
        ):  # fmt: skip
            widget.setEnabled(usable)
        self.channels_label.setEnabled(False)
        self.bleep_label.setEnabled(False)
        self.delete_button.setEnabled(usable and self.selected is not None)
        count = editor.source.channels
        if edit.channels is None:
            self.channels_label.setText(f"{channels_text(count)} now" if count else "")
        else:
            _graph, after = mediaedit.channel_filter(edit.channels, count)
            self.channels_label.setText(f"{channels_text(count)} → {channels_text(after)}")
        total = sum(b - a for a, b in edit.bleeps)
        n = len(edit.bleeps)
        self.bleep_label.setText(
            f"{n} part{'s' if n != 1 else ''} · {m.length_text(total)}" if n else "No parts"
        )
        if editor.mode is self.mode:
            editor.timeline.set_spans(edit.bleeps, self.selected)
