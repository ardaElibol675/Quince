"""The Video and Sound Editors, driven like a user would (keys, timeline, controls), saving
through the real job queue with the generated audio / video fixtures (1 s video at 10 fps,
0.5 s sound)."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from quince.backends import ffmpeg
from quince.core.deps import Availability
from quince.core.selection import Selection
from quince.ui.editors.media import marks as m
from quince.ui.editors.media.editor import SoundEditor, VideoEditor
from tests.ui.test_editors import (  # noqa: F401 (autouse fixtures)
    _OPEN,
    _close_editors,
    _private_options,
    drag,
    run_request,
    saved_output,
    settle,
)

GENERATED = Path(__file__).resolve().parent.parent / "fixtures" / "generated"

_MISSING = Availability.probe().missing(("ffmpeg", "ffprobe"))
pytestmark = pytest.mark.skipif(bool(_MISSING), reason=f"needs {', '.join(_MISSING)}")


def fixture(name: str, tmp_path: Path) -> Path:
    src = GENERATED / name
    if not src.exists():
        pytest.skip(f"{name} missing; run scripts/make_fixtures.py")
    return Path(shutil.copy(src, tmp_path / name))


def open_media(cls, path: Path):
    selection = Selection.from_paths([path])
    window = cls(selection, cls.load(selection))
    window.confirm_discard = lambda: True
    window.run_save_dialog = lambda dialog: dialog.target()
    _OPEN.append(window)
    window.resize(1000, 700)
    window.show()
    QApplication.processEvents()
    return window


def key(window, k, modifiers=Qt.KeyboardModifier.NoModifier):
    """A key press as the user makes it: the window is active, the focus on the preview."""
    window.activateWindow()
    QTest.keyClick(window.canvas, k, modifiers)
    QApplication.processEvents()


def only_result(snap) -> Path:
    assert len(snap.outputs) == 1, snap.outputs
    return Path(snap.outputs[0])


def probe(path: Path):
    return ffmpeg.probe(path)


def exported(window, action, **choose):
    """Run an extra output (Save Parts / Save Frames); the editor stays open."""
    if choose:
        window.run_save_dialog = lambda d: replace(d.target(), **choose)
    requests = []
    window.saved.connect(requests.append)
    action()
    assert len(requests) == 1 and window.isVisible()
    return run_request(window, requests[0])


def mark(window, start, end):
    window.seek_to(start)
    window.mark_in()
    window.seek_to(end)
    window.mark_out()


# --- shared timeline keys -----------------------------------------------------------------------


def test_timeline_keys(qtbot, tmp_path):
    window = open_media(VideoEditor, fixture("sample-video.mp4", tmp_path))
    key(window, Qt.Key.Key_Right)
    assert window.position == pytest.approx(1.0)  # clamped to the 1 s length
    key(window, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    assert window.position == pytest.approx(0.9)  # one frame at 10 fps
    key(window, Qt.Key.Key_O)
    key(window, Qt.Key.Key_Home)
    assert window.position == 0.0
    key(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    key(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    key(window, Qt.Key.Key_I)
    assert window.marks.span(window.duration) == pytest.approx((0.2, 0.9))
    key(window, Qt.Key.Key_M)
    assert window.marks.markers == pytest.approx((0.2,))
    key(window, Qt.Key.Key_M)
    assert window.marks.markers == ()
    key(window, Qt.Key.Key_End)
    assert window.position == pytest.approx(1.0)
    key(window, Qt.Key.Key_Space)
    qtbot.waitUntil(lambda: window.player.playing or window.position < 1.0, timeout=3000)
    window.player.pause()
    key(window, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)  # plays In..Out
    qtbot.waitUntil(lambda: not window.player.playing, timeout=5000)
    assert window.position <= 0.9 + 0.05


def test_timeline_click_seeks(qtbot, tmp_path):
    window = open_media(VideoEditor, fixture("sample-video.mp4", tmp_path))
    timeline = window.timeline
    area = timeline.area()
    point = QPoint(int(area.left() + area.width() * 0.5), 20)
    QTest.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=point)
    assert window.position == pytest.approx(0.5, abs=0.02)


# --- the Video Editor --------------------------------------------------------------------------


def test_video_editor_modes_and_the_save_window(qtbot, tmp_path):
    window = open_media(VideoEditor, fixture("sample-video.mp4", tmp_path))
    assert [mode.key for mode in window.modes] == [
        "trim", "crop", "redact", "speed", "sound", "split",
    ]  # fmt: skip
    QTest.keySequence(window, "Ctrl+5")
    assert window.mode.key == "sound" and window.side_area.isVisible()
    QTest.keySequence(window, "Ctrl+2")
    assert window.mode.key == "crop" and window.canvas.tool is window.part("crop").tool
    seen = []
    window.run_save_dialog = lambda d: seen.append(d) and None
    window.save()
    dialog = seen[0]
    formats = [c.format_id for c in dialog.choices]
    assert formats[:7] == ["mp4", "mov", "mkv", "webm", "avi", "wmv", "gif"] and "mp3" in formats
    assert dialog.format_id == "mp4" and dialog.stem == "sample-video-edited"
    assert dialog.format_combo.currentText() == "MP4"
    assert window.part("crop").mode.button.icon().isNull() is False  # bundled crop icon
    assert window.isVisible()  # cancelled


def test_trim_exact_with_undo(qtbot, tmp_path):
    window = open_media(VideoEditor, fixture("sample-video.mp4", tmp_path))
    mark(window, 0.2, 0.7)
    key(window, Qt.Key.Key_T)  # Keep In-Out
    assert window.state().trim == pytest.approx((0.2, 0.7)) and not window.marks.has_range
    assert window.timeline.trim == pytest.approx((0.2, 0.7))
    window.undo()
    assert window.state().trim is None
    window.redo()
    out = only_result(saved_output(window))
    assert out.name == "sample-video-edited.mp4"
    assert probe(out).duration == pytest.approx(0.5, abs=0.15)


def test_fast_trim_only_without_other_changes(qtbot, tmp_path):
    window = open_media(VideoEditor, fixture("sample-video.mp4", tmp_path))
    trim = window.part("trim")
    mark(window, 0.5, 1.0)
    trim.keep_marked()
    trim.fast_box.setChecked(True)
    assert window.state().fast
    window.change(replace(window.edit, speed=2.0))
    assert not trim.fast_box.isEnabled() or not window.fast_allowed()
    window.undo()
    assert window.fast_allowed() and trim.fast_box.isEnabled()
    keys = window.media.keyframes
    start = max(k for k in keys if k <= 0.5 + 1e-6)
    assert trim.kept_start() == pytest.approx(start)
    if start < 0.5:
        assert "keyframe" in trim.label.text()
        assert window.timeline.cuts == pytest.approx((start,))
    out = only_result(saved_output(window))
    assert probe(out).duration == pytest.approx(1.0 - start, abs=0.15)


def test_crop_redact_speed_and_sound_in_one_file(qtbot, tmp_path):
    window = open_media(VideoEditor, fixture("sample-video.mp4", tmp_path))
    window.set_mode("crop")
    w, h = window.source.size
    drag(window.canvas, (0, 0), (w // 2, h // 2))
    assert window.state().crop is not None
    window.set_mode("redact")
    drag(window.canvas, (w // 2 + 4, h // 2 + 4), (w - 4, h - 4))
    assert len(window.state().regions) == 1
    window.set_mode("speed")
    window.part("speed").spin.setValue(2.0)
    window.set_mode("sound")
    mark(window, 0.1, 0.4)
    key(window, Qt.Key.Key_A)  # covers In-Out with a bleep
    (bleep,) = window.state().bleeps
    assert bleep == pytest.approx((0.1, 0.4))
    window.part("sound").normalize_box.setChecked(True)
    snap = saved_output(window, format_id="mkv", stem="all")
    out = only_result(snap)
    assert out.name == "all.mkv"
    info = probe(out)
    cw, ch = w // 2 // 2 * 2, h // 2 // 2 * 2
    assert (info.video.width, info.video.height) == (cw, ch)
    assert info.duration == pytest.approx(0.5, abs=0.15)
    assert any("LUFS" in n for n in snap.notes) and any("Metadata" in n for n in snap.notes)


def test_remove_sound_and_sound_only_targets(qtbot, tmp_path):
    window = open_media(VideoEditor, fixture("sample-video.mp4", tmp_path))
    sound = window.part("sound")
    sound.remove_box.setChecked(True)
    assert not sound.normalize_box.isEnabled()
    assert "mp3" not in [c.format_id for c in window.output_choices()]
    out = only_result(saved_output(window))
    assert probe(out).video is not None and not probe(out).audio
    other = open_media(VideoEditor, fixture("sample-video.mkv", tmp_path))
    out = only_result(saved_output(other, format_id="mp3"))
    assert out.suffix == ".mp3" and probe(out).video is None


def test_save_parts_at_markers_keeps_the_editor_open(qtbot, tmp_path):
    window = open_media(VideoEditor, fixture("sample-video.mp4", tmp_path))
    window.set_mode("split")
    window.seek_to(0.4)
    key(window, Qt.Key.Key_M)
    assert len(window.part("split").parts()) == 2
    assert window.timeline.cuts == pytest.approx((0.4,))
    snap = exported(window, window.save_parts)
    (folder,) = snap.outputs
    assert folder.name == "sample-video-parts"
    parts = sorted(folder.iterdir())
    assert [p.name for p in parts] == ["sample-video-parts-01.mp4", "sample-video-parts-02.mp4"]
    split = window.part("split")
    split.method_combo.setCurrentIndex(split.method_combo.findData("parts"))
    split.parts_spin.setValue(3)
    assert len(split.parts()) == 3


def test_save_frames_one_and_several(qtbot, tmp_path):
    window = open_media(VideoEditor, fixture("sample-video.mp4", tmp_path))
    window.seek_to(0.5)
    out = only_result(exported(window, window.save_frames))
    assert out.name == "sample-video-frame.png"
    window.toggle_marker()
    window.seek_to(0.2)
    window.toggle_marker()
    snap = exported(window, window.save_frames, format_id="jpg")
    (folder,) = snap.outputs
    assert sorted(p.name for p in folder.iterdir()) == [
        "sample-video-frames-01.jpg", "sample-video-frames-02.jpg",
    ]  # fmt: skip


# --- the Sound Editor --------------------------------------------------------------------------


def test_sound_editor_trim_channels_bleep(qtbot, tmp_path):
    window = open_media(SoundEditor, fixture("sample-audio.ogg", tmp_path))
    assert [mode.key for mode in window.modes] == ["trim", "speed", "sound", "video"]
    assert not window.part("trim").fast_box.isVisible()
    sound = window.part("sound")
    assert [sound.channels_combo.itemData(i) for i in range(sound.channels_combo.count())] == [
        None, "stereo",
    ]  # fmt: skip
    sound.channels_combo.setCurrentIndex(1)
    assert len(window.timeline.lanes) == 2 and "Mono → Stereo" in sound.channels_label.text()
    mark(window, 0.1, 0.4)
    window.part("trim").keep_marked()
    window.set_mode("sound")
    window.seek_to(0.2)
    window.mark_in()
    window.seek_to(0.3)
    window.mark_out()
    sound.add_span()
    assert len(window.state().bleeps) == 1
    window.timeline_pressed(0.25)
    assert sound.selected == 0
    sound.delete_selected()
    assert not window.state().bleeps
    out = only_result(saved_output(window))
    assert out.name == "sample-audio-edited.ogg"
    info = probe(out)
    assert info.audio[0].channels == 2 and info.duration == pytest.approx(0.3, abs=0.1)


def test_sound_editor_speed_changes_playback(qtbot, tmp_path):
    window = open_media(SoundEditor, fixture("sample-audio.wav", tmp_path))
    window.part("speed").spin.setValue(2.0)
    assert window.player._player.playbackRate() == pytest.approx(2.0)
    out = only_result(saved_output(window, format_id="flac"))
    assert out.suffix == ".flac" and probe(out).duration == pytest.approx(0.25, abs=0.08)


def test_sound_to_video_in_the_video_mode(qtbot, tmp_path):
    window = open_media(SoundEditor, fixture("sample-audio.m4a", tmp_path))
    window.set_mode("video")
    assert window.canvas.picture.isVisible()
    qtbot.waitUntil(lambda: window.canvas.picture.image.width() == 1920, timeout=15000)
    visual = window.part("video")
    still = next(b for b in visual.style_group.buttons() if b.property("quince_key") == "still")
    still.click()
    assert not visual.ready()  # no cover art and no picture yet
    visual.style_group.buttons()[1].click()  # Bars
    visual.size_group.buttons()[1].click()  # Square
    assert window.default_format() == "mp4"
    out = only_result(saved_output(window))
    assert out.name == "sample-audio-edited.mp4"
    video = probe(out).video
    assert (video.width, video.height) == (1080, 1080)
    window.set_mode("trim")
    assert not window.canvas.picture.isVisible()


def test_sound_to_video_with_a_picture(qtbot, tmp_path):
    window = open_media(SoundEditor, fixture("sample-audio.mp3", tmp_path))
    window.change(
        replace(
            window.edit,
            visual=replace(
                window.edit.visual, style="still", picture=fixture("sample.png", tmp_path)
            ),
        )
    )
    assert window.part("video").ready()
    out = only_result(saved_output(window, format_id="mp4"))
    assert probe(out).video.width == 1920


def test_editor_refuses_a_file_without_video(tmp_path):
    from quince.core.jobs import JobError

    with pytest.raises(JobError, match="one file"):
        VideoEditor.load(Selection.from_paths([fixture("sample-audio.wav", tmp_path)]))


def test_clock_text():
    assert m.clock(61.5) == "1:01.50"
