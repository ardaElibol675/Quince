"""Every audio/video route with real ffmpeg, on the generated fixtures and a few made here."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from quince.backends import media
from quince.core import filetype, formats
from quince.core.deps import Availability
from quince.core.jobs import JobQueue, JobState
from quince.core.registry import discover
from quince.core.selection import Selection
from tests.unit.test_jobs import wait_finished

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        Availability.probe().missing(("ffmpeg", "ffprobe")), reason="needs ffmpeg and ffprobe"
    ),
]

GENERATED = Path(__file__).resolve().parent.parent / "fixtures" / "generated"
REGISTRY = discover()
ROUTES = [r for r in REGISTRY.routes if r.backend == "ffmpeg"]

AUDIO_CODEC = {
    "mp3": {"mp3"}, "m4a": {"aac"}, "wav": {"pcm_s16le"}, "flac": {"flac"}, "ogg": {"vorbis"},
    "opus": {"opus"}, "aiff": {"pcm_s16be"}, "wma": {"wmav2"},
}  # fmt: skip
VIDEO_CODEC = {
    "mp4": {"h264"}, "mov": {"h264"}, "mkv": {"h264"}, "webm": {"vp9"},
    "avi": {"mpeg4"}, "wmv": {"wmv2"}, "gif": {"gif"},
}  # fmt: skip


def fixture_name(fmt: str) -> str:
    category = formats.get(fmt).category.value
    return {"audio": f"sample-audio.{fmt}", "video": f"sample-video.{fmt}"}.get(
        category, "sample-animated.gif"
    )


def copy_fixture(fmt: str, folder: Path) -> Path:
    src = GENERATED / fixture_name(fmt)
    if not src.exists():
        pytest.skip(f"{src.name} missing; run scripts/make_fixtures.py")
    return Path(shutil.copy(src, folder))


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def streams(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    data = json.loads(out)
    by_kind: dict = {}
    for s in data["streams"]:
        by_kind.setdefault(s["codec_type"], []).append(s)
    by_kind["duration"] = float(data["format"].get("duration") or 0)
    return by_kind


def convert(paths, target, events=None):
    op = REGISTRY.get(f"convert.{formats.get(target).category.value}.{target}")
    queue = JobQueue(workers=1, on_event=events.append if events is not None else None)
    try:
        job = queue.submit(op.run, Selection.from_paths(paths), title="t")
        return wait_finished(queue, job, timeout=120)
    finally:
        queue.shutdown()


def only(snap) -> Path:
    assert snap.state is JobState.SUCCEEDED, snap.error
    (out,) = snap.outputs
    return out


@pytest.mark.parametrize("route", ROUTES, ids=[f"{r.source}-{r.target}" for r in ROUTES])
def test_route(route, tmp_path):
    src = copy_fixture(route.source, tmp_path)
    original = src.read_bytes()
    out = only(convert([src], route.target))
    assert out.suffix == f".{formats.get(route.target).extension}"
    assert filetype.detect(out).id == route.target
    assert src.read_bytes() == original
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".quince-tmp-")]

    info = streams(out)
    source_info = streams(src)
    if route.target in AUDIO_CODEC:
        assert [s["codec_name"] for s in info["audio"]] in ([c] for c in AUDIO_CODEC[route.target])
        assert "video" not in info
    else:
        video = info["video"]
        assert len(video) == 1
        copied = video[0]["codec_name"] == source_info["video"][0]["codec_name"]
        assert copied or video[0]["codec_name"] in VIDEO_CODEC[route.target]
        assert int(video[0]["width"]) % 2 == 0 or route.target == "gif"
        if route.target == "gif" or route.source == "gif":
            assert "audio" not in info
        else:
            assert len(info["audio"]) == 1
    if info["duration"] and source_info["duration"]:
        assert info["duration"] == pytest.approx(source_info["duration"], abs=0.25)


def test_24_bit_and_surround_audio(tmp_path):
    flac = tmp_path / "deep.flac"
    surround = "pan=5.1|c0=c0|c1=c0|c2=c0|c3=c0|c4=c0|c5=c0"
    ffmpeg("-f", "lavfi", "-i", "sine=d=0.5:sample_rate=96000", "-af", surround,
           "-sample_fmt", "s32", str(flac))  # fmt: skip
    wav = only(convert([flac], "wav"))
    assert streams(wav)["audio"][0]["codec_name"] == "pcm_s24le"
    snap = convert([flac], "mp3")
    mp3 = streams(only(snap))["audio"][0]
    assert mp3["channels"] == 2 and int(mp3["sample_rate"]) == 48000
    assert media.DOWNMIX_NOTE in snap.notes
    wma = streams(only(convert([flac], "wma")))["audio"][0]
    assert wma["channels"] == 2 and int(wma["sample_rate"]) <= 48000


def test_cover_art_and_tags(tmp_path):
    mp3 = tmp_path / "song.mp3"
    ffmpeg("-f", "lavfi", "-i", "sine=d=0.5", "-f", "lavfi", "-i", "color=red:s=64x64:d=1",
           "-map", "0", "-map", "1", "-frames:v", "1", "-c:a", "libmp3lame", "-c:v", "mjpeg",
           "-disposition:v", "attached_pic", "-metadata", "title=Güneşli Öğle",
           "-id3v2_version", "3", str(mp3))  # fmt: skip
    flac = streams(only(convert([mp3], "flac")))
    assert flac["video"][0]["disposition"]["attached_pic"] == 1
    m4a = streams(only(convert([mp3], "m4a")))
    assert m4a["video"][0]["codec_name"] == "mjpeg"
    snap = convert([mp3], "ogg")
    assert "Cover art was not kept" in snap.notes
    tags = streams(snap.outputs[0])["audio"][0].get("tags", {})
    assert {k.lower(): v for k, v in tags.items()}.get("title") == "Güneşli Öğle"  # tags are kept


def test_subtitles_into_mp4(tmp_path):
    srt = tmp_path / "sub.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,800\nMerhaba dünya\n", encoding="utf-8")
    mkv = tmp_path / "film.mkv"
    ffmpeg("-i", str(GENERATED / "sample-video.mp4"), "-i", str(srt), "-map", "0", "-map", "1",
           "-c", "copy", "-c:s", "srt", str(mkv))  # fmt: skip
    mp4 = streams(only(convert([mkv], "mp4")))
    assert mp4["subtitle"][0]["codec_name"] == "mov_text"
    snap = convert([mkv], "avi")
    assert "Subtitles were not kept (AVI cannot store them)" in snap.notes


def test_odd_dimensions_are_made_even(tmp_path):
    src = tmp_path / "odd.webm"
    ffmpeg("-f", "lavfi", "-i", "testsrc=s=161x121:d=0.5", "-c:v", "libvpx-vp9", str(src))
    video = streams(only(convert([src], "mp4")))["video"][0]
    assert (video["width"], video["height"]) == (160, 120)


def test_large_video_into_gif_is_scaled(tmp_path):
    src = tmp_path / "tall.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc=s=720x1280:r=30:d=0.5", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", str(src))  # fmt: skip
    gif = streams(only(convert([src], "gif")))["video"][0]
    assert gif["height"] == 480 and gif["width"] == 270  # the longer side is 480


def test_transparent_gif_becomes_white_video(tmp_path):
    from PIL import Image

    frames = []
    for color in ((255, 0, 0, 255), (0, 0, 255, 255)):
        im = Image.new("RGBA", (33, 21), (0, 0, 0, 0))
        im.paste(color, (5, 5, 20, 15))
        frames.append(im)
    gif = tmp_path / "anim.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=200, loop=0, disposal=2)
    mp4 = only(convert([gif], "mp4"))
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(mp4), "-frames:v", "1", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    assert min(raw[:3]) > 230  # the clear corner is white, not black


def test_still_gif_and_broken_files_are_explained(tmp_path):
    from PIL import Image

    still = tmp_path / "still.gif"
    Image.new("RGB", (8, 8)).save(still)
    snap = convert([still], "mp4")
    assert snap.error == "still.gif: Not animated; convert it to an image format instead"
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"ID3" + b"\0" * 20)
    snap = convert([broken], "wav")
    assert snap.state is JobState.FAILED
    assert snap.error.startswith("broken.mp3: ")


def test_a_silent_video_has_no_sound_to_extract(tmp_path):
    src = tmp_path / "silent.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc=s=64x48:d=0.3", "-c:v", "libx264", "-pix_fmt",
           "yuv420p", str(src))  # fmt: skip
    assert convert([src], "mp3").error == "silent.mp4: No sound to convert"


def test_progress_and_cancel_on_a_long_file(tmp_path):
    src = tmp_path / "long.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc=s=640x360:r=30:d=40", "-f", "lavfi", "-i", "sine=d=40",
           "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", str(src))  # fmt: skip
    op = REGISTRY.get("convert.video.webm")
    events = []
    queue = JobQueue(workers=1, on_event=events.append)
    try:
        job = queue.submit(op.run, Selection.from_paths([src]), title="t")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            done = [e.progress for e in events if e.progress]
            if done and 0 < done[-1] < 1:
                break
            time.sleep(0.05)
        queue.cancel(job)
        snap = wait_finished(queue, job, timeout=30)
    finally:
        queue.shutdown()
    fractions = [
        e.progress for e in events if e.progress is not None and e.state.value == "running"
    ]
    assert any(0 < f < 1 for f in fractions), "progress is reported while encoding"
    assert fractions == sorted(fractions)
    assert snap.state is JobState.CANCELLED
    assert os.listdir(tmp_path) == ["long.mp4"]  # no partial file


def test_batch_progress_stays_in_order(tmp_path):
    files = [copy_fixture("wav", tmp_path)]
    second = tmp_path / "second.wav"
    shutil.copy(files[0], second)
    files.append(second)
    events = []
    snap = convert(files, "mp3", events)
    assert len(snap.outputs) == 2
    fractions = [e.progress for e in events if e.progress is not None]
    assert fractions == sorted(fractions)  # the second file does not restart at 0


# --- the system player's decoders ------------------------------------------------------------


def _discover(path: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run(
        ["gst-discoverer-1.0", str(path)], capture_output=True, text=True, env=env, timeout=60
    )
    return proc.stdout + proc.stderr


@pytest.mark.skipif(shutil.which("gst-discoverer-1.0") is None, reason="needs GStreamer tools")
def test_outputs_open_in_gstreamer(tmp_path):
    """Totem (Videos) plays with GStreamer: every output it has decoders for must open there."""
    checked = []
    for target in [*AUDIO_CODEC, *VIDEO_CODEC]:
        source = "wav" if target in AUDIO_CODEC and target != "wav" else "mp4"
        if target == "mp4":
            source = "mkv"
        folder = tmp_path / target
        folder.mkdir()
        out = only(convert([copy_fixture(source, folder)], target))
        report = _discover(out)
        if "Missing plugins" in report or "missing plugin" in report.lower():
            continue  # no decoder installed, e.g. gstreamer1.0-libav for H.264/AAC
        assert "Done discovering" in report and "Duration: 0:00:00.0" not in report, report
        checked.append(target)
    assert {"flac", "ogg", "opus", "wav", "webm"} <= set(checked)
