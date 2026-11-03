import shutil
import time
from pathlib import Path

import pytest

from quince.backends import ffmpeg
from quince.core.jobs import JobQueue, JobState
from quince.core.selection import Selection


def test_progress_parser():
    seen = []
    parse = ffmpeg.progress_parser(10.0, seen.append)
    for line in ("frame=1", "out_time_us=2500000", "out_time_ms=5000000", "out_time_us=N/A",
                 "out_time_us=99000000", "progress=continue", "progress=end"):  # fmt: skip
        parse(line)
    assert seen == [0.25, 0.5, 1.0, 1.0]


def test_progress_parser_without_duration():
    seen = []
    ffmpeg.progress_parser(None, seen.append)("out_time_us=1000")
    assert seen == [None]


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed"
)


@pytest.mark.integration
@needs_ffmpeg
def test_real_ffmpeg_job_reports_progress(tmp_path):
    source = tmp_path / "tone.wav"
    from quince.backends import process

    process.run(["ffmpeg", "-hide_banner", "-f", "lavfi", "-i", "sine=duration=3",
                 "-ac", "1", source])  # fmt: skip
    assert ffmpeg.probe_duration(source) == pytest.approx(3.0, abs=0.1)

    events = []
    queue = JobQueue(on_event=events.append)

    def run(ctx):
        out = ctx.new_output(source, extension="flac")
        ffmpeg.run_ffmpeg(["-i", source, out], ctx, duration_s=ffmpeg.probe_duration(source, ctx))

    try:
        job = queue.submit(run, Selection.from_paths([source]), title="Convert to FLAC")
        end = time.monotonic() + 20
        while not queue.snapshot(job).state.finished and time.monotonic() < end:
            time.sleep(0.05)
        snap = queue.snapshot(job)
    finally:
        queue.shutdown()
    assert snap.state is JobState.SUCCEEDED, snap.error
    assert snap.outputs == (tmp_path / "tone.flac",)
    assert any(e.progress == 1.0 for e in events)
    assert sorted(p.name for p in Path(tmp_path).iterdir()) == ["tone.flac", "tone.wav"]


@pytest.mark.integration
@needs_ffmpeg
def test_ffmpeg_failure_is_short_and_cleaned(tmp_path):
    source = tmp_path / "broken.mp4"
    source.write_bytes(b"not a video")
    queue = JobQueue()

    def run(ctx):
        out = ctx.new_output(source, extension="mkv")
        ffmpeg.run_ffmpeg(["-i", source, out], ctx)

    try:
        job = queue.submit(run, Selection.from_paths([source]), title="t")
        end = time.monotonic() + 20
        while not queue.snapshot(job).state.finished and time.monotonic() < end:
            time.sleep(0.05)
        snap = queue.snapshot(job)
    finally:
        queue.shutdown()
    assert snap.state is JobState.FAILED
    assert snap.error.startswith("ffmpeg: ") and "Invalid data" in snap.error
    assert [p.name for p in tmp_path.iterdir()] == ["broken.mp4"]
