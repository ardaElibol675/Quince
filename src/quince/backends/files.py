"""Copying and moving files and folders for the Copy / Move actions.

Both run as jobs (``quince.core.jobs``): cancellable, with progress by bytes, and never over
an existing name — a name that is taken gets `` (1)``, `` (2)``, ... (Hard Rule 4).

- **Copy** writes every item to a temporary name in the destination; the job engine moves
  them into place only when all of them are complete (all or nothing).
- **Move** renames items on the same file system. Items on another file system are copied
  first; once every item is in place, the originals are deleted. If placing an item fails,
  the items already placed are moved back, so a failed Move leaves the originals where they
  were. Moving is the only operation that changes the original locations.

Symbolic links are copied as links. Special files (pipes, sockets, devices) are refused.
"""

from __future__ import annotations

import errno
import logging
import os
import secrets
import shutil
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from quince.core import naming
from quince.core.jobs import TEMP_PREFIX, JobContext, JobError, move_no_replace, remove_tree

log = logging.getLogger(__name__)

CHUNK = 8 * 1024 * 1024
ENTRY_WEIGHT = 4096  # progress weight of one entry, so many small files still move the bar
_NO_COPY_FILE_RANGE = {errno.EXDEV, errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.EBADF}


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{n} bytes" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1000
    raise AssertionError("unreachable")


def os_error_reason(exc: OSError, path: Path) -> str:
    """A short, readable reason for a failed file system call on ``path``."""
    name = Path(exc.filename).name if exc.filename else path.name
    if exc.errno in (errno.EACCES, errno.EPERM):
        return f"Permission denied: {name}"
    if exc.errno == errno.ENOSPC:
        return "The destination is full"
    if exc.errno == errno.EROFS:
        return f"{name} is on a read-only file system"
    if exc.errno == errno.ENAMETOOLONG:
        return f"The name {name} is too long for the destination"
    return f"{exc.strerror or exc}: {name}"


# --- measuring and checks ---------------------------------------------------


@dataclass
class _Size:
    bytes: int = 0
    entries: int = 0

    @property
    def weight(self) -> int:
        return self.bytes + self.entries * ENTRY_WEIGHT


def _measure(path: Path, ctx: JobContext, size: _Size, *, deletable: bool) -> None:
    """Add up ``path``'s tree. With ``deletable``, every folder must allow removing its
    contents (for moving across file systems)."""
    ctx.check()
    st = os.lstat(path)
    size.entries += 1
    if stat.S_ISLNK(st.st_mode):
        return
    if stat.S_ISREG(st.st_mode):
        size.bytes += st.st_size
        return
    if not stat.S_ISDIR(st.st_mode):
        raise JobError(f"{path.name} is a special file and cannot be copied")
    if deletable and not os.access(path, os.W_OK | os.X_OK):
        raise JobError(f"Cannot move {path.name}: no permission to remove its contents")
    with os.scandir(path) as entries:
        children = [Path(e.path) for e in entries]
    for child in children:
        _measure(child, ctx, size, deletable=deletable)


def _check_destination(dest: Path) -> None:
    if not dest.is_dir():
        raise JobError(f"{dest} is not a folder")
    if not os.access(dest, os.W_OK | os.X_OK):
        raise JobError(f"Cannot write to {dest}")


def _check_source(src: Path, dest: Path, verb: str) -> None:
    if not os.path.lexists(src):
        raise JobError(f"{src.name} no longer exists")
    if src.is_dir() and not src.is_symlink():
        real, target = src.resolve(), dest.resolve()
        if target == real or real in target.parents:
            raise JobError(f"Cannot {verb} {src.name} into itself")


def _check_space(dest: Path, needed: int) -> None:
    try:
        free = shutil.disk_usage(dest).free
    except OSError:
        return
    if needed > free:
        raise JobError(
            f"Not enough space in {dest.name or dest}: "
            f"needs {human_size(needed)}, {human_size(free)} free"
        )


# --- copying ----------------------------------------------------------------


