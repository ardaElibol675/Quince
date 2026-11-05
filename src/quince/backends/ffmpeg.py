"""ffmpeg / ffprobe helpers: stream probing, duration and progress from ``-progress pipe:1``.

The conversion presets live in ``quince.backends.media``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from quince.backends import process
from quince.core.jobs import JobContext, JobError

PROBE_TIMEOUT_S = 60
_FLOAT_FORMATS = ("flt", "fltp", "dbl", "dblp")


@dataclass(frozen=True)
class Stream:
    """One stream of a media file, as ffprobe reports it."""

    index: int
    kind: str  # "video", "audio", "subtitle", "data", "attachment"
    codec: str
    channels: int = 0
    sample_rate: int = 0
    sample_fmt: str = ""
    bits: int = 0  # stored bits per sample (audio), e.g. 24 for 24-bit FLAC; 0 for lossy
    width: int = 0
    height: int = 0
    fps: float = 0.0
    attached_pic: bool = False  # cover art stored as a video stream

    @property
    def is_float(self) -> bool:
        return self.sample_fmt in _FLOAT_FORMATS


@dataclass(frozen=True)
class MediaInfo:
    streams: tuple[Stream, ...]
    duration: float | None = None

    @property
    def video(self) -> Stream | None:
        """The main video stream (cover art does not count)."""
        return next((s for s in self.streams if s.kind == "video" and not s.attached_pic), None)

    @property
    def audio(self) -> tuple[Stream, ...]:
        return tuple(s for s in self.streams if s.kind == "audio")

    @property
    def subtitles(self) -> tuple[Stream, ...]:
        return tuple(s for s in self.streams if s.kind == "subtitle")

    @property
    def cover(self) -> Stream | None:
        return next((s for s in self.streams if s.kind == "video" and s.attached_pic), None)


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _rate(value) -> float:
    num, _, den = str(value or "").partition("/")
    try:
        return float(num) / float(den or 1) if float(den or 1) else 0.0
    except ValueError:
        return 0.0


def _bits(raw: dict) -> int:
    """The stored depth. Lossy codecs (MP3, AAC, Vorbis) have none: they decode to float,
    which says nothing about their quality, so 0 is returned for them."""
    return _int(raw.get("bits_per_raw_sample")) or _int(raw.get("bits_per_sample"))


def parse_probe(text: str) -> MediaInfo:
    """``ffprobe -show_streams -show_format -of json`` output → ``MediaInfo``."""
    data = json.loads(text or "{}")
    streams = []
    for raw in data.get("streams", []):
        kind = str(raw.get("codec_type", ""))
        streams.append(
            Stream(
                index=_int(raw.get("index")),
                kind=kind,
                codec=str(raw.get("codec_name", "")),
                channels=_int(raw.get("channels")),
                sample_rate=_int(raw.get("sample_rate")),
                sample_fmt=str(raw.get("sample_fmt", "")),
                bits=_bits(raw) if kind == "audio" else 0,
                width=_int(raw.get("width")),
                height=_int(raw.get("height")),
                fps=_rate(raw.get("avg_frame_rate")) or _rate(raw.get("r_frame_rate")),
                attached_pic=bool(raw.get("disposition", {}).get("attached_pic")),
            )
        )
    try:
        duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    return MediaInfo(tuple(streams), duration if duration and duration > 0 else None)


def probe_text(path: Path, ctx: JobContext | None = None) -> str:
    """ffprobe's JSON for the streams and the container (with their tags)."""
    lines: list[str] = []
    try:
        process.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
            ctx,
            timeout_s=PROBE_TIMEOUT_S,
            on_stdout=lines.append,
        )
    except process.BackendError as exc:
        raise JobError("Not a readable audio or video file", exc.details) from exc
    return "\n".join(lines)


def probe_json(path: Path, ctx: JobContext | None = None) -> dict:
    try:
        data = json.loads(probe_text(path, ctx) or "{}")
    except ValueError as exc:
        raise JobError("Cannot read the streams of this file", str(exc)) from exc
    return data if isinstance(data, dict) else {}


def probe(path: Path, ctx: JobContext | None = None) -> MediaInfo:
    """Streams and duration of a media file; ``JobError`` when ffprobe cannot read it."""
    text = probe_text(path, ctx)
    try:
        return parse_probe(text)
    except ValueError as exc:
        raise JobError("Cannot read the streams of this file", str(exc)) from exc


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
    report: Callable[[float | None], None] | None = None,
    on_stderr: Callable[[str], None] | None = None,
) -> process.ProcessResult:
    """Run ``ffmpeg BASE_ARGS args`` and report its progress to ``ctx`` (or ``report``)."""

    def to_ctx(fraction: float | None) -> None:
        if ctx is not None:
            ctx.progress(fraction)

    return process.run(
        ["ffmpeg", *BASE_ARGS, *args],
        ctx,
        timeout_s=timeout_s,
        on_stdout=progress_parser(duration_s, report or to_ctx),
        on_stderr=on_stderr,
    )


def sub_progress(
    ctx: JobContext | None, start: float, end: float
) -> Callable[[float | None], None]:
    """A ``report`` callback mapping 0..1 to ``start..end`` of the current file's progress."""

    def report(fraction: float | None) -> None:
        if ctx is not None:
            ctx.progress(None if fraction is None else start + fraction * (end - start))

    return report
