"""The media editors, driven like a user would (keys, timeline, controls), saving through the
real job queue with the generated audio / video fixtures (1 s video at 10 fps, 0.5 s sound)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from quince.backends import ffmpeg
from quince.core.deps import Availability
from quince.core.selection import Selection
from quince.ui.editors.media.channels import ChannelsEditor
from quince.ui.editors.media.crop import CropVideoEditor
from quince.ui.editors.media.redact_audio import RedactAudioEditor
from quince.ui.editors.media.redact_video import RedactVideoEditor
from quince.ui.editors.media.snapshots import SnapshotsEditor
from quince.ui.editors.media.speed import SpeedEditor
from quince.ui.editors.media.split import SplitVideoEditor
from quince.ui.editors.media.to_video import AudioToVideoEditor
from quince.ui.editors.media.trim import TrimAudioEditor, TrimVideoEditor
from tests.ui.test_editors import (  # noqa: F401 (autouse fixtures)
    _OPEN,
    _close_editors,
    _private_options,
    drag,
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


# --- shared timeline keys -----------------------------------------------------------------------


def test_timeline_keys(qtbot, tmp_path):
    window = open_media(TrimVideoEditor, fixture("sample-video.mp4", tmp_path))
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
    window = open_media(TrimVideoEditor, fixture("sample-video.mp4", tmp_path))
    timeline = window.timeline
    area = timeline.area()
    point = QPoint(int(area.left() + area.width() * 0.5), 20)
    QTest.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=point)
    assert window.position == pytest.approx(0.5, abs=0.02)


# --- trim ---------------------------------------------------------------------------------------


def test_trim_video_exact_with_undo(qtbot, tmp_path):
    window = open_media(TrimVideoEditor, fixture("sample-video.mp4", tmp_path))
    assert not window.save_button.isEnabled()
    window.seek_to(0.3)
    window.mark_in()
    window.seek_to(0.8)
    window.mark_out()
    assert window.save_button.isEnabled()
    window.undo()
    assert window.marks.out_s is None
    window.redo()
    snap = saved_output(window)
    out = only_result(snap)
    assert out.name == "sample-video-trimmed.mp4"
    info = probe(out)
    assert info.video is not None and info.duration == pytest.approx(0.5, abs=0.12)


def test_trim_video_fast_starts_at_keyframe(qtbot, tmp_path):
    window = open_media(TrimVideoEditor, fixture("sample-video.mp4", tmp_path))
    window.seek_to(0.5)
    window.mark_in()
    window.fast_box.setChecked(True)
    assert "keyframe" in window.info.text()
    assert window.part()[0] == pytest.approx(window.media.keyframes[0])
    snap = saved_output(window)
    assert probe(only_result(snap)).duration == pytest.approx(1.0, abs=0.12)


def test_trim_audio(qtbot, tmp_path):
    window = open_media(TrimAudioEditor, fixture("sample-audio.wav", tmp_path))
    assert window.timeline is window.canvas and window.media.wave is not None
    assert not window.fast_box.isVisible()
    window.seek_to(0.1)
    window.mark_in()
    window.seek_to(0.4)
    window.mark_out()
    out = only_result(saved_output(window))
    assert probe(out).duration == pytest.approx(0.3, abs=0.01)


# --- crop, speed, snapshots, split ------------------------------------------------------------


def test_crop_video(qtbot, tmp_path):
    window = open_media(CropVideoEditor, fixture("sample-video.mkv", tmp_path))
    drag(window.canvas, (11, 13), (101, 91))
    assert window.save_button.isEnabled() and "90 × 78" in window.info.text()
    out = only_result(saved_output(window))
    assert out.name == "sample-video-cropped.mkv"
    video = probe(out).video
    assert (video.width, video.height) == (90, 78)


def test_change_speed_video_and_audio(qtbot, tmp_path):
    window = open_media(SpeedEditor, fixture("sample-video.webm", tmp_path))
    window.spin.setValue(2.0)
    assert "0:00.50" in window.info.text()
    out = only_result(saved_output(window))
    assert out.name == "sample-video-2x.webm"
    assert probe(out).duration == pytest.approx(0.5, abs=0.1)
    window = open_media(SpeedEditor, fixture("sample-audio.mp3", tmp_path))
    window.spin.setValue(0.5)
    window.pitch_box.setChecked(False)
    out = only_result(saved_output(window))
    assert out.name == "sample-audio-0.5x.mp3"
    assert probe(out).duration == pytest.approx(1.0, abs=0.1)


def test_snapshots_current_frame_and_marked_frames(qtbot, tmp_path):
    src = fixture("sample-video.mp4", tmp_path)
    window = open_media(SnapshotsEditor, src)
    window.seek_to(0.5)
    assert window.save_button.isEnabled() and "0:00.50" in window.info.text()
    out = only_result(saved_output(window))
    assert out.name == "sample-video-snapshot.png" and out.stat().st_size > 0
    window = open_media(SnapshotsEditor, src)
    for t in (0.2, 0.6, 1.0):
        window.seek_to(t)
        window.toggle_marker()
    window.format_combo.setCurrentIndex(window.format_combo.findData("jpg"))
    assert window.save_button.text() == "Save 3 Pictures"
    folder = only_result(saved_output(window))
    assert folder.is_dir() and folder.name == "sample-video"
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["sample-video-01.jpg", "sample-video-02.jpg", "sample-video-03.jpg"]


def test_split_video_markers_and_equal_parts(qtbot, tmp_path):
    src = fixture("sample-video.mov", tmp_path)
    window = open_media(SplitVideoEditor, src)
    assert not window.save_button.isEnabled()
    window.seek_to(0.4)
    window.toggle_marker()
    assert window.info.text() == "2 parts" and window.timeline.cuts == pytest.approx((0.4,))
    folder = only_result(saved_output(window))
    parts = sorted(folder.iterdir())
    assert [p.name for p in parts] == ["sample-video-01.mov", "sample-video-02.mov"]
    assert probe(parts[0]).duration == pytest.approx(0.4, abs=0.12)
    window = open_media(SplitVideoEditor, src)
    window.mode_combo.setCurrentIndex(window.mode_combo.findData("parts"))
    window.parts_spin.setValue(3)
    assert window.info.text() == "3 parts"
    window.undo()  # back to 2 parts
    assert window.parts_spin.value() == 2
    window.redo()
    assert len(list(only_result(saved_output(window)).iterdir())) == 3


# --- redact -------------------------------------------------------------------------------------


def test_redact_video_box_with_time_range(qtbot, tmp_path):
    window = open_media(RedactVideoEditor, fixture("sample-video.mp4", tmp_path))
    window.seek_to(0.2)
    window.mark_in()
    window.seek_to(0.6)
    window.mark_out()
    drag(window.canvas, (10, 10), (80, 60))
    assert len(window.regions) == 1
    region = window.regions[0]
    assert (region.start, region.end) == pytest.approx((0.2, 0.6)) and region.style == "solid"
    assert window.timeline.spans == ((region.start, region.end),)
    window.styles.buttons()[1].click()  # Blur
    assert window.regions[0].style == "blur"
    window.seek_to(0.9)
    assert window.region_at((40, 30)) is None  # hidden only in its own time
    window.mark_in()  # In at 0.9 (Out dropped)
    window.use_marks()
    assert window.regions[0].start == pytest.approx(0.9)
    snap = saved_output(window)
    out = only_result(snap)
    assert out.name == "sample-video-redacted.mp4" and probe(out).video is not None
    assert "Metadata was removed from the redacted copy" in snap.notes


def test_redact_audio_bleep(qtbot, tmp_path):
    window = open_media(RedactAudioEditor, fixture("sample-audio.flac", tmp_path))
    window.add_span()  # nothing marked
    assert not window.spans and "first" in window.info.text()
    window.seek_to(0.1)
    window.mark_in()
    window.seek_to(0.25)
    window.mark_out()
    key(window, Qt.Key.Key_A)
    assert (
        window.spans[0] == pytest.approx((0.1, 0.25))
        and len(window.spans) == 1
        and not window.marks.has_range
    )
    window.timeline_pressed(0.2)
    assert window.selected == 0
    window.delete_selected()
    assert not window.spans
    window.undo()
    assert len(window.spans) == 1
    key(window, Qt.Key.Key_2)  # Silence
    assert window.style == "silence"
    out = only_result(saved_output(window))
    assert out.name == "sample-audio-redacted.flac"
    assert probe(out).duration == pytest.approx(0.5, abs=0.02)


# --- channels, audio to video -------------------------------------------------------------------


def test_channels_mono_to_stereo(qtbot, tmp_path):
    window = open_media(ChannelsEditor, fixture("sample-audio.ogg", tmp_path))
    assert not window.save_button.isEnabled()
    assert [window.mode_combo.itemData(i) for i in range(window.mode_combo.count())] == [
        None,
        "stereo",
    ]
    window.mode_combo.setCurrentIndex(1)
    assert len(window.timeline.lanes) == 2 and "Mono → Stereo" in window.info.text()
    out = only_result(saved_output(window))
    assert out.name == "sample-audio-stereo.ogg"
    assert probe(out).audio[0].channels == 2


def test_audio_to_video(qtbot, tmp_path):
    window = open_media(AudioToVideoEditor, fixture("sample-audio.m4a", tmp_path))
    assert window.save_button.isEnabled()
    qtbot.waitUntil(lambda: window.canvas.image.width() == 1920, timeout=15000)  # preview
    still = next(b for b in window.style_group.buttons() if b.property("quince_key") == "still")
    still.click()
    assert not window.save_button.isEnabled()  # no cover art and no picture yet
    window.set_picture(None)
    window.style_group.buttons()[1].click()  # Bars
    window.size_group.buttons()[1].click()  # Square
    settle()
    out = only_result(saved_output(window))
    assert out.name == "sample-audio.mp4"
    video = probe(out).video
    assert (video.width, video.height) == (1080, 1080)


def test_audio_to_video_with_a_picture(qtbot, tmp_path):
    window = open_media(AudioToVideoEditor, fixture("sample-audio.mp3", tmp_path))
    window.set_picture(fixture("sample.png", tmp_path))
    assert window.visual == "still" and window.save_button.isEnabled()
    out = only_result(saved_output(window))
    assert probe(out).video.width == 1920


def test_editor_refuses_a_file_without_video(tmp_path):
    from quince.core.jobs import JobError

    with pytest.raises(JobError, match="one file"):
        TrimVideoEditor.load(Selection.from_paths([fixture("sample-audio.wav", tmp_path)]))
