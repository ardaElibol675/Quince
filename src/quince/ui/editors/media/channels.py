"""Audio channels: change the left / right channels of a sound (or of a video's sound).

Stereo: mix to mono, put one side on both sides (a microphone recorded on one channel),
swap the sides, or mute one. Mono: make it stereo. More channels: mix down to stereo or mono.
The waveform shows one lane per channel, as the result will be. A video's picture is copied;
only its first sound track is changed.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLabel

from quince.backends import mediaedit
from quince.core.formats import Category
from quince.core.operations import JobRequest
from quince.ui.editors.media._common import MediaEditor


class ChannelsEditor(MediaEditor):
    name = "audio.channels"
    title = "Audio Channels"
    save_label = "Save"
    categories = frozenset({Category.AUDIO, Category.VIDEO})
    needs_audio = True
    wants_wave = True
    wave_per_channel = True

    def __init__(self, selection, data, parent=None) -> None:
        super().__init__(selection, data, parent)
        self.add_tool(QLabel("Change"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Choose…", None)
        for mode in mediaedit.channel_modes(self.source.channels):
            self.mode_combo.addItem(mediaedit.CHANNEL_MODES[mode][0], mode)
        self.mode_combo.currentIndexChanged.connect(lambda _i: self._changed())
        self.add_tool(self.mode_combo)
        self.toolbar.addStretch(1)
        self.media_hint("Choose how the channels change")
        self._refresh()
        self.start_history()

    @property
    def mode(self) -> str | None:
        return self.mode_combo.currentData()

    def state(self) -> str | None:
        return self.mode

    def set_state(self, state) -> None:
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(state)))
        self.mode_combo.blockSignals(False)
        self._refresh()

    def can_save(self) -> bool:
        return self.mode is not None

    def _changed(self) -> None:
        self._refresh()
        self.commit()

    def _refresh(self) -> None:
        wave = self.media.wave
        channels = self.source.channels
        mode = self.mode
        if wave is not None:
            lanes = wave.lanes if mode is None else mediaedit.channel_lanes(mode, wave.lanes)
            labels = ("L", "R") if len(lanes) == 2 else self.lane_labels()[: len(lanes)]
            self.timeline.set_lanes(lanes, labels if len(lanes) > 1 else ())
        if mode is None:
            self.info.setText(f"{_channels(channels)} now")
        else:
            _graph, count = mediaedit.channel_filter(mode, channels)
            self.info.setText(f"{_channels(channels)} → {_channels(count)}")
        self.update_buttons()

    def build_job(self) -> JobRequest:
        source, mode = self.source, self.mode
        suffix = mediaedit.CHANNEL_MODES[mode][1]
        return self.job(lambda ctx, out: mediaedit.change_channels(ctx, source, out, mode), suffix)


def _channels(count: int) -> str:
    return {1: "Mono", 2: "Stereo"}.get(count, f"{count} channels")


EDITOR = ChannelsEditor
