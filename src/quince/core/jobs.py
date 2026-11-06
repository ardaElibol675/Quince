"""Background jobs: a queue of worker threads with progress, cancellation and timeouts.

An operation's ``run(ctx)`` does the work on a worker thread. It writes every output to a
temporary path from ``ctx.new_output()``; only when ``run`` returns are the outputs moved to
their final names, atomically and never over an existing file (CLAUDE.md Hard Rule 4).
When ``run`` fails, is cancelled or times out, all temporary outputs are deleted, so no
partial file is ever left behind.

Cancellation is cooperative: ``ctx.check()`` raises when the job was cancelled or ran out
of time, and the process wrapper (``quince.backends.process``) kills external programs.

A job that puts its results in place itself (Move) reports them with ``ctx.add_result()``;
it is then responsible for leaving a consistent state when it fails.

Batch jobs wrap each file in ``with ctx.item(path):``. A file that fails there loses only its
own outputs and is reported; the others are kept. The job fails only when every file failed.
``ctx.note()`` adds a remark for the success card ("Only the first frame was kept").
"""

from __future__ import annotations

import ctypes
import errno
import itertools
import logging
import os
import secrets
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from quince.core import naming
from quince.core.config import OutputConfig
from quince.core.selection import Selection

log = logging.getLogger(__name__)

DEFAULT_WORKERS = 2
WATCHDOG_INTERVAL_S = 0.2
PROGRESS_INTERVAL_S = 0.1  # at most ~10 progress events per second and job
TEMP_PREFIX = ".quince-tmp-"


class JobState(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def finished(self) -> bool:
        return self in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED)


class JobError(Exception):
    """A failure with a short reason for the user; ``details`` go to the log."""

    def __init__(self, reason: str, details: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details


class JobCancelled(Exception):
    pass


@dataclass(frozen=True)
class JobSnapshot:
    """What the UI needs to show a job; sent on every change."""

    id: int
    title: str
    op_id: str
    sources: tuple[Path, ...]
    state: JobState = JobState.QUEUED
    progress: float | None = None  # 0..1, or None when unknown
    message: str = ""
    outputs: tuple[Path, ...] = ()
    error: str = ""
    notes: tuple[str, ...] = ()  # remarks on a success ("Only the first frame was kept")
    failures: tuple[str, ...] = ()  # files that failed in a batch that still succeeded

    @property
    def partial(self) -> bool:
        return self.state is JobState.SUCCEEDED and bool(self.failures)


@dataclass
class _Output:
    temp: Path
    final: Path
    is_dir: bool


# --- moving outputs into place ---------------------------------------------------

_RENAME_NOREPLACE = 1
_AT_FDCWD = -100
try:
    _libc = ctypes.CDLL(None, use_errno=True)
    _renameat2 = _libc.renameat2
    _renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    _renameat2.restype = ctypes.c_int
except (OSError, AttributeError):  # pragma: no cover - glibc < 2.28
    _renameat2 = None


def move_no_replace(src: Path, dst: Path) -> None:
    """Rename ``src`` to ``dst`` atomically; ``FileExistsError`` if ``dst`` exists.

    Uses ``renameat2(RENAME_NOREPLACE)``. File systems without it get a hard link plus
    unlink for files, and a checked rename for folders.
    """
    if _renameat2 is not None:
        if (
            _renameat2(_AT_FDCWD, os.fsencode(src), _AT_FDCWD, os.fsencode(dst), _RENAME_NOREPLACE)
            == 0
        ):
            return
        err = ctypes.get_errno()
        if err == errno.EEXIST:
            raise FileExistsError(errno.EEXIST, "destination exists", str(dst))
        if err not in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP):
            raise OSError(err, os.strerror(err), str(dst))
    if src.is_dir():
        if os.path.lexists(dst):
            raise FileExistsError(errno.EEXIST, "destination exists", str(dst))
        os.rename(src, dst)
        return
    # Fails with FileExistsError instead of replacing; a symlink is linked, not its target.
    os.link(src, dst, follow_symlinks=False)
    os.unlink(src)