class _Progress:
    def __init__(self, ctx: JobContext, total: int, verb: str) -> None:
        self.ctx = ctx
        self.total = max(total, 1)
        self.done = 0
        self.verb = verb
        self.message = ""

    def item(self, path: Path) -> None:
        self.message = f"{self.verb} {path.name}"
        self.ctx.progress(self.done / self.total, self.message)

    def add(self, amount: int) -> None:
        self.done += amount
        self.ctx.progress(min(self.done / self.total, 1.0))


def _copy_file(src: Path, dst: Path, progress: _Progress) -> None:
    fin = os.open(src, os.O_RDONLY | os.O_CLOEXEC)
    try:
        mode = stat.S_IMODE(os.fstat(fin).st_mode) | stat.S_IWUSR
        fout = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, mode)
        try:
            fast = hasattr(os, "copy_file_range")
            while True:
                progress.ctx.check()
                if fast:
                    try:
                        n = os.copy_file_range(fin, fout, CHUNK)
                    except OSError as exc:
                        if exc.errno not in _NO_COPY_FILE_RANGE:
                            raise
                        fast = False  # offsets are where the last call left them
                        continue
                else:
                    data = os.read(fin, CHUNK)
                    n = len(data)
                    view = memoryview(data)
                    while view:
                        view = view[os.write(fout, view) :]
                if n == 0:
                    break
                progress.add(n)
        finally:
            os.close(fout)
    finally:
        os.close(fin)
    shutil.copystat(src, dst, follow_symlinks=False)


def _copy_entry(src: Path, dst: Path, progress: _Progress, *, created: bool = False) -> None:
    """Copy ``src`` to ``dst``; ``created`` means the folder ``dst`` already exists."""
    st = os.lstat(src)
    if stat.S_ISLNK(st.st_mode):
        os.symlink(os.readlink(src), dst)
    elif stat.S_ISDIR(st.st_mode):
        if not created:
            os.mkdir(dst, 0o700)  # writable while filling; the real mode is copied last
        with os.scandir(src) as entries:
            names = sorted(e.name for e in entries)
        for name in names:
            _copy_entry(src / name, dst / name, progress)
        shutil.copystat(src, dst, follow_symlinks=False)
    elif stat.S_ISREG(st.st_mode):
        _copy_file(src, dst, progress)
    else:
        raise JobError(f"{src.name} is a special file and cannot be copied")
    progress.add(ENTRY_WEIGHT)


def _is_real_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def copy_items(ctx: JobContext, paths: Iterable[Path], dest: Path) -> None:
    """Copy files and folders into ``dest`` (a job's ``run``)."""
    dest = Path(dest)
    sources = [Path(p) for p in paths]
    _check_destination(dest)
    size = _Size()
    try:
        for src in sources:
            _check_source(src, dest, "copy")
            _measure(src, ctx, size, deletable=False)
    except OSError as exc:
        raise JobError(os_error_reason(exc, dest)) from exc
    _check_space(dest, size.bytes)
    progress = _Progress(ctx, size.weight, "Copying")
    for src in sources:
        temp = ctx.new_output(src, target_dir=dest, directory=_is_real_dir(src))
        progress.item(src)
        try:
            _copy_entry(src, temp, progress, created=_is_real_dir(src))
        except OSError as exc:
            raise JobError(os_error_reason(exc, src), details=str(exc)) from exc


# --- moving -----------------------------------------------------------------


@dataclass
class _MoveItem:
    src: Path
    same_fs: bool
    temp: Path | None = None
    final: Path | None = None
    copied: bool = False  # placed from a copy: the original must be deleted


def same_file_system(src: Path, dest_dev: int) -> bool:
    """True when ``src`` can be renamed into a folder on device ``dest_dev``."""
    return os.lstat(src).st_dev == dest_dev


def _place(path: Path, wanted: Path) -> Path:
    """Rename ``path`` to ``wanted`` or the next free name; never over an existing file."""
    final = naming.unique_path(wanted)
    while True:
        try:
            move_no_replace(path, final)
            return final
        except FileExistsError:
            final = naming.unique_path(final)


