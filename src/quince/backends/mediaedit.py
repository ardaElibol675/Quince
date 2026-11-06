"""The pieces of the Video and Sound Editors' work with ffmpeg: reading a file for an editor
(streams, keyframes, waveform), and the filters and encoder arguments that
``quince.backends.mediapipeline`` puts together into one command (redacted areas, channels,
bleeps, speed, split points, the picture of a sound) plus the Video mode's preview.

The builders are pure, so the rules are unit tested without running ffmpeg. Whatever is
encoded uses the target container's own preset from ``quince.backends.media`` (H.264 CRF 20
for MP4/MOV/MKV, ...). Times are seconds from the start of the file, as the editor's player
shows them.
"""

from __future__ import annotations

import bisect
import contextlib
import json
import math
import sys
import tempfile
from array import array
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from quince.backends import ffmpeg, imageedit, process
from quince.backends.ffmpeg import MediaInfo, Stream
from quince.backends.media import (
    AUDIO_CODECS,
    AUDIO_TARGETS,
    COVER_CODECS,
    MUXERS,
    VIDEO_ENCODERS,
    VIDEO_TARGETS,
)
from quince.core.jobs import JobContext, JobError

MIN_PART_S = 0.1  # shorter parts are not made
MAX_PARTS = 500
WAVE_RATE = 4000  # samples per second decoded for the waveform
WAVE_BUCKETS = 3000
KEYFRAME_TIMEOUT_S = 600
SPEED_RANGE = (0.25, 4.0)
BLEEP_HZ = 1000
BLEEP_LEVEL = 0.3  # about -10 dBFS


def _t(seconds: float) -> str:
    return f"{max(0.0, seconds):.3f}"


# --- what an editor knows about a file ---------------------------------------------------------


@dataclass(frozen=True)
class Source:
    path: Path
    format_id: str
    info: MediaInfo
    size: tuple[int, int] = (0, 0)  # the video as shown (rotation applied); (0, 0) without

    @property
    def duration(self) -> float:
        return self.info.duration or 0.0

    @property
    def fps(self) -> float:
        video = self.info.video
        return video.fps if video is not None and video.fps > 0 else 0.0

    @property
    def is_video(self) -> bool:
        return self.format_id in VIDEO_TARGETS

    @property
    def channels(self) -> int:
        return self.info.audio[0].channels if self.info.audio else 0


def _rotation(raw: dict) -> int:
    try:
        rotate = int(float(raw.get("tags", {}).get("rotate", 0)))
    except (TypeError, ValueError):
        rotate = 0
    for side in raw.get("side_data_list", ()) or ():
        if isinstance(side, dict) and "rotation" in side:
            with contextlib.suppress(TypeError, ValueError):
                rotate = int(float(side["rotation"]))
    return rotate % 360


def display_size(data: dict, info: MediaInfo) -> tuple[int, int]:
    """The size the video is shown at: width and height swap for a quarter-turn rotation."""
    video = info.video
    if video is None:
        return (0, 0)
    raw = next((s for s in data.get("streams", []) if s.get("index") == video.index), {})
    if _rotation(raw) in (90, 270):
        return (video.height, video.width)
    return (video.width, video.height)


def inspect(path: Path, format_id: str, ctx: JobContext | None = None) -> Source:
    text = ffmpeg.probe_text(path, ctx)
    try:
        data = json.loads(text or "{}")
        info = ffmpeg.parse_probe(text)
    except ValueError as exc:
        raise JobError("Cannot read the streams of this file", str(exc)) from exc
    if not info.duration:
        raise JobError("The length of this file is unknown")
    return Source(Path(path), format_id, info, display_size(data, info))


def keyframes(path: Path, ctx: JobContext | None = None) -> tuple[float, ...]:
    """Times of the video's keyframes (where a fast cut can start), sorted."""
    times: list[float] = []

    def on_line(line: str) -> None:
        stamp, _, flags = line.strip().partition(",")
        if "K" not in flags:
            return
        with contextlib.suppress(ValueError):
            times.append(float(stamp))

    process.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", path],
        ctx,
        timeout_s=KEYFRAME_TIMEOUT_S,
        on_stdout=on_line,
        check=False,
    )  # fmt: skip
    return tuple(sorted(set(times)))


