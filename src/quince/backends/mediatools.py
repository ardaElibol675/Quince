"""Audio and video tools with ffmpeg: Remove audio, Normalize audio, Join videos.

- Remove audio copies every other stream as it is (no re-encoding).
- Normalize audio measures every audio track (EBU R128, ffmpeg ``loudnorm`` first pass) and
  brings it to -16 LUFS integrated loudness, -1.5 dBTP true peak in a second pass. Audio is
  re-encoded with the format's own preset (FLAC / WAV stay lossless, at the original sample
  rate); video and subtitles are copied.
- Join videos copies the streams when every file has the same container, codecs, frame size
  and audio layout (no quality loss); otherwise it encodes one MP4 (H.264 / AAC) at the first
  video's size and frame rate, fitting the others in with black bars and silence where a file
  has no sound.
"""

from __future__ import annotations

import json
import logging
import math
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from quince.backends import ffmpeg
from quince.backends.media import AUDIO_CODECS, AUDIO_TARGETS, MUXERS, VIDEO_TARGETS
from quince.core.jobs import JobContext, JobError

log = logging.getLogger(__name__)

# --- remove audio -----------------------------------------------------------------------------


def remove_audio(ctx: JobContext, src: Path, format_id: str, out: Path) -> None:
    info = ffmpeg.probe(src, ctx)
    if info.video is None:
        raise JobError("There is no video in this file")
    if not info.audio:
        raise JobError("There is no sound to remove")
    args = ["-i", src, "-map", "0", "-map", "-0:a", "-map", "-0:d", "-c", "copy",
            "-map_metadata", "0"]  # fmt: skip
    args += list(VIDEO_TARGETS[format_id].extra) if format_id in VIDEO_TARGETS else []
    ffmpeg.run_ffmpeg([*args, "-f", MUXERS[format_id], out], ctx, duration_s=info.duration)


# --- normalize audio --------------------------------------------------------------------------

TARGET_LUFS = -16.0
TARGET_PEAK = -1.5
TARGET_LRA = 11.0
_JSON_BLOCK = re.compile(r"\{[^{}]*\}", re.S)


@dataclass(frozen=True)
class Loudness:
    integrated: float  # LUFS
    true_peak: float  # dBTP
    lra: float
    threshold: float
    offset: float


def parse_loudnorm(stderr: str) -> Loudness:
    """The measurement ``loudnorm=print_format=json`` prints at the end of a first pass."""
    blocks = _JSON_BLOCK.findall(stderr)
    for block in reversed(blocks):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if "input_i" not in data:
            continue
        try:
            values = [float(data[k]) for k in ("input_i", "input_tp", "input_lra",
                                               "input_thresh", "target_offset")]  # fmt: skip
        except (KeyError, TypeError, ValueError):
            break
        if not all(math.isfinite(v) for v in values):
            raise JobError("The sound is silent; there is nothing to normalize")
        return Loudness(*values)
    raise JobError("ffmpeg could not measure the loudness")


def _loudnorm(m: Loudness | None = None) -> str:
    base = f"loudnorm=I={TARGET_LUFS}:TP={TARGET_PEAK}:LRA={TARGET_LRA}"
    if m is None:
        return base + ":print_format=json"
    return (
        f"{base}:measured_I={m.integrated}:measured_TP={m.true_peak}:measured_LRA={m.lra}"
        f":measured_thresh={m.threshold}:offset={m.offset}:linear=true:print_format=summary"
    )


def measure(ctx: JobContext, src: Path, track: int, duration: float | None, span) -> Loudness:
    lines: list[str] = []
    ffmpeg.run_ffmpeg(
        ["-i", src, "-map", f"0:a:{track}", "-af", _loudnorm(), "-f", "null", "-"],
        ctx,
        duration_s=duration,
        report=ffmpeg.sub_progress(ctx, *span),
        on_stderr=lines.append,
    )
    return parse_loudnorm("\n".join(lines))


def _audio_codec(format_id: str) -> str:
    if format_id in AUDIO_TARGETS:
        return AUDIO_TARGETS[format_id].codec
    return VIDEO_TARGETS[format_id].audio_codec


