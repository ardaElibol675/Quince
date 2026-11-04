import errno
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from quince.backends import files
from quince.core.jobs import JobQueue, JobState
from quince.core.selection import Selection
from tests.unit.test_jobs import wait_finished


@pytest.fixture
def queue():
    q = JobQueue(workers=1)
    yield q
    q.shutdown()


def run(queue, fn, *paths):
    job_id = queue.submit(fn, Selection.from_paths(paths), title="test")
    return wait_finished(queue, job_id)


def copy(queue, sources, dest):
    return run(queue, lambda ctx: files.copy_items(ctx, sources, dest), *sources)


def move(queue, sources, dest):
    return run(queue, lambda ctx: files.move_items(ctx, sources, dest), *sources)


def tree(root: Path) -> dict[str, str]:
    """Relative path -> content ("<dir>", "-> target" for links) of everything below root."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = str(path.relative_to(root))
            if path.is_symlink():
                out[rel] = "-> " + os.readlink(path)
            elif path.is_dir():
                out[rel] = "<dir>"
            else:
                out[rel] = path.read_text()
    return out


@pytest.fixture
def src(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "a.txt").write_text("alpha")
    folder = root / "album"
    (folder / "sub").mkdir(parents=True)
    (folder / "one.jpg").write_text("1")
    (folder / "sub" / "two.jpg").write_text("2")
    (folder / "empty.txt").write_text("")
    os.symlink("one.jpg", folder / "link.jpg")
    return root


@pytest.fixture
def dest(tmp_path):
    path = tmp_path / "dest"
    path.mkdir()
    return path


@pytest.fixture
def other_fs(tmp_path):
    """A folder on another file system (tmpfs), for moves that must copy."""
    shm = Path("/dev/shm")
    if not shm.is_dir() or not os.access(shm, os.W_OK):
        pytest.skip("/dev/shm is not available")
    if os.stat(shm).st_dev == os.stat(tmp_path).st_dev:
        pytest.skip("/dev/shm is on the same file system as the test folder")
    path = Path(tempfile.mkdtemp(prefix="quince-test-", dir=shm))
    yield path
    shutil.rmtree(path, ignore_errors=True)


# --- copy ---------------------------------------------------------------------


def test_copy_files_and_folders(queue, src, dest):
    before = tree(src)
    snap = copy(queue, [src / "a.txt", src / "album"], dest)
    assert snap.state is JobState.SUCCEEDED, snap.error
    assert snap.outputs == (dest / "a.txt", dest / "album")
    assert tree(dest / "album") == tree(src / "album")
    assert (dest / "album" / "link.jpg").is_symlink()  # links are copied as links
    assert (dest / "a.txt").read_text() == "alpha"
    assert tree(src) == before  # originals untouched


def test_copy_keeps_metadata(queue, src, dest):
    os.chmod(src / "a.txt", 0o640)
    os.utime(src / "a.txt", (1_000_000_000, 1_000_000_000))
    copy(queue, [src / "a.txt"], dest)
    st = os.stat(dest / "a.txt")
    assert oct(st.st_mode & 0o777) == oct(0o640)
    assert st.st_mtime == 1_000_000_000


def test_copy_never_overwrites(queue, src, dest):
    (dest / "a.txt").write_text("existing")
    (dest / "album").mkdir()
    snap = copy(queue, [src / "a.txt", src / "album"], dest)
    assert snap.outputs == (dest / "a (1).txt", dest / "album (1)")
    assert (dest / "a.txt").read_text() == "existing"
    assert (dest / "a (1).txt").read_text() == "alpha"


def test_copy_into_the_same_folder_makes_a_numbered_copy(queue, src):
    snap = copy(queue, [src / "a.txt"], src)
    assert snap.outputs == (src / "a (1).txt",)


def test_copy_folder_into_itself_is_refused(queue, src):
    snap = copy(queue, [src / "album"], src / "album" / "sub")
    assert snap.state is JobState.FAILED
    assert snap.error == "Cannot copy album into itself"
    assert not (src / "album" / "sub" / "album").exists()


def test_copy_missing_source(queue, src, dest):
    snap = copy(queue, [src / "gone.txt"], dest)
    assert snap.error == "gone.txt no longer exists"


def test_copy_to_a_file_is_refused(queue, src):
    snap = copy(queue, [src / "album"], src / "a.txt")
    assert snap.state is JobState.FAILED
    assert "is not a folder" in snap.error


def test_copy_refuses_special_files(queue, src, dest):
    os.mkfifo(src / "album" / "pipe")
    snap = copy(queue, [src / "album"], dest)
    assert snap.error == "pipe is a special file and cannot be copied"
    assert files.os.listdir(dest) == []


def test_copy_checks_free_space(queue, src, dest, monkeypatch):
    monkeypatch.setattr(files.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10, 9, 1))
    snap = copy(queue, [src / "a.txt"], dest)
    assert snap.error.startswith("Not enough space in dest: needs 5 bytes, 1 bytes free")


def test_failed_copy_leaves_nothing(queue, src, dest, monkeypatch):
    real = files._copy_file

    def failing(s, d, progress):
        if s.name == "two.jpg":
            raise PermissionError(errno.EACCES, "Permission denied", str(s))
        real(s, d, progress)

    monkeypatch.setattr(files, "_copy_file", failing)
    snap = copy(queue, [src / "a.txt", src / "album"], dest)
    assert snap.state is JobState.FAILED
    assert snap.error == "Permission denied: two.jpg"
    assert os.listdir(dest) == []  # all or nothing


def test_cancelled_copy_leaves_nothing(queue, src, dest, monkeypatch):
    (src / "big.bin").write_bytes(b"x" * 100)
    monkeypatch.setattr(files, "CHUNK", 1)  # many chunks, each checks for cancellation
    started = []

    def slow(ctx):
        started.append(ctx)
        ctx._cancel.set()  # as if Cancel was clicked while copying
        files.copy_items(ctx, [src / "big.bin"], dest)

    snap = run(queue, slow, src / "big.bin")
    assert snap.state is JobState.CANCELLED
    assert os.listdir(dest) == []


def test_read_only_folders_are_copied_and_cleaned_up(queue, src, dest, monkeypatch):
    os.chmod(src / "album" / "sub", 0o555)
    try:
        snap = copy(queue, [src / "album"], dest)
        assert snap.state is JobState.SUCCEEDED
        assert oct(os.stat(dest / "album" / "sub").st_mode & 0o777) == oct(0o555)

        # A failure after a read-only folder was copied still removes everything.
        monkeypatch.setattr(files, "_check_space", lambda d, n: None)
        real = files._copy_entry

        def fail_late(s, d, progress, created=False):
            real(s, d, progress, created=created)
            if s.name == "album":
                raise OSError(errno.EIO, "I/O error", str(s))

        monkeypatch.setattr(files, "_copy_entry", fail_late)
        other = dest / "again"
        other.mkdir()
        snap = copy(queue, [src / "album"], other)
        assert snap.state is JobState.FAILED
        assert os.listdir(other) == []
    finally:
        os.chmod(src / "album" / "sub", 0o755)
        for p in dest.rglob("sub"):
            os.chmod(p, 0o755)


def test_copy_without_copy_file_range(queue, src, dest, monkeypatch):
    def unsupported(*args):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(files.os, "copy_file_range", unsupported, raising=False)
    (src / "big.bin").write_bytes(os.urandom(300_000))
    monkeypatch.setattr(files, "CHUNK", 65536)
    snap = copy(queue, [src / "big.bin"], dest)
    assert snap.state is JobState.SUCCEEDED
    assert (dest / "big.bin").read_bytes() == (src / "big.bin").read_bytes()


def test_copy_reports_progress(src, dest):
    events = []
    q = JobQueue(workers=1, on_event=events.append)
    try:
        copy(q, [src / "album"], dest)
    finally:
        q.shutdown()
    messages = [e.message for e in events if e.message]
    assert "Copying album" in messages
    assert events[-1].progress == 1.0


def test_human_size():
    assert files.human_size(5) == "5 bytes"
    assert files.human_size(1500) == "1.5 KB"
    assert files.human_size(3_200_000_000) == "3.2 GB"


# --- move ---------------------------------------------------------------------


def test_move_on_the_same_file_system(queue, src, dest):
    album = tree(src / "album")
    inode = os.stat(src / "a.txt").st_ino
    snap = move(queue, [src / "a.txt", src / "album"], dest)
    assert snap.state is JobState.SUCCEEDED, snap.error
    assert snap.outputs == (dest / "a.txt", dest / "album")
    assert not (src / "a.txt").exists() and not (src / "album").exists()
    assert tree(dest / "album") == album
    assert os.stat(dest / "a.txt").st_ino == inode  # renamed, not copied


def test_move_never_overwrites(queue, src, dest):
    (dest / "a.txt").write_text("existing")
    snap = move(queue, [src / "a.txt"], dest)
    assert snap.outputs == (dest / "a (1).txt",)
    assert (dest / "a.txt").read_text() == "existing"


def test_move_skips_items_already_there(queue, src, dest):
    snap = move(queue, [src / "a.txt"], src)
    assert snap.state is JobState.FAILED
    assert snap.error == "Already in src"
    assert (src / "a.txt").exists()

    (dest / "b.txt").write_text("b")
    snap = move(queue, [src / "a.txt", dest / "b.txt"], dest)
    assert snap.outputs == (dest / "a.txt",)
    assert (dest / "b.txt").read_text() == "b"


def test_move_folder_into_itself_is_refused(queue, src):
    snap = move(queue, [src / "album"], src / "album" / "sub")
    assert snap.error == "Cannot move album into itself"
    assert (src / "album" / "one.jpg").exists()


def test_move_needs_permission_on_the_source_folder(queue, src, dest):
    os.chmod(src, 0o555)
    try:
        snap = move(queue, [src / "a.txt"], dest)
    finally:
        os.chmod(src, 0o755)
    assert snap.error == f"Cannot move a.txt: no permission to change {src}"
    assert (src / "a.txt").exists() and not (dest / "a.txt").exists()


def test_failed_move_puts_everything_back(queue, src, dest, monkeypatch):
    before = tree(src)
    real = files.move_no_replace

    def failing(a, b):
        if Path(a).name == "album":
            raise PermissionError(errno.EACCES, "Permission denied", str(a))
        real(a, b)

    monkeypatch.setattr(files, "move_no_replace", failing)
    snap = move(queue, [src / "a.txt", src / "album"], dest)
    assert snap.state is JobState.FAILED
    assert snap.error == "Permission denied: album"
    assert tree(src) == before
    assert os.listdir(dest) == []


def test_move_across_file_systems(queue, src, other_fs):
    album = tree(src / "album")
    snap = move(queue, [src / "a.txt", src / "album"], other_fs)
    assert snap.state is JobState.SUCCEEDED, snap.error
    assert snap.outputs == (other_fs / "a.txt", other_fs / "album")
    assert tree(other_fs / "album") == album
    assert (other_fs / "a.txt").read_text() == "alpha"
    assert sorted(os.listdir(src)) == []


def test_move_across_file_systems_simulated(queue, src, dest, monkeypatch):
    monkeypatch.setattr(files, "same_file_system", lambda s, d: False)
    album = tree(src / "album")
    snap = move(queue, [src / "album"], dest)
    assert snap.state is JobState.SUCCEEDED
    assert tree(dest / "album") == album
    assert not (src / "album").exists()


def test_cancelled_cross_move_leaves_the_originals(queue, src, dest, monkeypatch):
    monkeypatch.setattr(files, "same_file_system", lambda s, d: False)
    before = tree(src)

    def cancelled(ctx):
        ctx._cancel.set()
        files.move_items(ctx, [src / "a.txt", src / "album"], dest)

    snap = run(queue, cancelled, src / "a.txt")
    assert snap.state is JobState.CANCELLED
    assert tree(src) == before
    assert os.listdir(dest) == []


def test_cross_move_keeps_the_copy_when_the_original_cannot_be_removed(
    queue, src, dest, monkeypatch
):
    monkeypatch.setattr(files, "same_file_system", lambda s, d: False)
    real = files.remove_tree

    def stubborn(path):
        if Path(path) == src / "a.txt":
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        real(path)

    monkeypatch.setattr(files, "remove_tree", stubborn)
    snap = move(queue, [src / "a.txt"], dest)
    assert snap.state is JobState.FAILED
    assert snap.error == "Copied a.txt, but could not remove the original: Permission denied: a.txt"
    assert (dest / "a.txt").read_text() == "alpha"  # nothing lost
    assert (src / "a.txt").exists()


def test_cross_move_checks_that_folders_can_be_emptied(queue, src, dest, monkeypatch):
    monkeypatch.setattr(files, "same_file_system", lambda s, d: False)
    os.chmod(src / "album" / "sub", 0o555)
    try:
        snap = move(queue, [src / "album"], dest)
    finally:
        os.chmod(src / "album" / "sub", 0o755)
    assert snap.error == "Cannot move sub: no permission to remove its contents"
    assert os.listdir(dest) == []


def test_bind_mount_rename_falls_back_to_copy(queue, src, dest, monkeypatch):
    real = files.move_no_replace

    def exdev_for_sources(a, b):
        if Path(a).parent == src:
            raise OSError(errno.EXDEV, "Invalid cross-device link", str(a))
        real(a, b)

    monkeypatch.setattr(files, "move_no_replace", exdev_for_sources)
    snap = move(queue, [src / "a.txt"], dest)
    assert snap.state is JobState.SUCCEEDED, snap.error
    assert (dest / "a.txt").read_text() == "alpha"
    assert not (src / "a.txt").exists()