def snap_back(t: float, keys: Sequence[float]) -> float:
    """The keyframe at or before ``t`` (0 without one)."""
    i = bisect.bisect_right(keys, t + 1e-6) - 1
    return keys[i] if i >= 0 else 0.0


# --- waveform ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Waveform:
    """Peak levels (0..1) per time bucket, one lane per channel (or one mixed lane)."""

    lanes: tuple[tuple[float, ...], ...]

    @property
    def channels(self) -> int:
        return len(self.lanes)


def read_peaks(raw: Path, channels: int, buckets: int = WAVE_BUCKETS) -> Waveform:
    """Peaks of a raw signed 16-bit little-endian file, read a bucket at a time."""
    frames = raw.stat().st_size // (2 * channels)
    lanes: list[list[float]] = [[] for _ in range(channels)]
    if frames:
        per = max(1, math.ceil(frames / buckets))
        with raw.open("rb") as f:
            while chunk := f.read(per * channels * 2):
                samples = array("h")
                samples.frombytes(chunk[: len(chunk) // (2 * channels) * 2 * channels])
                if sys.byteorder == "big":
                    samples.byteswap()
                for c in range(channels):
                    part = samples[c::channels]
                    if part:
                        lanes[c].append(min(1.0, max(max(part), -min(part)) / 32767))
    return Waveform(tuple(tuple(lane) for lane in lanes))


def waveform(source: Source, *, per_channel: bool = False, ctx: JobContext | None = None):
    """The first audio track's waveform; None without sound."""
    if not source.info.audio:
        return None
    channels = min(8, max(1, source.channels)) if per_channel else 1
    with tempfile.TemporaryDirectory(prefix="quince-wave-") as tmp:
        raw = Path(tmp) / "wave.raw"
        process.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", source.path,
             "-map", f"0:{source.info.audio[0].index}", "-ac", str(channels),
             "-ar", str(WAVE_RATE), "-c:a", "pcm_s16le", "-f", "s16le", raw],
            ctx,
            timeout_s=KEYFRAME_TIMEOUT_S,
        )  # fmt: skip
        return read_peaks(raw, channels)


# --- shared argument pieces -----------------------------------------------------------------


def _audio_codec_key(format_id: str) -> str:
    if format_id in AUDIO_TARGETS:
        return AUDIO_TARGETS[format_id].codec
    return VIDEO_TARGETS[format_id].audio_codec


def encode_audio(stream: Stream, format_id: str, spec: str, channels: int | None = None) -> list:
    """Encode one audio stream with the container's preset (``channels``: after a filter)."""
    codec = AUDIO_CODECS[_audio_codec_key(format_id)]
    shaped = stream if channels is None else _with_channels(stream, channels)
    args = list(codec.args(shaped, spec))
    if codec.max_channels is not None and shaped.channels > codec.max_channels:
        args += [f"-ac:{spec}", str(codec.max_channels)]
    if codec.max_rate is not None and stream.sample_rate > codec.max_rate:
        args += [f"-ar:{spec}", str(codec.max_rate)]
    return args


def _with_channels(stream: Stream, channels: int) -> Stream:
    return replace(stream, channels=channels)


def encode_video(format_id: str) -> list[str]:
    return list(VIDEO_ENCODERS[VIDEO_TARGETS[format_id].video_encoder])


def _attachment_maps(info: MediaInfo, format_id: str) -> list[str]:
    if not VIDEO_TARGETS[format_id].attachments:
        return []
    found = [s for s in info.streams if s.kind == "attachment"]
    args: list[str] = []
    for s in found:
        args += ["-map", f"0:{s.index}"]
    return [*args, "-c:t", "copy"] if found else args


def _cover_maps(info: MediaInfo, format_id: str) -> list[str]:
    cover = info.cover
    if cover is None or cover.codec not in COVER_CODECS.get(format_id, ()):
        return []
    return ["-map", f"0:{cover.index}", "-c:v", "copy", "-disposition:v:0", "attached_pic"]


def _tail(format_id: str) -> list[str]:
    extra = list(VIDEO_TARGETS[format_id].extra) if format_id in VIDEO_TARGETS else []
    if format_id == "mp3":
        extra += ["-id3v2_version", "3"]
    return [*extra, "-f", MUXERS[format_id]]


# --- trim and split ---------------------------------------------------------------------------


