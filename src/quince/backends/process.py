"""Running external programs for operations (ffmpeg, soffice, pandoc, ...).

Operations never call ``subprocess`` themselves (CLAUDE.md Section 4); they go through
``run``, which reads output line by line, keeps the end of stderr for error messages, kills
the program on cancellation or timeout, and never inherits a foreign ``PYTHONPATH``.

``run(..., offline=True)`` starts the program without network access (Hard Rule 3), for
programs that would otherwise fetch remote images or links (pandoc, LibreOffice): inside
an unprivileged user + network namespace (``unshare -rn``), where only loopback exists.
Where namespaces are unavailable, proxy variables pointing at a closed local port are the
fallback.
"""

from __future__ import annotations

import functools
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from quince.core.jobs import JobCancelled, JobContext, JobError

log = logging.getLogger(__name__)

POLL_S = 0.1
TERMINATE_GRACE_S = 3.0
TAIL_LINES = 40


class BackendError(JobError):
    """An external program failed."""


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout_tail: tuple[str, ...]
    stderr_tail: tuple[str, ...]


DEAD_PROXY = "http://127.0.0.1:9"  # the discard port: nothing listens there


@functools.lru_cache(maxsize=1)
def offline_prefix() -> tuple[str, ...]:
    """The command prefix that cuts a program off the network, or () if none works."""
    unshare = shutil.which("unshare")
    if unshare:
        try:
            probe = subprocess.run(
                [unshare, "-rn", "true"], capture_output=True, timeout=10, check=False
            )
            if probe.returncode == 0:
                return (unshare, "-rn")
        except (OSError, subprocess.TimeoutExpired):
            pass
    log.warning("network namespaces unavailable; offline programs use a dead proxy instead")
    return ()


def offline_env() -> dict[str, str]:
    """Proxy settings that make HTTP clients fail at once (the fallback, and a second guard)."""
    return {
        key: DEAD_PROXY
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy")
    } | {"no_proxy": "", "NO_PROXY": ""}


def clean_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for child processes: without PYTHONPATH, plus ``extra``."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(extra or {})
    return env


def _reader(stream, tail: deque, callback: Callable[[str], None] | None) -> None:
    for raw in stream:
        line = raw.rstrip("\r\n")
        tail.append(line)
        if callback is not None:
            try:
                callback(line)
            except Exception:
                log.exception("output callback failed")
    stream.close()


def _stop(proc: subprocess.Popen) -> None:
    """Terminate the program and its children; kill them if they do not exit."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(TERMINATE_GRACE_S)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def failure_reason(name: str, stderr_tail: Sequence[str], returncode: int) -> str:
    """A short reason: the program name and its last meaningful error line."""
    for line in reversed(stderr_tail):
        line = line.strip()
        if line:
            return f"{name}: {line[:200]}"
    return f"{name} exited with code {returncode}"


def run(
    cmd: Sequence[str | Path],
    ctx: JobContext | None = None,
    *,
    timeout_s: float | None = None,
    on_stdout: Callable[[str], None] | None = None,
    on_stderr: Callable[[str], None] | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    check: bool = True,
    offline: bool = False,
) -> ProcessResult:
    """Run ``cmd`` to completion, reading its output line by line.

    Raises ``JobCancelled`` when ``ctx`` is cancelled (the program is killed),
    ``BackendError`` on a timeout, a missing program or (with ``check``) a non-zero exit.
    """
    args = [str(c) for c in cmd]
    name = Path(args[0]).name
    if offline:
        args = [*offline_prefix(), *args]
        env = {**offline_env(), **(env or {})}
    log.debug("running %s", " ".join(args))
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            env=clean_env(env),
            cwd=cwd,
            start_new_session=True,  # its own process group, so children die with it
        )
    except FileNotFoundError as exc:
        raise BackendError(f"{name} is not installed") from exc
    except OSError as exc:
        raise BackendError(f"Cannot start {name}: {exc.strerror}") from exc

    out_tail: deque[str] = deque(maxlen=TAIL_LINES)
    err_tail: deque[str] = deque(maxlen=TAIL_LINES)
    readers = [
        threading.Thread(target=_reader, args=(proc.stdout, out_tail, on_stdout), daemon=True),
        threading.Thread(target=_reader, args=(proc.stderr, err_tail, on_stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    try:
        while proc.poll() is None:
            if ctx is not None and ctx.cancelled:
                _stop(proc)
                raise JobCancelled()
            if deadline is not None and time.monotonic() > deadline:
                _stop(proc)
                raise BackendError(f"{name} took longer than {timeout_s:g} s and was stopped")
            time.sleep(POLL_S)
    except BaseException:
        if proc.poll() is None:
            _stop(proc)
        raise
    finally:
        for reader in readers:
            reader.join(2.0)

    result = ProcessResult(proc.returncode, tuple(out_tail), tuple(err_tail))
    if check and proc.returncode != 0:
        raise BackendError(
            failure_reason(name, result.stderr_tail, proc.returncode),
            details="\n".join(result.stderr_tail),
        )
    return result
