"""What every media editor shares: loading the file, the player, the timeline and its keys.

Keys (window shortcuts, so a number field still gets its own arrows):

- Space plays / pauses; Shift+Space plays the part between In and Out;
- ← / → move one second; Shift+← / → one frame (video) or 0.1 s (sound);
- I / O set In and Out at the playhead; M adds a marker there (or removes the one there);
- Home / End go to the start / end.

A video editor shows the picture with the timeline under it; a sound-only editor shows a
large waveform timeline instead (``SoundView``, with room for a picture above it).
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from PySide6.QtCore import QSizeF, Qt
from PySide6.QtGui import QCloseEvent, QColor, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from quince.backends import mediaedit
from quince.backends.mediaedit import Source, Waveform
from quince.core.formats import Category
from quince.core.jobs import JobContext, JobError
from quince.core.operations import JobRequest
from quince.core.selection import Selection
from quince.ui.editors.base import EditorWindow, tool_button
from quince.ui.editors.canvas import ImageCanvas
from quince.ui.editors.media import marks as m
from quince.ui.editors.media.marks import Marks
from quince.ui.editors.media.player import Player
from quince.ui.editors.media.timeline import Timeline

MEDIA_HINT = "Space plays · ← → seek"
MARKER_PX = 5  # a marker this close to the playhead is the one M removes


class SoundView(QWidget):
    """The preview of a sound: a large waveform timeline, and a picture above it that is
    hidden unless an editor shows one (the Sound Editor's Video mode)."""

    def __init__(self, duration: float) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.picture = ImageCanvas()
        self.picture.setVisible(False)
        layout.addWidget(self.picture, 3)
        self.timeline = Timeline(duration, tall=True)
        layout.addWidget(self.timeline, 2)

    def show_picture(self, on: bool) -> None:
        self.picture.setVisible(on)
        self.timeline.setMinimumHeight(110 if on else 140)


@dataclass(frozen=True)
class MediaData:
    source: Source
    wave: Waveform | None = None
    keyframes: tuple[float, ...] = ()


class MediaEditor(EditorWindow):
    compare_label = ""
    categories: ClassVar[frozenset[Category]] = frozenset({Category.VIDEO})
    needs_video: ClassVar[bool] = False
    needs_audio: ClassVar[bool] = False
    wants_wave: ClassVar[bool] = False  # load the waveform
    wave_per_channel: ClassVar[bool] = False
    wants_keyframes: ClassVar[bool] = False
    marks_are_state: ClassVar[bool] = False  # In / Out / markers are part of the edit (undo)
    picture_canvas: ClassVar[bool | None] = None  # a picture above the timeline (None: a video)

    @classmethod
    def load(cls, selection: Selection) -> MediaData:
        files = selection.files
        if len(files) != 1 or files[0].category not in cls.categories:
            raise JobError("This editor works on one file")
        item = files[0]
        source = mediaedit.inspect(item.path, item.format_id or "")
        if cls.needs_video and source.info.video is None:
            raise JobError("There is no video in this file")
        if cls.needs_audio and not source.info.audio:
            raise JobError("There is no sound in this file")
        wave = None
        if cls.wants_wave or source.info.video is None:  # a sound-only file shows its waveform
            wave = mediaedit.waveform(source, per_channel=cls.wave_per_channel)
        keys = mediaedit.keyframes(item.path) if cls.wants_keyframes and source.is_video else ()
        return MediaData(source, wave, keys)

    def __init__(self, selection: Selection, data: Any, parent=None) -> None:
        self.media: MediaData = data
        self.source: Source = data.source
        has_video = self.source.info.video is not None
        self.shows_video = has_video if self.picture_canvas is None else self.picture_canvas
        super().__init__(selection, data, parent)
        self.duration = self.source.duration
        self.position = 0.0
        self.marks = Marks()

        if self.shows_video:
            self.timeline = Timeline(self.duration)
            if self.media.wave is not None:
                self.timeline.setFixedHeight(84)
            self.canvas.set_image(_black(self.picture_size()), QSizeF(*self.picture_size()))
        elif isinstance(self.canvas, SoundView):
            self.timeline = self.canvas.timeline  # the large waveform is the preview
        else:
            self.timeline = self.canvas
        if self.media.wave is not None:
            self.timeline.set_lanes(self.media.wave.lanes, self.lane_labels())
        self.timeline.seek.connect(self.seek_to)
        self.timeline.pressed.connect(self.timeline_pressed)

        transport = QWidget()
        row = QHBoxLayout(transport)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(6)
        self.play_button = tool_button("Play", icon="media-playback-start", tip="Play  Space")
        self.play_button.clicked.connect(self.toggle_play)
        row.addWidget(self.play_button)
        self.time_label = QLabel()
        self.time_label.setMinimumWidth(150)
        row.addWidget(self.time_label)
        row.addSpacing(8)
        for text, tip, slot in (
            ("In", "Mark the start  I", self.mark_in),
            ("Out", "Mark the end  O", self.mark_out),
            ("Marker", "Add or remove a marker  M", self.toggle_marker),
        ):
            button = tool_button(text, tip=tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        self.clear_button = tool_button("Clear In/Out", tip="Forget In and Out")
        self.clear_button.clicked.connect(lambda: self.set_marks(m.clear_range(self.marks)))
        row.addWidget(self.clear_button)
        self.range_label = QLabel()
        self.range_label.setEnabled(False)
        row.addWidget(self.range_label)
        row.addStretch(1)
        self.transport = row
        self.below.addWidget(transport)
        if self.shows_video:
            holder = QWidget()
            line = QHBoxLayout(holder)
            line.setContentsMargins(0, 0, 0, 8)
            line.addWidget(self.timeline)
            self.below.addWidget(holder)

        self.player = Player(self.source.path, video=self.shows_video and has_video, parent=self)
        self.player.frame.connect(self._on_frame)
        self.player.position_changed.connect(self._on_playing_position)
        self.player.playing_changed.connect(self._on_playing)
        self.player.failed.connect(lambda message: self.info.setText(f"Cannot play: {message}"))

        fine = m.fine_step(self.source.fps, self.shows_video)
        keys: tuple[tuple[str, Callable[[], None]], ...] = (
            ("Space", self.toggle_play),
            ("Shift+Space", self.play_range),
            ("Left", lambda: self.seek_to(self.position - m.SEEK_S)),
            ("Right", lambda: self.seek_to(self.position + m.SEEK_S)),
            ("Shift+Left", lambda: self.seek_to(self.position - fine)),
            ("Shift+Right", lambda: self.seek_to(self.position + fine)),
            ("I", self.mark_in),
            ("O", self.mark_out),
            ("M", self.toggle_marker),
            ("Home", lambda: self.seek_to(0.0)),
            ("End", lambda: self.seek_to(self.duration)),
        )
        for sequence, slot in keys:
            QShortcut(QKeySequence(sequence), self, slot, context=Qt.ShortcutContext.WindowShortcut)
        self._update_time()
        self._update_range()

    def create_canvas(self) -> QWidget:
        if self.shows_video:
            return ImageCanvas()
        return SoundView(self.source.duration)

    # --- for subclasses -----------------------------------------------------------------

    def picture_size(self) -> tuple[int, int]:
        return self.source.size

    def lane_labels(self) -> tuple[str, ...]:
        wave = self.media.wave
        if wave is None or wave.channels < 2:
            return ()
        return ("L", "R") if wave.channels == 2 else tuple(str(n + 1) for n in range(wave.channels))

    def on_position(self, t: float) -> None:
        """The playhead moved (by the user or while playing)."""

    def marks_changed(self) -> None:
        """In / Out or the markers changed."""

    def timeline_pressed(self, t: float) -> None:
        """A click on the timeline at ``t`` (it also seeks there)."""

    def media_hint(self, text: str) -> None:
        self.set_hint(f"{text} · {MEDIA_HINT}" if text else MEDIA_HINT)

    def job(
        self, work: Callable[[JobContext, Path], None], suffix: str | None, **output
    ) -> JobRequest:
        """A job writing one new file (``work(ctx, out)``) next to the source."""
        path = self.source.path

        def run(ctx: JobContext) -> None:
            out = ctx.new_output(path, suffix=suffix, **output)
            work(ctx, out)

        return JobRequest(run)

    # --- playhead -----------------------------------------------------------------------

    def seek_to(self, t: float) -> None:
        t = m.clamp(t, self.duration)
        self.position = t
        self.player.seek(t)
        self.timeline.set_position(t)
        self._update_time()
        self.on_position(t)

    def toggle_play(self) -> None:
        if self.player.playing:
            self.player.pause()
            return
        if self.position >= self.duration - 0.05:
            self.seek_to(0.0)
        self.player.play()

    def play_range(self) -> None:
        start, end = self.marks.span(self.duration)
        self.seek_to(start)
        self.player.play(until=end)

    def _on_playing_position(self, t: float) -> None:
        self.position = m.clamp(t, self.duration)
        self.timeline.set_position(self.position)
        self._update_time()
        self.on_position(self.position)

    def _on_playing(self, playing: bool) -> None:
        self.play_button.setText("Pause" if playing else "Play")
        from quince.ui.host import icon_for

        icon = icon_for("media-playback-pause" if playing else "media-playback-start")
        if icon is not None:
            self.play_button.setIcon(icon)
        if not playing:
            self.position = m.clamp(self.player.position(), self.duration)
            self.timeline.set_position(self.position)
            self._update_time()

    def _on_frame(self, image: QImage) -> None:
        if isinstance(self.canvas, ImageCanvas):
            self.canvas.set_image(image, QSizeF(*self.source.size))

    def _update_time(self) -> None:
        self.time_label.setText(f"{m.clock(self.position)} / {m.clock(self.duration)}")

    # --- marks --------------------------------------------------------------------------

    def set_marks(self, marks: Marks, *, record: bool = True) -> None:
        if marks == self.marks:
            return
        self.marks = marks
        self.timeline.set_marks(marks)
        self._update_range()
        self.marks_changed()
        if record and self.marks_are_state:
            self.commit()

    def mark_in(self) -> None:
        self.set_marks(m.set_in(self.marks, self.position))

    def mark_out(self) -> None:
        self.set_marks(m.set_out(self.marks, self.position, self.duration))

    def toggle_marker(self) -> None:
        width = max(1.0, self.timeline.area().width())
        tolerance = max(self.duration * MARKER_PX / width, 1e-3)
        self.set_marks(m.toggle_marker(self.marks, self.position, tolerance))

    def _update_range(self) -> None:
        self.clear_button.setEnabled(self.marks.has_range)
        if self.marks.has_range:
            start, end = self.marks.span(self.duration)
            self.range_label.setText(f"{m.clock(start)} – {m.clock(end)}")
        else:
            self.range_label.setText("")

    # --- closing ------------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        super().closeEvent(event)
        if event.isAccepted():
            self.player.close()

    def state(self) -> Hashable:
        return self.marks

    def set_state(self, state: Hashable) -> None:
        self.set_marks(state, record=False)  # type: ignore[arg-type]


def _black(size: tuple[int, int]) -> QImage:
    w, h = size
    scale = max(1, max(w, h) // 64)
    image = QImage(max(1, w // scale), max(1, h // scale), QImage.Format.Format_RGB32)
    image.fill(QColor(0, 0, 0))
    return image