def _temp_in(dest: Path, src: Path) -> Path:
    return dest / f"{TEMP_PREFIX}{secrets.token_hex(4)}-{src.name}"


def _copy_to_temp(item: _MoveItem, dest: Path, progress: _Progress) -> None:
    item.temp = _temp_in(dest, item.src)
    if _is_real_dir(item.src):
        os.mkdir(item.temp, 0o700)
    progress.item(item.src)
    _copy_entry(item.src, item.temp, progress, created=_is_real_dir(item.src))


def _roll_back(placed: Sequence[_MoveItem]) -> None:
    for item in reversed(placed):
        try:
            if item.copied:
                remove_tree(item.final)
            else:
                move_no_replace(item.final, item.src)
        except OSError as exc:
            log.error("could not undo moving %s to %s: %s", item.src, item.final, exc)


def move_items(ctx: JobContext, paths: Iterable[Path], dest: Path) -> None:
    """Move files and folders into ``dest`` (a job's ``run``); reports each result."""
    dest = Path(dest)
    _check_destination(dest)
    items: list[_MoveItem] = []
    real_dest = dest.resolve()
    try:
        dest_dev = os.stat(dest).st_dev
        for src in map(Path, paths):
            _check_source(src, dest, "move")
            if src.parent.resolve() == real_dest:
                log.info("move: %s is already in %s", src, dest)
                continue
            if not os.access(src.parent, os.W_OK | os.X_OK):
                raise JobError(f"Cannot move {src.name}: no permission to change {src.parent}")
            items.append(_MoveItem(src, same_file_system(src, dest_dev)))
        if not items:
            raise JobError(f"Already in {dest.name or dest}")
        size = _Size()
        for item in items:
            if not item.same_fs:
                _measure(item.src, ctx, size, deletable=True)
    except OSError as exc:
        raise JobError(os_error_reason(exc, dest)) from exc
    _check_space(dest, size.bytes)
    progress = _Progress(ctx, max(size.weight, len(items)), "Moving")

    # 1. Copy what lives on another file system (cancellable).
    try:
        for item in items:
            if not item.same_fs:
                _copy_to_temp(item, dest, progress)
        ctx.check()
    except BaseException as exc:
        for item in items:
            if item.temp is not None:
                remove_tree(item.temp)
        if isinstance(exc, OSError):
            raise JobError(os_error_reason(exc, item.src), details=str(exc)) from exc
        raise

    # 2. Put everything in place; undo it all if one item fails. No cancelling from here.
    placed: list[_MoveItem] = []
    try:
        for item in items:
            if item.same_fs:
                try:
                    item.final = _place(item.src, dest / item.src.name)
                except OSError as exc:
                    if exc.errno != errno.EXDEV:  # same device, different mount (bind mount)
                        raise
                    item.same_fs = False
                    _copy_to_temp(item, dest, progress)
            if not item.same_fs:
                item.final = _place(item.temp, dest / item.src.name)
                item.copied = True
            placed.append(item)
            progress.add(1)
    except BaseException as exc:
        _roll_back(placed)
        for item in items:
            if item.temp is not None and not item.copied:
                remove_tree(item.temp)
        if isinstance(exc, OSError):
            raise JobError(os_error_reason(exc, item.src), details=str(exc)) from exc
        raise
    for item in items:
        ctx.add_result(item.final)

    # 3. Delete the originals that were copied. Nothing is lost if this fails: the copy is
    # complete, so the item just exists twice.
    failed: list[tuple[_MoveItem, OSError]] = []
    for item in items:
        if item.copied:
            try:
                remove_tree(item.src)
            except OSError as exc:
                log.error("moved %s but could not remove the original: %s", item.src, exc)
                failed.append((item, exc))
    if failed:
        item, exc = failed[0]
        more = f" (and {len(failed) - 1} more)" if len(failed) > 1 else ""
        raise JobError(
            f"Copied {item.src.name}{more}, but could not remove the original: "
            + os_error_reason(exc, item.src)
        )
