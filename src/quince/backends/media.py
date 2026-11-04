"""Audio and video conversion with ffmpeg.

``plan()`` decides, from the probed streams, what every output stream becomes; it is pure,
so the rules are unit tested without running ffmpeg. ``convert()`` runs it.

Rules:

- **Stream copy first.** A stream whose codec the target container can store is copied as
  it is (fast, no quality loss); only the others are encoded. When nothing is encoded the
  card says "Converted without re-encoding".
- **Video presets** (when encoding): H.264 CRF 20 for MP4/MOV/MKV, VP9 CRF 31 for WebM,
  MPEG-4 (Xvid) for AVI, WMV2 for WMV; dimensions rounded to even numbers.
- **Audio presets**: MP3 VBR V2 (~190 kbps), AAC 192 kbps (384 kbps above stereo), Vorbis q6,
  Opus 160 kbps, WMA 192 kbps; WAV/AIFF 16-bit, or 24-bit for sources deeper than 16 bits;
  FLAC keeps the depth. MP3 and WMA hold at most stereo (downmixed, with a note); WMA at most
  48 kHz.
- **Tracks**: the first video track, every audio track, subtitles where the container can
  hold them (text subtitles become ``mov_text`` in MP4/MOV and WebVTT in WebM). Audio targets
  take the first audio track. Cover art is kept for MP3, M4A and FLAC.
- **GIF** from video: at most 480 px on the longer side, at most 15 fps, with an optimized
  palette. **GIF to video**: transparent areas become white; a still GIF is refused.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from quince.backends import ffmpeg
from quince.backends.ffmpeg import MediaInfo, Stream
from quince.core import formats
from quince.core.jobs import JobContext, JobError
from quince.core.routes import Route

GIF_MAX_SIDE = 480
GIF_MAX_FPS = 15.0
COPIED_NOTE = "Converted without re-encoding (no quality loss)"
DOWNMIX_NOTE = "Downmixed to stereo"

MUXERS: Mapping[str, str] = MappingProxyType(
    {
        "mp3": "mp3", "m4a": "ipod", "wav": "wav", "flac": "flac", "ogg": "ogg",
        "opus": "opus", "aiff": "aiff", "wma": "asf",
        "mp4": "mp4", "mov": "mov", "mkv": "matroska", "webm": "webm", "avi": "avi",
        "wmv": "asf", "gif": "gif",
    }
)  # fmt: skip

TEXT_SUBTITLES = frozenset({"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"})


def _label(format_id: str) -> str:
    return formats.get(format_id).label


# --- audio encoders ------------------------------------------------------------------


@dataclass(frozen=True)
class AudioCodec:
    """How one output audio stream is encoded (``spec`` is e.g. ``"a"`` or ``"a:1"``)."""

    args: Callable[[Stream, str], list[str]]
    max_channels: int | None = None
    max_rate: int | None = None


def _bitrate(stereo: str, surround: str) -> Callable[[Stream, str], list[str]]:
    return lambda s, spec: [f"-b:{spec}", surround if s.channels > 2 else stereo]


def _pcm(endian: str) -> Callable[[Stream, str], list[str]]:
    return lambda s, spec: [f"-c:{spec}", f"pcm_s{24 if s.bits > 16 else 16}{endian}"]


def _flac(s: Stream, spec: str) -> list[str]:
    deep = s.bits > 16
    return [f"-c:{spec}", "flac", *([f"-sample_fmt:{spec}", "s32"] if deep else [])]


AUDIO_CODECS: Mapping[str, AudioCodec] = MappingProxyType(
    {
        "mp3": AudioCodec(
            lambda s, spec: [f"-c:{spec}", "libmp3lame", f"-q:{spec}", "2"],
            max_channels=2,
            max_rate=48000,
        ),
        "aac": AudioCodec(
            lambda s, spec: [f"-c:{spec}", "aac", *_bitrate("192k", "384k")(s, spec)]
        ),
        "pcm_le": AudioCodec(_pcm("le")),
        "pcm_be": AudioCodec(_pcm("be")),
        "flac": AudioCodec(_flac),
        "vorbis": AudioCodec(lambda s, spec: [f"-c:{spec}", "libvorbis", f"-q:{spec}", "6"]),
        "opus": AudioCodec(
            lambda s, spec: [f"-c:{spec}", "libopus", *_bitrate("160k", "320k")(s, spec)]
        ),
        "wma": AudioCodec(
            lambda s, spec: [f"-c:{spec}", "wmav2", f"-b:{spec}", "192k"],
            max_channels=2,
            max_rate=48000,
        ),
    }
)


@dataclass(frozen=True)
class AudioTarget:
    copy: frozenset[str]  # codecs stored as they are
    codec: str  # key of AUDIO_CODECS used otherwise
    cover: bool = False


AUDIO_TARGETS: Mapping[str, AudioTarget] = MappingProxyType(
    {
        "mp3": AudioTarget(frozenset({"mp3"}), "mp3", cover=True),
        "m4a": AudioTarget(frozenset({"aac", "alac"}), "aac", cover=True),
        "wav": AudioTarget(frozenset({"pcm_s16le", "pcm_s24le"}), "pcm_le"),
        "flac": AudioTarget(frozenset({"flac"}), "flac", cover=True),
        "ogg": AudioTarget(frozenset({"vorbis"}), "vorbis"),
        "opus": AudioTarget(frozenset({"opus"}), "opus"),
        "aiff": AudioTarget(frozenset({"pcm_s16be", "pcm_s24be"}), "pcm_be"),
        "wma": AudioTarget(frozenset({"wmav1", "wmav2"}), "wma"),
    }
)
COVER_CODECS = {"mp3": {"mjpeg", "png"}, "m4a": {"mjpeg", "png"}, "flac": {"mjpeg", "png"}}

# --- video containers ------------------------------------------------------------------

_EVEN = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
VIDEO_ENCODERS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "h264": ("-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"),
        "vp9": (
            "-c:v", "libvpx-vp9", "-crf", "31", "-b:v", "0", "-row-mt", "1",
            "-deadline", "good", "-cpu-used", "4", "-pix_fmt", "yuv420p",
        ),
        "mpeg4": ("-c:v", "mpeg4", "-vtag", "xvid", "-q:v", "3", "-pix_fmt", "yuv420p"),
        "wmv2": ("-c:v", "wmv2", "-q:v", "3", "-pix_fmt", "yuv420p"),
    }
)  # fmt: skip


@dataclass(frozen=True)
class VideoTarget:
    video_copy: frozenset[str]
    video_encoder: str  # key of VIDEO_ENCODERS
    audio_copy: frozenset[str]
    audio_codec: str  # key of AUDIO_CODECS
    subtitles: str | None = None  # "copy", "mov_text", "webvtt" or None (cannot store them)
    extra: tuple[str, ...] = ()
    attachments: bool = False


_MKV_AUDIO = frozenset(
    {"aac", "mp3", "mp2", "ac3", "eac3", "dts", "opus", "vorbis", "flac", "alac", "truehd",
     "pcm_s16le", "pcm_s24le", "pcm_f32le"}
)  # fmt: skip
_MP4_AUDIO = frozenset({"aac", "mp3", "ac3", "eac3", "alac"})
VIDEO_TARGETS: Mapping[str, VideoTarget] = MappingProxyType(
    {
        "mp4": VideoTarget(
            frozenset({"h264", "hevc", "mpeg4"}), "h264", _MP4_AUDIO, "aac",
            subtitles="mov_text", extra=("-movflags", "+faststart"),
        ),
        "mov": VideoTarget(
            frozenset({"h264", "hevc", "mpeg4", "prores", "mjpeg"}), "h264",
            _MP4_AUDIO | {"pcm_s16le", "pcm_s24le", "pcm_s16be", "pcm_s24be"}, "aac",
            subtitles="mov_text", extra=("-movflags", "+faststart"),
        ),
        "mkv": VideoTarget(
            frozenset({"h264", "hevc", "vp8", "vp9", "av1", "mpeg4", "mpeg2video", "mpeg1video",
                       "msmpeg4v3", "theora", "prores", "mjpeg", "vc1"}),
            "h264", _MKV_AUDIO, "aac", subtitles="copy", attachments=True,
        ),
        "webm": VideoTarget(
            frozenset({"vp8", "vp9", "av1"}), "vp9", frozenset({"opus", "vorbis"}), "opus",
            subtitles="webvtt",
        ),
        "avi": VideoTarget(
            frozenset({"mpeg4", "msmpeg4v3", "mjpeg"}), "mpeg4",
            frozenset({"mp3", "ac3", "pcm_s16le"}), "mp3",
        ),
        "wmv": VideoTarget(
            frozenset({"wmv1", "wmv2", "wmv3", "vc1"}), "wmv2",
            frozenset({"wmav1", "wmav2", "wmapro"}), "wma",
        ),
    }
)  # fmt: skip


# --- planning ------------------------------------------------------------------------


@dataclass
class Plan:
    args: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    encoded: bool = False  # at least one stream is encoded

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    def finish(self) -> Plan:
        if not self.encoded:
            self.note(COPIED_NOTE)
        return self


def _audio_stream(plan: Plan, stream: Stream, spec: str, copy: frozenset[str], codec: str) -> None:
    """Copy or encode one audio stream as output stream ``spec``."""
    encoder = AUDIO_CODECS[codec]
    too_many = encoder.max_channels is not None and stream.channels > encoder.max_channels
    too_fast = encoder.max_rate is not None and stream.sample_rate > encoder.max_rate
    if stream.codec in copy and not too_many and not too_fast:
        plan.args += [f"-c:{spec}", "copy"]
        return
    plan.encoded = True
    plan.args += encoder.args(stream, spec)
    if too_many:
        plan.args += [f"-ac:{spec}", str(encoder.max_channels)]
        plan.note(DOWNMIX_NOTE)
    if too_fast:
        plan.args += [f"-ar:{spec}", str(encoder.max_rate)]


def plan_audio(info: MediaInfo, target: str) -> Plan:
    preset = AUDIO_TARGETS[target]
    if not info.audio:
        raise JobError("No sound to convert")
    plan = Plan()
    first = info.audio[0]
    plan.args += ["-map", f"0:{first.index}"]
    _audio_stream(plan, first, "a", preset.copy, preset.codec)
    if len(info.audio) > 1:
        plan.note("Only the first audio track was kept")
    cover = info.cover
    if cover is not None:
        if preset.cover and cover.codec in COVER_CODECS.get(target, ()):
            plan.args += [
                "-map",
                f"0:{cover.index}",
                "-c:v",
                "copy",
                "-disposition:v:0",
                "attached_pic",
            ]
        else:
            plan.note("Cover art was not kept")
    if target == "mp3":
        plan.args += ["-id3v2_version", "3"]
    return plan.finish()


def _subtitles(plan: Plan, info: MediaInfo, target: str, preset: VideoTarget) -> None:
    kept = 0
    dropped = False
    for sub in info.subtitles:
        text = sub.codec in TEXT_SUBTITLES
        if preset.subtitles is None or (preset.subtitles != "copy" and not text):
            dropped = True
            continue
        plan.args += ["-map", f"0:{sub.index}"]
        if preset.subtitles == "copy":
            codec = "srt" if sub.codec == "mov_text" else "copy"
        else:
            codec = preset.subtitles
        plan.args += [f"-c:s:{kept}", codec]
        kept += 1
    if dropped:
        reason = (
            f"{_label(target)} cannot store them" if preset.subtitles is None else "image-based"
        )
        plan.note(f"Subtitles were not kept ({reason})")


def plan_video(info: MediaInfo, target: str) -> Plan:
    preset = VIDEO_TARGETS[target]
    video = info.video
    if video is None:
        raise JobError("No video track to convert")
    plan = Plan()
    plan.args += ["-map", f"0:{video.index}"]
    if video.codec in preset.video_copy:
        plan.args += ["-c:v", "copy"]
        if video.codec == "hevc" and target in ("mp4", "mov"):
            plan.args += ["-tag:v", "hvc1"]  # what Apple players and browsers expect
    else:
        plan.encoded = True
        plan.args += [*VIDEO_ENCODERS[preset.video_encoder], "-vf", _EVEN]
    for n, stream in enumerate(info.audio):
        plan.args += ["-map", f"0:{stream.index}"]
        _audio_stream(plan, stream, f"a:{n}", preset.audio_copy, preset.audio_codec)
    _subtitles(plan, info, target, preset)
    if preset.attachments:
        for stream in info.streams:
            if stream.kind == "attachment":
                plan.args += ["-map", f"0:{stream.index}"]
        if any(s.kind == "attachment" for s in info.streams):
            plan.args += ["-c:t", "copy"]
    plan.args += list(preset.extra)
    return plan.finish()


def gif_filter(stream: Stream) -> str:
    fps = min(GIF_MAX_FPS, stream.fps) if stream.fps > 0 else GIF_MAX_FPS
    side = GIF_MAX_SIDE
    scale = (
        f"scale='if(gte(iw,ih),min({side},iw),-2)':'if(gte(iw,ih),-2,min({side},ih))':flags=lanczos"
    )
    return (
        f"fps={fps:g},{scale},split[a][b];[a]palettegen=stats_mode=diff[p];"
        "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
    )


def plan_gif(info: MediaInfo) -> Plan:
    video = info.video
    if video is None:
        raise JobError("No video track to convert")
    plan = Plan(encoded=True)
    plan.args += ["-map", f"0:{video.index}", "-vf", gif_filter(video), "-loop", "0"]
    return plan


_ON_WHITE = "split[a][b];[a]drawbox=c=white@1:t=fill[bg];[bg][b]overlay=format=auto"


def plan_from_gif(info: MediaInfo, target: str) -> Plan:
    """An animated GIF into a video: transparent areas on white, no sound."""
    preset = VIDEO_TARGETS[target]
    video = info.video
    if video is None:
        raise JobError("No animation to convert")
    plan = Plan(encoded=True)
    encoder = VIDEO_ENCODERS[preset.video_encoder]
    plan.args += ["-map", f"0:{video.index}", *encoder, "-vf", f"{_ON_WHITE},{_EVEN}"]
    plan.args += list(preset.extra)
    return plan


def plan(info: MediaInfo, source: str, target: str) -> Plan:
    """The ffmpeg output arguments (without the output file) for one conversion."""
    if target == "gif":
        return plan_gif(info)
    if source == "gif":
        return plan_from_gif(info, target)
    if target in AUDIO_TARGETS:
        return plan_audio(info, target)
    if target in VIDEO_TARGETS:
        return plan_video(info, target)
    raise JobError(f"ffmpeg cannot write {_label(target)}")


# --- running -------------------------------------------------------------------------


def _check_animated(src: Path) -> None:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(src) as im:
            animated = getattr(im, "is_animated", False)
    except (OSError, UnidentifiedImageError) as exc:
        raise JobError("Not a readable GIF image") from exc
    if not animated:
        raise JobError("Not animated; convert it to an image format instead")


def convert(ctx: JobContext, src: Path, out: Path, route: Route) -> None:
    """Convert one audio or video file ``src`` into ``out`` (a temporary path)."""
    if route.source == "gif":
        _check_animated(src)
    info = ffmpeg.probe(src, ctx)
    steps = plan(info, route.source, route.target)
    ffmpeg.run_ffmpeg(
        ["-i", src, "-map_metadata", "0", *steps.args, "-f", MUXERS[route.target], out],
        ctx,
        duration_s=info.duration,
    )
    for text in steps.notes:
        ctx.note(text)
