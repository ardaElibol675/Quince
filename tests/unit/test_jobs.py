import os
import threading
import time
from pathlib import Path

import pytest

from quince.core import jobs
from quince.core.config import OutputConfig, OutputLocation
from quince.core.jobs import JobCancelled, JobError, JobQueue, JobState, move_no_replace
from quince.core.selection import Selection
from quince.operations import demo


class Recorder:
    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def __call__(self, snapshot):
        with self.lock:
            self.events.append(snapshot)

    def states(self, job_id):
        with self.lock:
            return [e.state for e in self.events if e.id == job_id]

    def last(self, job_id):
        with self.lock:
            return [e for e in self.events if e.id == job_id][-1]


def wait_finished(queue, job_id, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        snap = queue.snapshot(job_id)
        if snap.state.finished:
            return snap
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish: {queue.snapshot(job_id)}")


@pytest.fixture
def rec():
    return Recorder()


@pytest.fixture
def queue(rec):
    q = JobQueue(workers=2, on_event=rec)
    yield q
    q.shutdown()


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "photo.png"
    path.write_bytes(b"original")
    return path


def _sel(*paths):
    return Selection.from_paths(paths)


def _listing(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_success_moves_output_into_place(queue, rec, source):
    def run(ctx):
        out = ctx.new_output(source, extension="jpg")
        assert out.name.startswith(jobs.TEMP_PREFIX) and out.name.endswith("photo.jpg")
        out.write_bytes(b"converted")
        ctx.progress(0.5, "half")

    job = queue.submit(run, _sel(source), title="Convert to JPG")
    snap = wait_finished(queue, job)
    assert snap.state is JobState.SUCCEEDED and snap.progress == 1.0
    assert snap.outputs == (source.parent / "photo.jpg",)
    assert (source.parent / "photo.jpg").read_bytes() == b"converted"
    assert _listing(source.parent) == ["photo.jpg", "photo.png"]
    assert source.read_bytes() == b"original"
    states = rec.states(job)
    assert states[0] is JobState.QUEUED and states[-1] is JobState.SUCCEEDED
    assert any(e.message == "half" for e in rec.events)


def test_never_overwrites_existing_files(queue, source):
    (source.parent / "photo.jpg").write_bytes(b"precious")

    def run(ctx):
        ctx.new_output(source, extension="jpg").write_bytes(b"new")

    snap = wait_finished(queue, queue.submit(run, _sel(source), title="t"))
    assert snap.outputs == (source.parent / "photo (1).jpg",)
    assert (source.parent / "photo.jpg").read_bytes() == b"precious"


def test_name_taken_while_running_gets_the_next_one(queue, source):
    def run(ctx):
        ctx.new_output(source, extension="jpg").write_bytes(b"new")
        (source.parent / "photo.jpg").write_bytes(b"appeared meanwhile")

    snap = wait_finished(queue, queue.submit(run, _sel(source), title="t"))
    assert snap.outputs == (source.parent / "photo (1).jpg",)
    assert (source.parent / "photo.jpg").read_bytes() == b"appeared meanwhile"


def test_parallel_jobs_reserve_different_names(queue, source):
    gate = threading.Barrier(2)

    def run(ctx):
        out = ctx.new_output(source, extension="jpg")
        gate.wait(5)  # both reserved before either is committed
        out.write_bytes(b"x")

    a = queue.submit(run, _sel(source), title="a")
    b = queue.submit(run, _sel(source), title="b")
    outs = {wait_finished(queue, a).outputs[0].name, wait_finished(queue, b).outputs[0].name}
    assert outs == {"photo.jpg", "photo (1).jpg"}


def test_failure_leaves_no_partial_files(queue, source):
    def run(ctx):
        ctx.new_output(source, extension="jpg").write_bytes(b"half")
        folder = ctx.new_output(source, extension="", suffix="pages", directory=True)
        (folder / "page-1.png").write_bytes(b"p1")
        raise JobError("Could not decode the image", details="traceback here")

    snap = wait_finished(queue, queue.submit(run, _sel(source), title="t"))
    assert snap.state is JobState.FAILED and snap.error == "Could not decode the image"
    assert _listing(source.parent) == ["photo.png"]


def test_unexpected_exception_is_reported_and_cleaned(queue, source):
    def run(ctx):
        ctx.new_output(source, extension="jpg").write_bytes(b"half")
        raise ValueError("boom")

    snap = wait_finished(queue, queue.submit(run, _sel(source), title="t"))
    assert snap.state is JobState.FAILED and "boom" in snap.error
    assert _listing(source.parent) == ["photo.png"]


def test_missing_output_is_a_failure(queue, source):
    def run(ctx):
        ctx.new_output(source, extension="jpg")  # never written

    snap = wait_finished(queue, queue.submit(run, _sel(source), title="t"))
    assert snap.state is JobState.FAILED and "was not written" in snap.error


def test_all_or_nothing_commit(queue, source, monkeypatch):
    def run(ctx):
        ctx.new_output(source, extension="jpg").write_bytes(b"1")
        ctx.new_output(source, extension="webp").write_bytes(b"2")

    real = jobs.move_no_replace

    def flaky(src, dst):
        if dst.suffix == ".webp":
            raise PermissionError("read-only")
        real(src, dst)

    monkeypatch.setattr(jobs, "move_no_replace", flaky)
    snap = wait_finished(queue, queue.submit(run, _sel(source), title="t"))
    assert snap.state is JobState.FAILED
    assert _listing(source.parent) == ["photo.png"]


def test_cancel_while_running(queue, rec, source):
    started = threading.Event()

    def run(ctx):
        ctx.new_output(source, extension="jpg").write_bytes(b"partial")
        started.set()
        while True:
            ctx.check()
            time.sleep(0.01)

    job = queue.submit(run, _sel(source), title="t")
    assert started.wait(5)
    queue.cancel(job)
    snap = wait_finished(queue, job)
    assert snap.state is JobState.CANCELLED
    assert _listing(source.parent) == ["photo.png"]


def test_cancel_before_start(rec, source):
    q = JobQueue(workers=1, on_event=rec)
    release = threading.Event()
    try:
        first = q.submit(lambda ctx: release.wait(5), _sel(source), title="blocker")
        second = q.submit(lambda ctx: pytest.fail("must not run"), _sel(source), title="t")
        q.cancel(second)
        release.set()
        assert wait_finished(q, first).state is JobState.SUCCEEDED
        assert wait_finished(q, second).state is JobState.CANCELLED
        assert JobState.RUNNING not in rec.states(second)
    finally:
        q.shutdown()


def test_timeout_stops_the_job(queue, source):
    def run(ctx):
        ctx.new_output(source, extension="jpg").write_bytes(b"partial")
        while True:
            ctx.check()
            time.sleep(0.01)

    snap = wait_finished(queue, queue.submit(run, _sel(source), title="t", timeout_s=0.3))
    assert snap.state is JobState.FAILED and "longer than 0.3 s" in snap.error
    assert _listing(source.parent) == ["photo.png"]


def test_progress_is_throttled_but_final_values_get_through(queue, rec, source):
    def run(ctx):
        for i in range(1000):
            ctx.progress(i / 1000)
        ctx.progress(1.0)

    job = queue.submit(run, _sel(source), title="t")
    wait_finished(queue, job)
    progress_events = [e for e in rec.events if e.id == job and e.state is JobState.RUNNING]
    assert len(progress_events) < 50
    assert progress_events[-1].progress == 1.0


def test_outputs_in_configured_folder(queue, source, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = OutputConfig(OutputLocation.FOLDER, out_dir)

    def run(ctx):
        ctx.new_output(source, extension="jpg").write_bytes(b"x")

    snap = wait_finished(queue, queue.submit(run, _sel(source), title="t", output=output))
    assert snap.outputs == (out_dir / "photo.jpg",)


def test_unwritable_folder_is_explained(queue, source, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    output = OutputConfig(OutputLocation.FOLDER, locked)
    try:
        snap = wait_finished(
            queue,
            queue.submit(
                lambda ctx: ctx.new_output(source), _sel(source), title="t", output=output
            ),
        )
    finally:
        locked.chmod(0o700)
    if os.geteuid() == 0:
        pytest.skip("root can write anywhere")
    assert snap.state is JobState.FAILED and snap.error == f"Cannot write to {locked}"


def test_directory_output(queue, source):
    def run(ctx):
        folder = ctx.new_output(source, extension="", suffix="pages", directory=True)
        (folder / "1.png").write_bytes(b"1")

    snap = wait_finished(queue, queue.submit(run, _sel(source), title="t"))
    assert snap.outputs == (source.parent / "photo-pages",)
    assert (source.parent / "photo-pages" / "1.png").exists()


def test_move_no_replace(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("a")
    b.write_text("b")
    with pytest.raises(FileExistsError):
        move_no_replace(a, b)
    assert b.read_text() == "b" and a.exists()
    move_no_replace(a, tmp_path / "c")
    assert (tmp_path / "c").read_text() == "a" and not a.exists()
    d, e = tmp_path / "d", tmp_path / "e"
    d.mkdir()
    e.mkdir()  # an empty folder must not be replaced either
    with pytest.raises(FileExistsError):
        move_no_replace(d, e)


def test_move_no_replace_fallback_without_renameat2(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_renameat2", None)
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("a")
    b.write_text("b")
    with pytest.raises(FileExistsError):
        move_no_replace(a, b)
    move_no_replace(a, tmp_path / "c")
    assert (tmp_path / "c").read_text() == "a"


def test_demo_operations(monkeypatch, queue, source):
    monkeypatch.setattr(demo, "SLOW_SECONDS", 0.2)
    slow, fail = demo.OPERATIONS
    ok = wait_finished(queue, queue.submit(slow.run, _sel(source), title=slow.title))
    assert ok.state is JobState.SUCCEEDED
    assert ok.outputs[0].name == "photo-demo.txt"
    assert "step 60/60" in ok.outputs[0].read_text()
    bad = wait_finished(queue, queue.submit(fail.run, _sel(source), title=fail.title))
    assert bad.state is JobState.FAILED and "on purpose" in bad.error
    assert _listing(source.parent) == ["photo-demo.txt", "photo.png"]


def test_check_raises_cancelled(source):
    q = JobQueue(workers=1)
    try:
        ctx = jobs.JobContext(q, 1, _sel(source), OutputConfig(), threading.Event(), None)
        ctx.check()
        ctx._cancel.set()
        with pytest.raises(JobCancelled):
            ctx.check()
        assert ctx.remaining() is None
    finally:
        q.shutdown()


def test_outputs_in_a_given_folder(queue, source, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()

    def run(ctx):
        ctx.new_output(source, target_dir=target).write_text("copy")

    snap = wait_finished(queue, queue.submit(run, _sel(source), title="t"))
    assert snap.outputs == (target / "photo.png",)


def test_results_placed_by_the_job_are_reported(queue, source, tmp_path):
    placed = tmp_path / "placed.txt"

    def run(ctx):
        placed.write_text("x")
        ctx.add_result(placed)
        ctx.new_output(source, extension="txt").write_text("y")

    snap = wait_finished(queue, queue.submit(run, _sel(source), title="t"))
    assert snap.state is JobState.SUCCEEDED
    assert snap.outputs == (placed, tmp_path / "photo.txt")


def test_cancel_after_results_were_placed_is_not_reported_as_cancelled(queue, source, tmp_path):
    placed = tmp_path / "placed.txt"

    def run(ctx):
        placed.write_text("x")
        ctx.add_result(placed)
        ctx._cancel.set()  # too late: the job already changed things

    snap = wait_finished(queue, queue.submit(run, _sel(source), title="t"))
    assert snap.state is JobState.SUCCEEDED and snap.outputs == (placed,)


def test_remove_tree_handles_read_only_folders(tmp_path):
    root = tmp_path / "tree"
    (root / "ro").mkdir(parents=True)
    (root / "ro" / "f.txt").write_text("x")
    os.chmod(root / "ro", 0o555)
    jobs.remove_tree(root)
    assert not root.exists()
    link = tmp_path / "link"
    os.symlink(tmp_path, link)
    jobs.remove_tree(link)
    assert not os.path.lexists(link) and tmp_path.exists()


def test_move_no_replace_fallback_keeps_symlinks(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_renameat2", None)
    target = tmp_path / "target.txt"
    target.write_text("t")
    link = tmp_path / "link"
    os.symlink("target.txt", link)
    move_no_replace(link, tmp_path / "moved")
    assert os.readlink(tmp_path / "moved") == "target.txt"


def test_item_failures_keep_the_other_outputs(queue, source, tmp_path):
    other = tmp_path / "other.png"
    other.write_bytes(b"x")

    def run(ctx):
        for path in (source, other):
            with ctx.item(path):
                ctx.new_output(path, extension="txt").write_text("partial")
                if path == other:
                    raise JobError("cannot read it")
        ctx.note("a remark")
        ctx.note("a remark")

    snap = wait_finished(queue, queue.submit(run, _sel(source, other), title="t"))
    assert snap.state is JobState.SUCCEEDED and snap.partial
    assert snap.outputs == (tmp_path / "photo.txt",)
    assert snap.failures == ("other.png: cannot read it",)
    assert snap.notes == ("a remark",)
    assert _listing(tmp_path) == ["other.png", "photo.png", "photo.txt"]  # no partial file


def test_job_fails_when_every_item_failed(queue, source, tmp_path):
    other = tmp_path / "other.png"
    other.write_bytes(b"x")

    def run(ctx):
        for path in (i.path for i in ctx.selection.files):
            with ctx.item(path):
                raise ValueError("boom") if path == other else JobError("bad header")

    snap = wait_finished(queue, queue.submit(run, _sel(source, other), title="t"))
    assert snap.state is JobState.FAILED
    assert snap.error == "2 files failed; photo.png: bad header"
    one = wait_finished(queue, queue.submit(run, _sel(source), title="t"))
    assert one.error == "photo.png: bad header"


def test_cancel_inside_an_item_cancels_the_job(queue, source):
    def run(ctx):
        with ctx.item(source):
            ctx.new_output(source, extension="txt").write_text("x")
            ctx._cancel.set()
            ctx.check()

    snap = wait_finished(queue, queue.submit(run, _sel(source), title="t"))
    assert snap.state is JobState.CANCELLED
    assert _listing(source.parent) == ["photo.png"]


def test_item_progress_is_mapped_to_its_span(source, tmp_path):
    events = []
    q = JobQueue(workers=1, on_event=events.append)

    def run(ctx):
        with ctx.item(source, (0.5, 1.0)):
            ctx.progress(0.0, "second file")
            ctx.progress(0.5, "halfway")
        ctx.progress(0.25, "outside an item")

    try:
        wait_finished(q, q.submit(run, _sel(source), title="t"))
    finally:
        q.shutdown()
    by_message = {}
    for e in events:  # the first event with each message (the final one keeps the last text)
        by_message.setdefault(e.message, e.progress)
    assert by_message["second file"] == 0.5
    assert by_message["halfway"] == 0.75
    assert by_message["outside an item"] == 0.25


def test_suffixes_from_settings_replace_the_declared_ones(queue, source):
    output = OutputConfig(suffixes={"compressed": "small", "converted": "copy"})

    def run(ctx):
        ctx.new_output(source, suffix="compressed").write_bytes(b"x")
        ctx.new_output(source, suffix="edited").write_bytes(b"x")
        ctx.new_output(source, extension="jpg", suffix="converted").write_bytes(b"x")

    snap = wait_finished(queue, queue.submit(run, _sel(source), title="t", output=output))
    assert [p.name for p in snap.outputs] == [
        "photo-small.png",
        "photo-edited.png",
        "photo-copy.jpg",
    ]


def test_conversions_add_no_suffix_by_default(queue, source):
    def run(ctx):
        ctx.new_output(source, extension="jpg", suffix="converted").write_bytes(b"x")

    snap = wait_finished(queue, queue.submit(run, _sel(source), title="t"))
    assert [p.name for p in snap.outputs] == ["photo.jpg"]