def normalize(ctx: JobContext, src: Path, format_id: str, out: Path) -> None:
    info = ffmpeg.probe(src, ctx)
    if not info.audio:
        raise JobError("There is no sound in this file")
    tracks = len(info.audio)
    measured = []
    for i in range(tracks):
        span = (i / (tracks + 1), (i + 1) / (tracks + 1))
        ctx.progress(span[0], f"Measuring loudness{f' (track {i + 1})' if tracks > 1 else ''}")
        measured.append(measure(ctx, src, i, info.duration, span))
    codec = AUDIO_CODECS[_audio_codec(format_id)]
    args: list = ["-i", src, "-map", "0", "-map", "-0:d", "-c", "copy", "-map_metadata", "0"]
    for i, (stream, m) in enumerate(zip(info.audio, measured, strict=True)):
        args += [f"-filter:a:{i}", _loudnorm(m)]
        args += codec.args(stream, f"a:{i}")
        rate = stream.sample_rate or 48000
        if codec.max_rate:
            rate = min(rate, codec.max_rate)
        args += [f"-ar:a:{i}", str(rate)]
    if format_id in VIDEO_TARGETS:
        args += list(VIDEO_TARGETS[format_id].extra)
    ctx.progress(tracks / (tracks + 1), "Normalizing")
    ffmpeg.run_ffmpeg(
        [*args, "-f", MUXERS[format_id], out],
        ctx,
        duration_s=info.duration,
        report=ffmpeg.sub_progress(ctx, tracks / (tracks + 1), 1.0),
    )
    before = ", ".join(f"{m.integrated:.1f}" for m in measured)
    ctx.note(f"Loudness {before} → {TARGET_LUFS:.0f} LUFS")


# --- join videos ------------------------------------------------------------------------------

JOIN_RATE = 48000
JOIN_ENCODER = ("-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p")


def _signature(info: ffmpeg.MediaInfo) -> tuple:
    v = info.video
    audio = tuple((a.codec, a.channels, a.sample_rate) for a in info.audio)
    return (v.codec, v.width, v.height, round(v.fps, 2), audio)


def can_copy(infos: Sequence[ffmpeg.MediaInfo], format_ids: Sequence[str]) -> bool:
    """Streams can be joined without re-encoding (the concat demuxer)."""
    return len(set(format_ids)) == 1 and len({_signature(i) for i in infos}) == 1


def _concat_list(paths: Sequence[Path]) -> str:
    lines = []
    for path in paths:
        escaped = str(path).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def join_filter(infos: Sequence[ffmpeg.MediaInfo], width: int, height: int, fps: float) -> str:
    """The ``-filter_complex`` that scales, pads and concatenates every input."""
    with_audio = any(i.audio for i in infos)
    parts, labels = [], []
    for n, info in enumerate(infos):
        parts.append(
            f"[{n}:{info.video.index}]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:g},format=yuv420p[v{n}]"
        )
        labels.append(f"[v{n}]")
        if with_audio:
            if info.audio:
                parts.append(
                    f"[{n}:{info.audio[0].index}]aresample={JOIN_RATE},"
                    f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{n}]"
                )
            else:
                duration = info.duration or 0.0
                parts.append(f"aevalsrc=0|0:d={duration:.3f}:s={JOIN_RATE}[a{n}]")
            labels.append(f"[a{n}]")
    audio = 1 if with_audio else 0
    parts.append(f"{''.join(labels)}concat=n={len(infos)}:v=1:a={audio}[v]" +
                 ("[a]" if with_audio else ""))  # fmt: skip
    return ";".join(parts)


@dataclass(frozen=True)
class JoinPlan:
    infos: tuple[ffmpeg.MediaInfo, ...]
    copy: bool
    out_format: str  # the first file's format when copying, else mp4


def plan_join(ctx: JobContext, paths: Sequence[Path], format_ids: Sequence[str]) -> JoinPlan:
    infos = []
    for path in paths:
        info = ffmpeg.probe(path, ctx)
        if info.video is None:
            raise JobError(f"{path.name} has no video")
        infos.append(info)
    copy = can_copy(infos, format_ids)
    return JoinPlan(tuple(infos), copy, format_ids[0] if copy else "mp4")


def join(ctx: JobContext, paths: Sequence[Path], plan: JoinPlan, out: Path) -> None:
    """Join ``paths`` into ``out`` as planned by ``plan_join``."""
    infos = plan.infos
    total = sum(i.duration or 0.0 for i in infos) or None
    if plan.copy:
        out_format = plan.out_format
        with tempfile.TemporaryDirectory(prefix="quince-join-") as tmp:
            listing = Path(tmp) / "list.txt"
            listing.write_text(_concat_list(paths), encoding="utf-8")
            args = ["-f", "concat", "-safe", "0", "-i", listing, "-map", "0", "-c", "copy"]
            args += list(VIDEO_TARGETS[out_format].extra)
            ffmpeg.run_ffmpeg([*args, "-f", MUXERS[out_format], out], ctx, duration_s=total)
        ctx.note("Joined without re-encoding (no quality loss)")
        return
    first = infos[0].video
    width, height = max(2, first.width // 2 * 2), max(2, first.height // 2 * 2)
    fps = first.fps if 0 < first.fps <= 120 else 30.0
    args: list = []
    for path in paths:
        args += ["-i", path]
    graph = join_filter(infos, width, height, fps)
    args += ["-filter_complex", graph, "-map", "[v]"]
    if any(i.audio for i in infos):
        args += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    args += [*JOIN_ENCODER, *VIDEO_TARGETS["mp4"].extra, "-f", MUXERS["mp4"], out]
    ffmpeg.run_ffmpeg(args, ctx, duration_s=total)
    ctx.note(f"Encoded as one MP4 at {width} × {height}: the videos differ")
