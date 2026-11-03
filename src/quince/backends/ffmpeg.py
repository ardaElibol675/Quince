"""ffmpeg / ffprobe helpers: duration probing and progress from ``-progress pipe:1``.

Phase 8 adds the conversion presets on top of these.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from quince.backends import process
from quince.core.jobs import JobContext

# Prepended to every ffmpeg call: no prompts, never overwrite, machine-readable progress.
BASE_ARGS = ("-hide_banner", "-nostdin", "-n", "-progress", "pipe:1", "-nostats")


def probe_duration(path: Path, ctx: JobContext | None = None) -> float | None:
    """Duration of a media file in seconds, or None if ffprobe cannot tell."""
    result = process.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ],
        ctx,
        timeout_s=30,
        check=False,
    )  # fmt: skip
    for line in result.stdout_tail:
        try:
            value = float(line)
        except ValueError:
            continue
        if value > 0:
            return value
    return None


def progress_parser(
    duration_s: float | None, report: Callable[[float | None], None]
) -> Callable[[str], None]:
    """A stdout line callback for ``-progress pipe:1`` that reports 0..1 (None if unknown)."""

    def on_line(line: str) -> None:
        key, _, value = line.partition("=")
        if key in ("out_time_us", "out_time_ms"):  # both are microseconds in ffmpeg
            try:
                seconds = int(value) / 1_000_000
            except ValueError:
                return
            report(min(1.0, seconds / duration_s) if duration_s else None)
        elif key == "progress" and value == "end":
            report(1.0)

    return on_line


def run_ffmpeg(
    args: Sequence[str | Path],
    ctx: JobContext | None,
    *,
    duration_s: float | None = None,
    timeout_s: float | None = None,
) -> process.ProcessResult:
    """Run ``ffmpeg BASE_ARGS args`` and report its progress to ``ctx``."""

    def report(fraction: float | None) -> None:
        if ctx is not None:
            ctx.progress(fraction)

    return process.run(
        ["ffmpeg", *BASE_ARGS, *args],
        ctx,
        timeout_s=timeout_s,
        on_stdout=progress_parser(duration_s, report),
    )