def split_points(
    duration: float, *, markers: Sequence[float] = (), parts: int = 0, every: float = 0.0
) -> tuple[float, ...]:
    """Where to cut: at ``markers``, into ``parts`` equal parts, or ``every`` seconds."""
    if parts > 1:
        points = [duration * k / parts for k in range(1, parts)]
    elif every > 0:
        count = min(MAX_PARTS + 1, math.ceil(duration / every))
        points = [k * every for k in range(1, count)]
    else:
        points = list(markers)
    inside = sorted(p for p in points if MIN_PART_S <= p <= duration - MIN_PART_S)
    kept: list[float] = []
    for p in inside:
        if not kept or p - kept[-1] >= MIN_PART_S:
            kept.append(p)
    return tuple(kept)


# --- crop -------------------------------------------------------------------------------------

Box = tuple[float, float, float, float]


def even_box(box: Box, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """``box`` as whole even pixels inside ``size`` (x, y, width, height); at least 2 x 2."""
    w_max, h_max = size
    x0 = max(0, min(w_max - 2, int(box[0]) // 2 * 2))
    y0 = max(0, min(h_max - 2, int(box[1]) // 2 * 2))
    w = max(2, min(w_max - x0, round(box[2] - x0)) // 2 * 2)
    h = max(2, min(h_max - y0, round(box[3] - y0)) // 2 * 2)
    return x0, y0, w, h


# --- speed ------------------------------------------------------------------------------------


def atempo_chain(factor: float) -> str:
    """``atempo`` filters for ``factor`` (each one between 0.5 and 2, so any ffmpeg takes it)."""
    steps: list[float] = []
    while factor > 2.0 + 1e-9:
        steps.append(2.0)
        factor /= 2.0
    while factor < 0.5 - 1e-9:
        steps.append(0.5)
        factor /= 0.5
    steps.append(factor)
    return ",".join(f"atempo={s:.6g}" for s in steps)


def speed_audio_filter(factor: float, stream: Stream, keep_pitch: bool) -> str:
    if keep_pitch:
        return atempo_chain(factor)
    rate = stream.sample_rate or 48000
    return f"asetrate={round(rate * factor)},aresample={rate}"


# --- snapshots --------------------------------------------------------------------------------

SNAPSHOT_FORMATS = {"png": ["-c:v", "png"], "jpg": ["-c:v", "mjpeg", "-q:v", "2"]}


def snapshot_time(source: Source, t: float) -> float:
    """``t`` kept before the last frame, where a picture still exists."""
    frame = 1 / source.fps if source.fps else 0.04
    return max(0.0, min(t, source.duration - frame))


def clock(t: float) -> str:
    minutes, seconds = divmod(max(0.0, t), 60)
    hours, minutes = divmod(int(minutes), 60)
    text = f"{minutes:d}:{seconds:05.2f}"
    return f"{hours:d}:{minutes:02d}:{seconds:05.2f}" if hours else text


# --- redact video -----------------------------------------------------------------------------

REDACT_STYLES = imageedit.REDACT_STYLES


@dataclass(frozen=True)
class Region:
    """An area of the picture hidden from ``start`` to ``end`` (seconds)."""

    box: Box  # pixels of the video as shown
    start: float
    end: float
    style: str = "solid"

    def visible(self, t: float) -> bool:
        return self.start - 1e-6 <= t <= self.end + 1e-6


def _enable(start: float, end: float) -> str:
    return f"between(t,{_t(start)},{_t(end)})"


def blur_radii(w: int, h: int) -> tuple[int, int]:
    """Box blur radii (luma, chroma) for an area, within what ``boxblur`` allows."""
    short = min(w, h)
    luma = max(1, min(round(short * imageedit.BLUR_STRENGTH), short // 2 - 1))
    chroma = max(0, min(luma // 2, short // 4 - 1))
    return luma, chroma


def pixel_blocks(w: int, h: int) -> tuple[int, int]:
    short = max(1, min(w, h))
    blocks = imageedit.PIXELATE_BLOCKS
    return max(1, round(w * blocks / short)), max(1, round(h * blocks / short))


def redact_graph(regions: Sequence[Region], size: tuple[int, int], video_index: int) -> str:
    """A filter graph from the video to ``[vN]`` hiding every region in its time range."""
    parts = [f"[0:{video_index}]null[v0]"]
    for i, region in enumerate(regions):
        x, y, w, h = even_box(region.box, size)
        enable = _enable(region.start, region.end)
        src, dst = f"[v{i}]", f"[v{i + 1}]"
        if region.style == "solid":
            parts.append(
                f"{src}drawbox=x={x}:y={y}:w={w}:h={h}:color=black:t=fill:enable='{enable}'{dst}"
            )
            continue
        if region.style == "blur":
            luma, chroma = blur_radii(w, h)
            effect = f"boxblur=lr={luma}:lp=3:cr={chroma}:cp=3"
        else:
            bw, bh = pixel_blocks(w, h)
            effect = f"scale={bw}:{bh}:flags=area,scale={w}:{h}:flags=neighbor"
        parts.append(
            f"{src}split[m{i}][c{i}];[c{i}]crop={w}:{h}:{x}:{y},{effect}[e{i}];"
            f"[m{i}][e{i}]overlay={x}:{y}:enable='{enable}'{dst}"
        )
    return ";".join(parts)


REDACT_NOTE = "Metadata was removed from the redacted copy"


# --- audio channels -----------------------------------------------------------------------------

CHANNEL_MODES: dict[str, tuple[str, str]] = {
    # mode: (label, file name suffix)
    "mono": ("Mono (mix both)", "mono"),
    "left": ("Left on both sides", "left"),
    "right": ("Right on both sides", "right"),
    "swap": ("Swap left and right", "swapped"),
    "mute_left": ("Mute left", "muted-left"),
    "mute_right": ("Mute right", "muted-right"),
    "stereo": ("Stereo", "stereo"),
}
_PAN = {
    "left": "pan=stereo|c0=c0|c1=c0",
    "right": "pan=stereo|c0=c1|c1=c1",
    "swap": "pan=stereo|c0=c1|c1=c0",
    "mute_left": "pan=stereo|c1=c1",
    "mute_right": "pan=stereo|c0=c0",
}


def channel_modes(channels: int) -> tuple[str, ...]:
    if channels <= 0:
        return ()
    if channels == 1:
        return ("stereo",)
    if channels == 2:
        return ("mono", "left", "right", "swap", "mute_left", "mute_right")
    return ("stereo", "mono")


def channel_filter(mode: str, channels: int) -> tuple[str, int]:
    """The filter for ``mode`` and the number of channels it gives."""
    if mode not in channel_modes(channels):
        raise JobError("This change does not fit the channels of the sound")
    if mode == "mono":
        return ("pan=mono|c0=0.5*c0+0.5*c1" if channels == 2 else "aformat=channel_layouts=mono"), 1
    if mode == "stereo":
        if channels == 1:
            return "pan=stereo|c0=c0|c1=c0", 2
        return "aformat=channel_layouts=stereo", 2
    return _PAN[mode], 2


def channel_lanes(mode: str, lanes: Sequence[Sequence[float]]) -> tuple[tuple[float, ...], ...]:
    """What the waveform lanes look like after ``mode`` (for the preview)."""
    lanes = [tuple(lane) for lane in lanes]
    if not lanes:
        return ()
    silent = tuple(0.0 for _ in lanes[0])
    if mode in ("mono", "stereo"):
        mixed = tuple(max(values) for values in zip(*lanes, strict=False))
        return (mixed,) if mode == "mono" else (mixed, mixed)
    left, right = lanes[0], lanes[1]
    return {
        "left": (left, left),
        "right": (right, right),
        "swap": (right, left),
        "mute_left": (silent, right),
        "mute_right": (left, silent),
    }[mode]


# --- redact audio -------------------------------------------------------------------------------

Span = tuple[float, float]


def merge_spans(spans: Sequence[Span]) -> tuple[Span, ...]:
    """Sorted, with overlapping or touching spans joined; empty ones dropped."""
    merged: list[list[float]] = []
    for start, end in sorted((min(a, b), max(a, b)) for a, b in spans):
        if end - start < 1e-3:
            continue
        if merged and start <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return tuple((a, b) for a, b in merged)


def redact_audio_filter(spans: Sequence[Span], style: str) -> str:
    spans = merge_spans(spans)
    if not spans:
        raise JobError("Nothing to hide")
    inside = "+".join(_enable(a, b) for a, b in spans)
    if style == "silence":
        return f"volume=0:enable='{inside}'"
    tone = f"{BLEEP_LEVEL}*sin(2*PI*{BLEEP_HZ}*t)"
    return f"aeval=exprs='if({inside},{tone},val(ch))':c=same"


# --- audio to video ---------------------------------------------------------------------------

VISUALS: dict[str, str] = {
    "waveform": "Waveform",
    "bars": "Bars",
    "spectrum": "Spectrum",
    "still": "Picture",
}
VIDEO_SIZES: dict[str, tuple[str, tuple[int, int]]] = {
    "wide": ("16:9 · 1920 × 1080", (1920, 1080)),
    "square": ("Square · 1080 × 1080", (1080, 1080)),
}
VISUAL_FPS = 30
STILL_FPS = 5
RGB = tuple[int, int, int]


def _hex(color: Sequence[int]) -> str:
    r, g, b = (int(c) for c in color[:3])
    return f"0x{r:02x}{g:02x}{b:02x}"


def visual_graph(style: str, size: tuple[int, int], background: RGB, color: RGB) -> str:
    """A graph from the sound (``[0:a]``-labelled input ``[a]``) or picture to ``[v]``."""
    w, h = size
    bg = _hex(background)
    if style == "still":
        return (
            f"[pic]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={bg},setsar=1,format=yuv420p[v]"
        )
    back = f"color=c={bg}:s={w}x{h}:r={VISUAL_FPS}[bg]"
    if style == "waveform":
        wave = (
            f"[a]aformat=channel_layouts=mono,showwaves=s={w}x{h * 2 // 5}:mode=cline"
            f":rate={VISUAL_FPS}:colors={_hex(color)}:scale=sqrt[fg]"
        )
    elif style == "bars":
        wave = (
            f"[a]showfreqs=s={w * 4 // 5 // 2 * 2}x{h // 2 // 2 * 2}:mode=bar:ascale=log"
            f":fscale=log:win_size=2048:colors={_hex(color)}[fg]"
        )
    elif style == "spectrum":
        return f"[a]showcqt=s={w}x{h}:fps={VISUAL_FPS}:axis=0,format=yuv420p[v]"
    else:
        raise JobError(f"Unknown picture style {style!r}")
    return f"{back};{wave};[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuv420p[v]"


def extract_cover(source: Source, out: Path, ctx: JobContext | None = None) -> Path:
    cover = source.info.cover
    if cover is None:
        raise JobError("This file has no cover picture; choose one")
    process.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", source.path,
         "-map", f"0:{cover.index}", "-frames:v", "1", "-f", "image2", "-c:v", "png", out],
        ctx,
        timeout_s=60,
    )  # fmt: skip
    return out


def visual_args(
    source: Source,
    style: str,
    size: tuple[int, int],
    background: RGB,
    color: RGB,
    picture: Path | None,
    *,
    start: float = 0.0,
    length: float | None = None,
) -> list:
    """Arguments (without the output) for the video; ``start``/``length`` cut a preview."""
    info = source.info
    if not info.audio:
        raise JobError("There is no sound in this file")
    first = info.audio[0]
    length = source.duration - start if length is None else length
    window = ["-ss", _t(start), "-t", _t(length)]
    graph = visual_graph(style, size, background, color)
    if style == "still":
        if picture is None:
            raise JobError("Choose a picture for the video")
        inputs = ["-loop", "1", "-framerate", str(STILL_FPS), *window, "-i", picture,
                  *window, "-i", source.path]  # fmt: skip
        graph = "[0:v]null[pic];" + graph
        audio = f"1:{first.index}"
    else:
        inputs = [*window, "-i", source.path]
        graph = f"[0:{first.index}]anull[a];" + graph
        audio = f"0:{first.index}"
    return [*inputs, "-filter_complex", graph, "-map", "[v]", "-map", audio]


def visual_preview(
    source: Source,
    style: str,
    size: tuple[int, int],
    background: RGB,
    color: RGB,
    picture: Path | None,
    at: float,
    out: Path,
) -> Path:
    """One picture of the video around ``at`` (the last frame of a short piece) as PNG."""
    length = 0.6
    start = max(0.0, min(at, source.duration - length) - 0.5)
    with tempfile.TemporaryDirectory(prefix="quince-cover-") as tmp:
        if style == "still" and picture is None:
            picture = extract_cover(source, Path(tmp) / "cover.png")
        args = visual_args(
            source, style, size, background, color, picture, start=start, length=length
        )
        process.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", *args[:-2], "-an",
             "-c:v", "png", "-update", "1", "-f", "image2", out],
            None,
            timeout_s=60,
        )  # fmt: skip
    return out