def _make_writable(func, path, _exc_info) -> None:
    """``rmtree`` error handler: a read-only folder (copied as such) blocks deleting its
    contents, so make the parent writable and retry once."""
    parent = os.path.dirname(path)
    try:
        os.chmod(parent, os.stat(parent).st_mode | 0o700)
        func(path)
    except OSError:
        raise _exc_info[1] from None


def remove_tree(path: Path) -> None:
    """Delete a file, symlink or folder tree, also when folders inside are read-only."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, onerror=_make_writable)
    else:
        path.unlink(missing_ok=True)


def _remove(path: Path) -> None:
    try:
        remove_tree(path)
    except OSError as exc:
        log.warning("could not remove temporary %s: %s", path, exc)


# --- the context a job runs with -------------------------------------------------


class JobContext:
    """Passed to ``Operation.run``: the selection, progress, cancellation and outputs."""

    def __init__(
        self,
        queue: JobQueue,
        job_id: int,
        selection: Selection,
        output: OutputConfig,
        cancel: threading.Event,
        deadline: float | None,
    ) -> None:
        self.selection = selection
        self.job_id = job_id
        self._queue = queue
        self._output = output
        self._cancel = cancel
        self._deadline = deadline
        self.timed_out = False
        self.outputs: list[_Output] = []
        self.results: list[Path] = []
        self.notes: list[str] = []
        self.failures: list[tuple[Path, str]] = []
        self.items_done = 0
        self._span = (0.0, 1.0)  # progress range of the current item
        self._last_progress = 0.0

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def remaining(self) -> float | None:
        """Seconds left before the timeout, or None without one."""
        return None if self._deadline is None else max(0.0, self._deadline - time.monotonic())

    def check(self) -> None:
        """Raise if the job should stop (cancelled or out of time)."""
        if self._deadline is not None and time.monotonic() > self._deadline:
            self.timed_out = True
            self._cancel.set()
        if self._cancel.is_set():
            raise JobCancelled()

    def progress(self, fraction: float | None = None, message: str | None = None) -> None:
        if fraction is not None:
            start, end = self._span
            fraction = start + min(1.0, max(0.0, fraction)) * (end - start)
        now = time.monotonic()
        if message is None and fraction != 1.0 and now - self._last_progress < PROGRESS_INTERVAL_S:
            return
        self._last_progress = now
        self._queue._update(self.job_id, progress=fraction, message=message)

    def new_output(
        self,
        source: Path,
        *,
        extension: str | None = None,
        suffix: str | None = None,
        directory: bool = False,
        target_dir: Path | None = None,
        name: str | None = None,
    ) -> Path:
        """A temporary path to write one output to; returns it.

        The final name (next to ``source``, in the configured folder, or in ``target_dir``
        when given) is reserved now and taken when the job succeeds. With ``directory``, a
        temporary folder is created. ``name`` replaces the source's stem (a name the user
        chose); it is still made free like any other.
        """
        if target_dir is None:
            target_dir = self._output.directory_for(Path(source))
        if not os.access(target_dir, os.W_OK | os.X_OK):
            raise JobError(f"Cannot write to {target_dir}")
        final = self._queue._reserve(source, target_dir, extension, suffix, name)
        temp = target_dir / f"{TEMP_PREFIX}{secrets.token_hex(4)}-{final.name}"
        if directory:
            temp.mkdir()
        self.outputs.append(_Output(temp, final, directory))
        return temp

    def discard_output(self, temp: Path) -> None:
        """Give up an output from ``new_output`` (deleted, its name freed)."""
        dropped = [out for out in self.outputs if out.temp == Path(temp)]
        self.outputs = [out for out in self.outputs if out.temp != Path(temp)]
        self._queue._discard(dropped)

    def add_result(self, path: Path) -> None:
        """Report an output the job already put at its final place (not a temporary one)."""
        self.results.append(Path(path))

    def note(self, text: str) -> None:
        """A remark shown with the result; the same text is shown once."""
        if text not in self.notes:
            self.notes.append(text)

    @contextmanager
    def item(self, source: Path, span: tuple[float, float] = (0.0, 1.0)) -> Iterator[None]:
        """One file of a batch: if it fails, only its outputs are dropped and the failure is
        recorded; cancellation still stops the whole job. Inside, progress 0..1 is mapped to
        ``span`` of the whole job, so a backend reports its own file's progress."""
        start = len(self.outputs)
        self._span = span
        try:
            yield
        except JobCancelled:
            raise
        except Exception as exc:
            if isinstance(exc, JobError):
                reason = exc.reason
                log.warning(
                    "job %d: %s failed: %s%s",
                    self.job_id,
                    source,
                    reason,
                    f"\n{exc.details}" if exc.details else "",
                )
            else:
                reason = f"Unexpected error: {exc}"
                log.exception("job %d: %s crashed", self.job_id, source)
            dropped, self.outputs[start:] = self.outputs[start:], []
            self._queue._discard(dropped)
            self.failures.append((Path(source), reason))
        else:
            self.items_done += 1
        finally:
            self._span = (0.0, 1.0)


