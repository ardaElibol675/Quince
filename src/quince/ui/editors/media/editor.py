"""The Video Editor and the Sound Editor: every edit of one video or sound in one window,
saved as one new file in one ffmpeg run (``quince.backends.mediapipeline``).

Video Editor modes (Ctrl+1 …): Trim, Crop, Redact, Speed, Sound, Split; "Save Frames…" in the
toolbar saves the marked frames (or the one shown) as pictures. Sound Editor modes: Trim,
Speed, Sound, Video. One undo history covers every mode; In / Out and the markers are a
selection used by the modes (Trim keeps In-Out, Bleep covers it, Split cuts at the markers).

Save asks for the name (``name-edited``), the format (the source's) and the folder. "Save
Parts…" and "Save Frames…" save extra files and keep the editor open.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from quince.backends import mediaedit, mediapipeline
from quince.backends.media import AUDIO_TARGETS, VIDEO_TARGETS
from quince.backends.mediapipeline import MediaEdit
from quince.core.formats import Category
from quince.core.jobs import JobContext
from quince.core.operations import JobRequest
from quince.ui.editors.base import Mode, tool_button
from quince.ui.editors.media import marks as m
from quince.ui.editors.media._common import MediaEditor, SoundView
from quince.ui.editors.media._crop import VideoCropPart
from quince.ui.editors.media._redact import VideoRedactPart
from quince.ui.editors.media._sound import SoundPart
from quince.ui.editors.media._speed import SpeedPart
from quince.ui.editors.media._split import SplitPart
from quince.ui.editors.media._trim import TrimPart
from quince.ui.editors.media._visual import VisualPart, saved_visual
from quince.ui.editors.save_dialog import OutputChoice, SaveTarget

VIDEO_FORMATS = ("mp4", "mov", "mkv", "webm", "avi", "wmv")
VIDEO_SOUND_FORMATS = ("mp3", "m4a", "wav")  # the sound of a video, as the Convert wheel
SOUND_FORMATS = ("mp3", "m4a", "wav", "flac", "ogg", "opus", "aiff", "wma")
FRAME_FORMATS = (OutputChoice("png", note="Lossless"), OutputChoice("jpg", note="Smaller files"))


class EditWindow(MediaEditor):
    """What the Video and Sound Editors share."""

    wants_wave = True
    wave_per_channel = True

    def __init__(self, selection, data, parent=None) -> None:
        super().__init__(selection, data, parent)
        self.edit = MediaEdit(visual=saved_visual())
        self.parts: list = []
        self.build_parts()
        for part in self.parts:
            self.add_mode(part.mode)
        self.set_mode(self.parts[0].mode.key)
        self.refresh_view()
        self.start_history()

    def build_parts(self) -> None:
        raise NotImplementedError

    def part(self, key: str):
        return next((p for p in self.parts if p.mode.key == key), None)

    # --- the edit -----------------------------------------------------------------------

    def change(self, edit: MediaEdit, *, merge: str | None = None, record: bool = True) -> None:
        self.edit = edit
        if record:
            self.commit(merge)
        else:
            self.refresh_view()

    def commit(self, merge: str | None = None) -> None:
        super().commit(merge)
        self.sync_parts()
        self.refresh_view()

    def sync_parts(self) -> None:
        for part in self.parts:
            part.sync()

    def state(self) -> MediaEdit:
        return self.edit

    def set_state(self, state: MediaEdit) -> None:
        self.edit = state
        self.sync_parts()
        self.refresh_view()

    def can_save(self) -> bool:
        return True  # unchanged, it can still be saved in another format or place

    def fast_allowed(self) -> bool:
        return mediapipeline.fast_possible(self.source, self.edit, self.source.format_id)

    # --- what is shown ------------------------------------------------------------------

    def refresh_view(self) -> None:
        edit = self.edit
        self.timeline.set_trim(edit.trim)
        wave = self.media.wave
        if wave is not None:
            lanes = wave.lanes
            if edit.channels is not None:
                lanes = mediaedit.channel_lanes(edit.channels, wave.lanes)
            labels = ("L", "R") if len(lanes) == 2 else self.lane_labels()[: len(lanes)]
            self.timeline.set_lanes(lanes, labels if len(lanes) > 1 else ())
        self.player.set_rate(edit.speed)
        self._update_mute()
        self.info.setText(self.summary())
        if self.mode is not None:
            self._mode_marks(self.mode)
        if hasattr(self.canvas, "update"):
            self.canvas.update()

    def summary(self) -> str:
        edit = self.edit
        start, end = edit.span(self.duration)
        length = (end - start) / edit.speed
        words = []
        if edit.trim is not None:
            words.append("trimmed")
        if edit.crop is not None:
            words.append("cropped")
        if edit.regions:
            words.append("redacted")
        if edit.speed_changed:
            words.append(f"{edit.speed:g}×")
        if edit.remove_audio:
            words.append("no sound")
        else:
            if edit.channels is not None:
                words.append(mediaedit.CHANNEL_MODES[edit.channels][1])
            if edit.bleeps:
                words.append("bleeped")
            if edit.normalize:
                words.append("normalized")
        text = f"{m.clock(self.duration)} → {m.clock(length)}"
        return f"{text} · {', '.join(words)}" if words else text

    def _update_mute(self) -> None:
        t = self.position
        edit = self.edit
        hidden = any(a <= t <= b for a, b in edit.bleeps)
        self.player.set_muted(edit.remove_audio or hidden)

    def _mode_marks(self, mode: Mode) -> None:
        """The spans and cut lines the timeline shows in ``mode``."""
        key = mode.key
        spans: Sequence = ()
        selected = None
        cuts: Sequence[float] = ()
        if key == "redact":
            part = self.part("redact")
            spans = [(r.start, r.end) for r in self.edit.regions]
            selected = part.selected
        elif key == "sound":
            spans, selected = self.edit.bleeps, self.part("sound").selected
        elif key == "split":
            cuts = [start for start, _end in self.part("split").parts()[1:]]
        elif key == "trim" and self.edit.trim is not None:
            real = self.part("trim").kept_start()
            if abs(real - self.edit.trim[0]) > 1e-3:
                cuts = [real]
        self.timeline.set_spans(spans, selected)
        self.timeline.set_cuts(cuts)

    def mode_changed(self, old: Mode | None, new: Mode) -> None:
        if isinstance(self.canvas, SoundView):
            self.canvas.show_picture(new.key == "video")
        self.sync_parts()
        self._mode_marks(new)

    def timeline_pressed(self, t: float) -> None:
        part = self.part(self.mode.key) if self.mode is not None else None
        if part is not None and hasattr(part, "timeline_pressed"):
            part.timeline_pressed(t)
            self._mode_marks(self.mode)

    def on_position(self, t: float) -> None:
        self._update_mute()
        if self.mode is not None and self.mode.key == "redact":
            self.canvas.update()
        visual = self.part("video")
        if visual is not None and self.mode is visual.mode:
            visual.schedule()

    def marks_changed(self) -> None:
        for key in ("split", "trim"):
            part = self.part(key)
            if part is not None:
                part.sync()
        if self.mode is not None:
            self._mode_marks(self.mode)

    def part_spans(self, *, markers=(), parts=0, every=0.0, fast=False):
        keys = self.media.keyframes if fast and self.fast_allowed() else None
        return mediapipeline.part_spans(
            self.source, self.edit, markers=markers, parts=parts, every=every, keys=keys
        )

    # --- saving -------------------------------------------------------------------------

    def default_format(self) -> str:
        ids = [c.format_id for c in self.output_choices()]
        return self.source.format_id if self.source.format_id in ids else ids[0]

    def effective_edit(self, target: str) -> MediaEdit:
        """The edit as it is written to ``target``: a fast trim starts at its keyframe."""
        edit = self.edit
        if not edit.fast:
            return edit
        if edit.trim is None or not mediapipeline.fast_possible(self.source, edit, target):
            return replace(edit, fast=False)
        start, end = edit.trim
        return replace(edit, trim=(mediaedit.snap_back(start, self.media.keyframes), end))

    def build_job(self, target: SaveTarget | None = None) -> JobRequest:
        assert target is not None
        source, edit, path = self.source, self.effective_edit(target.format_id), self.source.path

        def run(ctx: JobContext) -> None:
            out = ctx.new_output(
                path, extension=target.extension, name=target.stem, target_dir=target.folder
            )
            for note in mediapipeline.run_edit(ctx, source, edit, target.format_id, out):
                ctx.note(note)

        return JobRequest(run)

    def closeEvent(self, event) -> None:
        super().closeEvent(event)
        visual = self.part("video")
        if event.isAccepted() and visual is not None:
            visual.close()


class VideoEditor(EditWindow):
    name = "video.edit"
    title = "Video Editor"
    categories = frozenset({Category.VIDEO})
    needs_video = True
    wants_keyframes = True

    def build_parts(self) -> None:
        self.parts = [
            TrimPart(self, fast=True),
            VideoCropPart(self),
            VideoRedactPart(self),
            SpeedPart(self),
            SoundPart(self, removable=True),
            SplitPart(self),
        ]
        self.frames_button = tool_button(
            "Save Frames…", icon="camera-photo",
            tip="Save the marked frames (or the one shown) as pictures",
        )  # fmt: skip
        self.frames_button.clicked.connect(self.save_frames)
        self.toolbar.addWidget(self.frames_button)

    def output_choices(self) -> list[OutputChoice]:
        choices = [OutputChoice(f) for f in VIDEO_FORMATS]
        choices.append(OutputChoice("gif", note="No sound; at most 480 px and 15 frames a second."))
        if self.source.info.audio and not self.edit.remove_audio:
            choices += [
                OutputChoice(f, note="Only the sound is saved.") for f in VIDEO_SOUND_FORMATS
            ]
        return choices

    # --- extra outputs ------------------------------------------------------------------

    def save_parts(self) -> None:
        split = self.part("split")
        stem = f"{self.source.path.stem}-parts"
        choices = [OutputChoice(f) for f in VIDEO_FORMATS]
        target = self.ask_target(
            choices, self.default_format(), stem, title="Save Parts", folder_output=True,
            accept_label="Save Parts",
        )  # fmt: skip
        if target is None:
            return
        edit = self.effective_edit(target.format_id)
        fast = split.fast_box.isChecked() and mediapipeline.fast_possible(
            self.source, edit, target.format_id
        )
        parts = split.parts(fast=fast)
        source, path = self.source, self.source.path

        def run(ctx: JobContext) -> None:
            folder = ctx.new_output(
                path, extension="", directory=True, name=target.stem, target_dir=target.folder
            )
            mediapipeline.export_parts(
                ctx, source, replace(edit, fast=fast), target.format_id, folder, target.stem, parts
            )

        self.export(JobRequest(run))

    def save_frames(self) -> None:
        times = self.marks.markers or (self.position,)
        stem = self.source.path.stem
        several = len(times) > 1
        target = self.ask_target(
            FRAME_FORMATS, "png", f"{stem}-frames" if several else f"{stem}-frame",
            title="Save Frames", folder_output=several, accept_label="Save Frames",
        )  # fmt: skip
        if target is None:
            return
        source, edit, path = self.source, self.edit, self.source.path
        image_format = target.format_id

        def run(ctx: JobContext) -> None:
            common = {"name": target.stem, "target_dir": target.folder}
            if not several:
                out = ctx.new_output(path, extension=target.extension, **common)
                shots = [(times[0], out)]
            else:
                folder = ctx.new_output(path, extension="", directory=True, **common)
                width = max(2, len(str(len(times))))
                shots = [
                    (t, folder / f"{target.stem}-{n:0{width}d}.{target.extension}")
                    for n, t in enumerate(times, 1)
                ]
            mediapipeline.export_frames(ctx, source, edit, shots, image_format)

        self.export(JobRequest(run))


class SoundEditor(EditWindow):
    name = "audio.edit"
    title = "Sound Editor"
    categories = frozenset({Category.AUDIO})
    needs_audio = True

    def build_parts(self) -> None:
        self.parts = [
            TrimPart(self, fast=False),
            SpeedPart(self),
            SoundPart(self, removable=False),
            VisualPart(self),
        ]

    def output_choices(self) -> list[OutputChoice]:
        choices = [OutputChoice(f) for f in SOUND_FORMATS if f in AUDIO_TARGETS]
        if "mp4" in VIDEO_TARGETS:
            note = "A video with the picture of the Video mode."
            if not self.part("video").ready():
                note = "Choose a picture in the Video mode first (no cover art)."
            choices.append(OutputChoice("mp4", note=note))
        return choices

    def default_format(self) -> str:
        if self.mode is not None and self.mode.key == "video":
            return "mp4"
        return super().default_format()

    def remember(self) -> None:
        self.part("video").remember()


EDITORS = (VideoEditor, SoundEditor)