def _failure_summary(failures: list[tuple[Path, str]]) -> str:
    """The error of a batch where every file failed."""
    path, reason = failures[0]
    if len(failures) == 1:
        return f"{path.name}: {reason}"
    return f"{len(failures)} files failed; {path.name}: {reason}"


# --- the queue -------------------------------------------------------------------


@dataclass
class _Job:
    snapshot: JobSnapshot
    run: Callable[[JobContext], None]
    selection: Selection
    output: OutputConfig
    timeout_s: float | None
    cancel: threading.Event = field(default_factory=threading.Event)
    deadline: float | None = None


class JobQueue:
    """Runs jobs on worker threads; reports every change through ``on_event``.

    ``on_event`` is called on worker threads (and on the caller's thread for submit and
    cancel); a UI must hop to its own thread.
    """

    def __init__(
        self,
        *,
        workers: int = DEFAULT_WORKERS,
        on_event: Callable[[JobSnapshot], None] | None = None,
    ) -> None:
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="quince-job")
        self._on_event = on_event or (lambda _s: None)
        self._lock = threading.Lock()
        self._jobs: dict[int, _Job] = {}
        self._ids = itertools.count(1)
        self._reserved: set[Path] = set()
        self._stop = threading.Event()
        self._watchdog = threading.Thread(
            target=self._watch, name="quince-job-watchdog", daemon=True
        )
        self._watchdog.start()

    # --- public API ------------------------------------------------------------

    def submit(
        self,
        run: Callable[[JobContext], None],
        selection: Selection,
        *,
        title: str,
        op_id: str = "",
        output: OutputConfig | None = None,
        timeout_s: float | None = None,
    ) -> int:
        job_id = next(self._ids)
        snapshot = JobSnapshot(job_id, title, op_id, tuple(i.path for i in selection.items))
        job = _Job(snapshot, run, selection, output or OutputConfig(), timeout_s)
        with self._lock:
            self._jobs[job_id] = job
        log.info("job %d queued: %s on %d item(s)", job_id, title, len(selection))
        self._on_event(snapshot)
        self._executor.submit(self._run, job)
        return job_id

    def cancel(self, job_id: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None and not job.snapshot.state.finished:
            log.info("job %d: cancel requested", job_id)
            job.cancel.set()

    def cancel_all(self) -> None:
        with self._lock:
            ids = list(self._jobs)
        for job_id in ids:
            self.cancel(job_id)

    def snapshot(self, job_id: int) -> JobSnapshot:
        with self._lock:
            return self._jobs[job_id].snapshot

    def shutdown(self, wait: bool = True) -> None:
        self.cancel_all()
        self._stop.set()
        self._executor.shutdown(wait=wait)

    # --- internals ---------------------------------------------------------------

    def _update(self, job_id: int, **changes) -> JobSnapshot:
        changes = {k: v for k, v in changes.items() if v is not None or k == "progress"}
        with self._lock:
            job = self._jobs[job_id]
            job.snapshot = replace(job.snapshot, **changes)
            snapshot = job.snapshot
        self._on_event(snapshot)
        return snapshot

    def _reserve(
        self,
        source: Path,
        directory: Path,
        extension: str | None,
        suffix: str | None,
        stem: str | None = None,
    ) -> Path:
        with self._lock:
            final = naming.output_path(
                Path(source),
                extension=extension,
                suffix=suffix,
                directory=directory,
                reserved=self._reserved,
                stem=stem,
            )
            self._reserved.add(final)
        return final

    def _unreserve(self, outputs: list[_Output]) -> None:
        with self._lock:
            for out in outputs:
                self._reserved.discard(out.final)

    def _watch(self) -> None:
        """Turn timeouts into cancellation, so running processes get killed promptly."""
        while not self._stop.wait(WATCHDOG_INTERVAL_S):
            now = time.monotonic()
            with self._lock:
                jobs = list(self._jobs.values())
            for job in jobs:
                if job.deadline is not None and now > job.deadline and not job.cancel.is_set():
                    log.info("job %d: timed out", job.snapshot.id)
                    job.cancel.set()

    def _run(self, job: _Job) -> None:
        job_id = job.snapshot.id
        if job.cancel.is_set():
            self._update(job_id, state=JobState.CANCELLED, message="Cancelled")
            return
        if job.timeout_s is not None:
            job.deadline = time.monotonic() + job.timeout_s
        ctx = JobContext(self, job_id, job.selection, job.output, job.cancel, job.deadline)
        self._update(job_id, state=JobState.RUNNING)
        try:
            job.run(ctx)
            if ctx.outputs:
                ctx.check()  # a late cancel still wins before temporary outputs move in
            if ctx.failures and not ctx.items_done:
                raise JobError(_failure_summary(ctx.failures))
            outputs = [*ctx.results, *self._commit(ctx.outputs)]
        except JobCancelled:
            self._discard(ctx.outputs)
            if ctx.timed_out or (job.deadline is not None and time.monotonic() > job.deadline):
                reason = f"Took longer than {job.timeout_s:g} s and was stopped"
                log.info("job %d failed: %s", job_id, reason)
                self._update(job_id, state=JobState.FAILED, error=reason)
            else:
                log.info("job %d cancelled", job_id)
                self._update(job_id, state=JobState.CANCELLED, message="Cancelled")
        except JobError as exc:
            self._discard(ctx.outputs)
            log.warning(
                "job %d failed: %s%s", job_id, exc.reason, f"\n{exc.details}" if exc.details else ""
            )
            self._update(job_id, state=JobState.FAILED, error=exc.reason)
        except Exception as exc:
            self._discard(ctx.outputs)
            log.exception("job %d crashed", job_id)
            self._update(job_id, state=JobState.FAILED, error=f"Unexpected error: {exc}")
        else:
            names = ", ".join(str(p) for p in outputs)
            log.info("job %d succeeded: %s", job_id, names or "no output")
            failures = tuple(f"{p.name}: {reason}" for p, reason in ctx.failures)
            if failures:
                total = ctx.items_done + len(failures)
                log.info("job %d: %d of %d file(s) failed", job_id, len(failures), total)
            self._update(
                job_id,
                state=JobState.SUCCEEDED,
                progress=1.0,
                outputs=tuple(outputs),
                notes=tuple(ctx.notes),
                failures=failures,
            )

    def _commit(self, outputs: list[_Output]) -> list[Path]:
        """Move every temporary output to its final name; never replace an existing file."""
        done: list[Path] = []
        try:
            for out in outputs:
                if not os.path.lexists(out.temp):
                    raise JobError(f"The output {out.final.name} was not written")
                final = out.final
                while True:
                    try:
                        move_no_replace(out.temp, final)
                        break
                    except FileExistsError:
                        # Someone created the name meanwhile: take the next free one.
                        final = naming.unique_path(final, self._reserved)
                done.append(final)
        except BaseException:
            for path in done:  # all or nothing
                _remove(path)
            raise
        finally:
            self._unreserve(outputs)
        return done

    def _discard(self, outputs: list[_Output]) -> None:
        for out in outputs:
            _remove(out.temp)
        self._unreserve(outputs)
