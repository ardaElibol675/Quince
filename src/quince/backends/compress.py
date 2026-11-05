"""Compress: a smaller file in the same format (CLAUDE.md Phase 12).

A ``Goal`` is either a quality level (high / medium / low) or a size: a percentage of the
original or a size per file. For a size, Quince tries settings from the best quality down
and keeps the first result that fits; a size it cannot reach is an error with the smallest
size it could make (Hard Rule 10). A result that is not smaller than the original is never
kept: the file is reported as already well compressed.

- Images (Pillow): JPEG / WebP / HEIC / AVIF by quality; PNG and GIF by fewer colors (PNG
  "high" is lossless); TIFF lossless, then JPEG-compressed. A size goal may also shrink the
  pixels (noted on the card). EXIF, color profile and DPI are kept like in conversions.
- PDF (Ghostscript ``pdfwrite``): printer (300 dpi images), ebook (150 dpi), screen (72 dpi),
  then 50 dpi for sizes.
- Audio (ffmpeg): MP3, M4A, OGG, Opus, WMA re-encoded at a lower bit rate; cover art kept.
- Video (ffmpeg): H.264 (VP9 for WebM) by CRF for a quality, two-pass at a computed bit rate
  for a size, scaled down when the bit rate is low. AVI and WMV become MP4.
"""

from __future__ import annotations

import io
import logging
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from quince.backends import ffmpeg, imaging, process
from quince.backends.files import human_size
from quince.backends.media import MUXERS, VIDEO_TARGETS
from quince.core import formats
from quince.core.jobs import JobContext, JobError

log = logging.getLogger(__name__)

LEVELS = ("high", "medium", "low")

# --- goals -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Goal:
    mode: str = "quality"  # "quality" | "percent" | "size"
    level: str = "medium"
    percent: int = 50
    size_bytes: int = 0

    def target(self, original: int) -> int | None:
        """The size to reach in bytes, or None for a quality goal."""
        if self.mode == "percent":
            return max(1, original * self.percent // 100)
        if self.mode == "size":
            return self.size_bytes
        return None


def check_target(original: int, target: int | None) -> None:
    if target is not None and original <= target:
        raise JobError(f"Already {human_size(original)}, not more than {human_size(target)}")


def keep_if_smaller(data_size: int, original: int) -> None:
    if data_size >= original:
        raise JobError(
            f"Already well compressed: the result ({human_size(data_size)}) would not be "
            f"smaller than {human_size(original)}"
        )


def unreachable(target: int, smallest: int | None) -> JobError:
    tail = f"; the smallest Quince can make is {human_size(smallest)}" if smallest else ""
    return JobError(f"Cannot reach {human_size(target)}{tail}")


# --- images ------------------------------------------------------------------------------

IMAGE_FORMATS = frozenset({"jpg", "png", "webp", "heic", "avif", "tiff", "gif"})
LOSSY_QUALITY = {
    "jpg": {"high": 82, "medium": 68, "low": 50},
    "webp": {"high": 80, "medium": 66, "low": 48},
    "heic": {"high": 70, "medium": 55, "low": 40},
    "avif": {"high": 65, "medium": 50, "low": 35},
}
QUALITY_LADDER = (92, 85, 78, 70, 62, 55, 48, 40, 32, 25)
SCALE_LADDER = (0.85, 0.7, 0.55, 0.4, 0.3, 0.2)
SCALED_QUALITY = (70, 50, 35)
COLOR_LEVELS = {"high": None, "medium": 256, "low": 64}  # PNG; None = lossless
GIF_COLORS = {"high": 256, "medium": 128, "low": 64}
COLOR_LADDER = (256, 128, 64, 32, 16)


@dataclass(frozen=True)
class _Step:
    """One way to encode the image: quality (lossy) or colors, at a scale."""

    quality: int | None = None
    colors: int | None = None
    scale: float = 1.0


def _resized(im: Image.Image, scale: float) -> Image.Image:
    if scale >= 1.0:
        return im
    size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
    return im.resize(size, Image.Resampling.LANCZOS)


def _quantized(im: Image.Image, colors: int) -> Image.Image:
    method = Image.Quantize.FASTOCTREE if im.mode == "RGBA" else Image.Quantize.MEDIANCUT
    if im.mode in ("RGB", "RGBA"):
        base = im
    elif im.mode == "P":
        base = im.convert("RGBA" if im.info.get("transparency") is not None else "RGB")
    else:
        base = im.convert("RGBA" if "A" in im.mode else "RGB")
    return base.quantize(colors=colors, method=method, dither=Image.Dither.FLOYDSTEINBERG)


class ImageEncoder:
    """Encodes one opened image in its own format with different settings."""

    def __init__(self, im: Image.Image, format_id: str) -> None:
        self.format_id = format_id
        self.target = imaging.TARGETS[format_id]
        upright = ImageOps.exif_transpose(im)
        self.prepared = imaging.prepare(upright, self.target)
        self.options = imaging._save_options(upright, self.prepared, self.target)
        self.alpha = imaging.has_alpha(self.prepared.image)

    def encode(self, step: _Step) -> bytes:
        im = _resized(self.prepared.image, step.scale)
        options = dict(self.options)
        fmt = self.target.pil_format
        if self.format_id in LOSSY_QUALITY:
            options["quality"] = step.quality
        elif self.format_id == "png":
            options.update(optimize=True, compress_level=9)
            if step.colors:
                im = _quantized(im, step.colors)
        elif self.format_id == "gif":
            im = _quantized(im, step.colors or 256)
            options["optimize"] = True
        elif self.format_id == "tiff":
            if step.quality is not None:
                options.update(compression="jpeg", quality=step.quality)
                im = im.convert("RGB") if im.mode not in ("RGB", "L", "CMYK") else im
            else:
                options["compression"] = "tiff_adobe_deflate"
        if self.format_id == "heic":
            imaging._register_heif()
        if step.scale < 1.0 and "dpi" in options:
            options["dpi"] = tuple(d * step.scale for d in options["dpi"])
        buffer = io.BytesIO()
        try:
            im.save(buffer, format=fmt, **options)
        except (OSError, ValueError, KeyError) as exc:
            raise JobError(f"Cannot write the image: {exc}", str(exc)) from exc
        return buffer.getvalue()

    def quality_step(self, level: str) -> _Step:
        if self.format_id in LOSSY_QUALITY:
            return _Step(quality=LOSSY_QUALITY[self.format_id][level])
        if self.format_id == "png":
            return _Step(colors=COLOR_LEVELS[level])
        if self.format_id == "gif":
            return _Step(colors=GIF_COLORS[level])
        # TIFF: lossless deflate, or JPEG inside the TIFF for medium / low
        if level == "high" or self.alpha:
            return _Step()
        return _Step(quality=LOSSY_QUALITY["jpg"][level])

    def ladder(self) -> list[_Step]:
        """Settings from the best quality down to the smallest result."""
        if self.format_id in LOSSY_QUALITY or (self.format_id == "tiff" and not self.alpha):
            steps = [_Step()] if self.format_id == "tiff" else []
            steps += [_Step(quality=q) for q in QUALITY_LADDER]
            steps += [_Step(quality=q, scale=s) for s in SCALE_LADDER for q in SCALED_QUALITY]
            return steps
        if self.format_id == "tiff":  # with alpha: lossless only, then smaller
            return [_Step(scale=s) for s in (1.0, *SCALE_LADDER)]
        colors = ([None] if self.format_id == "png" else []) + list(COLOR_LADDER)
        steps = [_Step(colors=c) for c in colors]
        steps += [_Step(colors=c, scale=s) for s in SCALE_LADDER for c in (128, 32)]
        return steps


def search(ladder: Sequence[_Step], size_of: Callable[[_Step], int], target: int) -> int | None:
    """Index of the first (best) step whose result fits ``target``; binary search, since the
    sizes shrink down the ladder. None when even the last step is too big."""
    if size_of(ladder[-1]) > target:
        return None
    low, high = 0, len(ladder) - 1  # high always fits
    while low < high:
        middle = (low + high) // 2
        if size_of(ladder[middle]) <= target:
            high = middle
        else:
            low = middle + 1
    return high


def compress_image(ctx: JobContext, src: Path, format_id: str, goal: Goal, out: Path) -> None:
    original = src.stat().st_size
    target = goal.target(original)
    check_target(original, target)
    im = imaging.open_image(src, format_id)
    try:
        if getattr(im, "n_frames", 1) > 1:
            word = "pages" if format_id == "tiff" else "frames"
            raise JobError(f"Images with several {word} cannot be compressed yet")
        encoder = ImageEncoder(im, format_id)
        cache: dict[_Step, bytes] = {}

        def encoded(step: _Step) -> bytes:
            ctx.check()
            if step not in cache:
                cache[step] = encoder.encode(step)
            return cache[step]

        if target is None:
            step = encoder.quality_step(goal.level)
            data = encoded(step)
        else:
            ladder = encoder.ladder()
            found = search(ladder, lambda s: len(encoded(s)), target)
            if found is None:
                raise unreachable(target, len(encoded(ladder[-1])))
            step = ladder[found]
            data = encoded(step)
        keep_if_smaller(len(data), original)
        out.write_bytes(data)
        if step.scale < 1.0:
            w, h = _resized(encoder.prepared.image, step.scale).size
            ctx.note(f"Resized to {w} × {h} to reach the size")
        if step.colors and format_id in ("png", "gif"):
            ctx.note(f"Reduced to {step.colors} colors")
        if encoder.prepared.flattened:
            ctx.note(imaging.FLATTENED_NOTE)
    finally:
        im.close()


# --- PDF (Ghostscript) ---------------------------------------------------------------------

GS_TIMEOUT_S = 1800
GS_PRESETS = {"high": "/printer", "medium": "/ebook", "low": "/screen"}
_TINY = (
    "-dDownsampleColorImages=true", "-dColorImageResolution=50",
    "-dDownsampleGrayImages=true", "-dGrayImageResolution=50",
    "-dDownsampleMonoImages=true", "-dMonoImageResolution=150",
)  # fmt: skip
PDF_LADDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("/printer", ()),
    ("/ebook", ()),
    ("/screen", ()),
    ("/screen", _TINY),
)


def _check_pdf(src: Path) -> None:
    from quince.backends import pdf

    doc = pdf.open_pdf(src)
    try:
        if doc.needs_pass:
            raise JobError("The PDF is password-protected")
    finally:
        doc.close()


def ghostscript(ctx: JobContext, src: Path, out: Path, preset: str, extra=()) -> None:
    process.run(
        [
            "gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.5", f"-dPDFSETTINGS={preset}",
            "-dDetectDuplicateImages=true", *extra, f"-sOutputFile={out}", "-f", src,
        ],
        ctx,
        timeout_s=GS_TIMEOUT_S,
    )  # fmt: skip
    if not out.exists() or out.stat().st_size == 0:
        raise JobError("Ghostscript wrote no PDF")


def compress_pdf(ctx: JobContext, src: Path, goal: Goal, out: Path) -> None:
    original = src.stat().st_size
    target = goal.target(original)
    check_target(original, target)
    _check_pdf(src)
    if target is None:
        ctx.progress(None, "Compressing")
        ghostscript(ctx, src, out, GS_PRESETS[goal.level])
        keep_if_smaller(out.stat().st_size, original)
        return
    smallest = None
    with tempfile.TemporaryDirectory(prefix="quince-gs-") as tmp:
        for i, (preset, extra) in enumerate(PDF_LADDER):
            ctx.progress(i / len(PDF_LADDER), f"Trying {preset.strip('/')} quality")
            attempt = Path(tmp) / f"try-{i}.pdf"
            ghostscript(ctx, src, attempt, preset, extra)
            size = attempt.stat().st_size
            smallest = size if smallest is None else min(smallest, size)
            if size <= target:
                attempt.replace(out)
                if extra:
                    ctx.note("Images were reduced to 50 dpi to reach the size")
                return
    raise unreachable(target, smallest)


# --- audio ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class AudioPreset:
    codec: str
    kbps: dict[str, int]
    min_kbps: int
    cover: bool = False


AUDIO_FORMATS = {
    "mp3": AudioPreset("libmp3lame", {"high": 160, "medium": 128, "low": 96}, 32, cover=True),
    "m4a": AudioPreset("aac", {"high": 128, "medium": 96, "low": 64}, 32, cover=True),
    "ogg": AudioPreset("libvorbis", {"high": 128, "medium": 96, "low": 64}, 48),
    "opus": AudioPreset("libopus", {"high": 96, "medium": 64, "low": 40}, 12),
    "wma": AudioPreset("wmav2", {"high": 128, "medium": 96, "low": 64}, 32),
}
MAX_ATTEMPTS = 3
SIZE_MARGIN = 0.96  # aim a little below the target; containers add a few bytes


def _need_duration(info: ffmpeg.MediaInfo) -> float:
    if not info.duration:
        raise JobError("Cannot tell how long the file is, so no bit rate can be computed")
    return info.duration


def _kbps_for(target: int, duration: float, other_kbps: float = 0.0) -> int:
    return int(target * 8 * SIZE_MARGIN / duration / 1000 - other_kbps)


def _encode_audio(
    ctx: JobContext, src: Path, format_id: str, info: ffmpeg.MediaInfo, kbps: int, out: Path
) -> None:
    preset = AUDIO_FORMATS[format_id]
    if not info.audio:
        raise JobError("There is no sound in this file")
    args: list[str | Path] = ["-i", src, "-map", "0:a:0"]
    if preset.cover and info.cover is not None:
        args += ["-map", f"0:{info.cover.index}", "-c:v", "copy"]
    args += ["-map_metadata", "0", "-c:a", preset.codec, "-b:a", f"{kbps}k"]
    out.unlink(missing_ok=True)
    ffmpeg.run_ffmpeg([*args, "-f", MUXERS[format_id], out], ctx, duration_s=info.duration)


def compress_audio(ctx: JobContext, src: Path, format_id: str, goal: Goal, out: Path) -> None:
    original = src.stat().st_size
    target = goal.target(original)
    check_target(original, target)
    info = ffmpeg.probe(src, ctx)
    preset = AUDIO_FORMATS[format_id]
    if target is None:
        _encode_audio(ctx, src, format_id, info, preset.kbps[goal.level], out)
        keep_if_smaller(out.stat().st_size, original)
        return
    duration = _need_duration(info)
    kbps = _kbps_for(target, duration)
    for _attempt in range(MAX_ATTEMPTS):
        if kbps < preset.min_kbps:
            smallest = int(preset.min_kbps * 1000 * duration / 8)
            raise unreachable(target, smallest)
        _encode_audio(ctx, src, format_id, info, kbps, out)
        size = out.stat().st_size
        if size <= target:
            keep_if_smaller(size, original)
            return
        kbps = int(kbps * target / size * 0.97)
    raise unreachable(target, out.stat().st_size)


# --- video ---------------------------------------------------------------------------------

VIDEO_FORMATS = frozenset({"mp4", "mov", "mkv", "webm", "avi", "wmv"})
CRF = {"h264": {"high": 23, "medium": 27, "low": 31}, "vp9": {"high": 33, "medium": 38, "low": 44}}
MIN_VIDEO_KBPS = 100
HEIGHT_CAPS = ((600, 480), (1400, 720), (3000, 1080))  # below this video kbps, at most h


def video_output(format_id: str) -> str:
    """The container a compressed video is written in (AVI and WMV become MP4)."""
    return format_id if format_id in ("mp4", "mov", "mkv", "webm") else "mp4"


def _encoder(out_format: str) -> str:
    return "vp9" if out_format == "webm" else "h264"


def _video_codec_args(encoder: str, *, crf: int | None = None, kbps: int | None = None) -> list:
    if encoder == "vp9":
        rate = ["-crf", str(crf), "-b:v", "0"] if crf is not None else ["-b:v", f"{kbps}k"]
        return ["-c:v", "libvpx-vp9", *rate, "-row-mt", "1", "-deadline", "good",
                "-cpu-used", "4", "-pix_fmt", "yuv420p"]  # fmt: skip
    rate = ["-crf", str(crf)] if crf is not None else ["-b:v", f"{kbps}k"]
    return ["-c:v", "libx264", "-preset", "medium", *rate, "-pix_fmt", "yuv420p"]


def height_cap(video_kbps: int) -> int | None:
    return next((h for limit, h in HEIGHT_CAPS if video_kbps < limit), None)


def _scale_filter(info: ffmpeg.MediaInfo, cap: int | None) -> tuple[list[str], tuple | None]:
    video = info.video
    even = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    if cap is None or video is None or video.height <= cap:
        return ["-vf", even], None
    width = round(video.width * cap / video.height / 2) * 2
    return ["-vf", f"scale=-2:{cap}"], (width, cap)


def _audio_args(
    info: ffmpeg.MediaInfo, out_format: str, kbps: int | None, *, copy_ok: bool
) -> list[str]:
    if not info.audio:
        return []
    preset = VIDEO_TARGETS[out_format]
    codec = "libopus" if out_format == "webm" else "aac"
    if copy_ok and all(s.codec in preset.audio_copy for s in info.audio):
        return ["-map", "0:a", "-c:a", "copy"]
    return ["-map", "0:a", "-c:a", codec, "-b:a", f"{kbps or 128}k"]


def _video_common(src: Path, info: ffmpeg.MediaInfo, format_id: str, out_format: str) -> list:
    args: list = ["-i", src, "-map", f"0:{info.video.index}", "-map_metadata", "0"]
    if out_format == format_id and info.subtitles:
        args += ["-map", "0:s", "-c:s", "copy"]
    return args


def _finish(out_format: str) -> list[str]:
    return [*VIDEO_TARGETS[out_format].extra, "-f", MUXERS[out_format]]


def compress_video(ctx: JobContext, src: Path, format_id: str, goal: Goal, out: Path) -> None:
    original = src.stat().st_size
    target = goal.target(original)
    check_target(original, target)
    info = ffmpeg.probe(src, ctx)
    if info.video is None:
        raise JobError("There is no video stream in this file")
    out_format = video_output(format_id)
    encoder = _encoder(out_format)
    if out_format != format_id:
        ctx.note(f"Saved as {out_format.upper()}: {format_id.upper()} cannot be compressed well")
    common = _video_common(src, info, format_id, out_format)
    if target is None:
        scale, _ = _scale_filter(info, None)
        args = [*common, *scale, *_video_codec_args(encoder, crf=CRF[encoder][goal.level])]
        args += _audio_args(info, out_format, None, copy_ok=True)
        ffmpeg.run_ffmpeg([*args, *_finish(out_format), out], ctx, duration_s=info.duration)
        keep_if_smaller(out.stat().st_size, original)
        return

    duration = _need_duration(info)
    audio_kbps = 0
    if info.audio:
        total = _kbps_for(target, duration)
        audio_kbps = 128 if total >= 1500 else 96 if total >= 600 else 64
    video_kbps = _kbps_for(target, duration, audio_kbps * len(info.audio))
    for _attempt in range(MAX_ATTEMPTS):
        if video_kbps < MIN_VIDEO_KBPS:
            needed = (MIN_VIDEO_KBPS + audio_kbps * len(info.audio)) * 1000 * duration / 8
            raise unreachable(target, int(needed / SIZE_MARGIN))
        resized = _two_pass(ctx, src, info, format_id, out_format, video_kbps, audio_kbps, out)
        size = out.stat().st_size
        if size <= target:
            keep_if_smaller(size, original)
            if resized:
                ctx.note(f"Resized to {resized[0]} × {resized[1]} to reach the size")
            return
        video_kbps = int(video_kbps * target / size * 0.97)
    raise unreachable(target, out.stat().st_size)


def _two_pass(
    ctx: JobContext,
    src: Path,
    info: ffmpeg.MediaInfo,
    format_id: str,
    out_format: str,
    video_kbps: int,
    audio_kbps: int,
    out: Path,
) -> tuple | None:
    encoder = _encoder(out_format)
    scale, resized = _scale_filter(info, height_cap(video_kbps))
    # The same frame timing in both passes, or x264's stats do not match (the null muxer and
    # the real one would otherwise drop or duplicate different frames).
    codec = [*_video_codec_args(encoder, kbps=video_kbps), "-vsync", "vfr"]
    with tempfile.TemporaryDirectory(prefix="quince-2pass-") as tmp:
        log_prefix = str(Path(tmp) / "pass")
        first = ["-i", src, "-map", f"0:{info.video.index}", *scale, *codec,
                 "-pass", "1", "-passlogfile", log_prefix, "-an", "-f", "null", "-"]  # fmt: skip
        ffmpeg.run_ffmpeg(
            first, ctx, duration_s=info.duration, report=ffmpeg.sub_progress(ctx, 0.0, 0.5)
        )
        second = [*_video_common(src, info, format_id, out_format), *scale, *codec,
                  "-pass", "2", "-passlogfile", log_prefix]  # fmt: skip
        second += _audio_args(info, out_format, audio_kbps, copy_ok=False)
        out.unlink(missing_ok=True)
        ffmpeg.run_ffmpeg(
            [*second, *_finish(out_format), out],
            ctx,
            duration_s=info.duration,
            report=ffmpeg.sub_progress(ctx, 0.5, 1.0),
        )
    return resized


# --- entry point ---------------------------------------------------------------------------

AUDIO_COMPRESSIBLE = frozenset(AUDIO_FORMATS)
COMPRESSIBLE = IMAGE_FORMATS | AUDIO_COMPRESSIBLE | VIDEO_FORMATS | {"pdf"}


def output_extension(format_id: str) -> str:
    if format_id in VIDEO_FORMATS:
        return formats.get(video_output(format_id)).extension
    return formats.get(format_id).extension


def compress(ctx: JobContext, src: Path, format_id: str, goal: Goal, out: Path) -> None:
    if format_id in IMAGE_FORMATS:
        compress_image(ctx, src, format_id, goal, out)
    elif format_id == "pdf":
        compress_pdf(ctx, src, goal, out)
    elif format_id in AUDIO_FORMATS:
        compress_audio(ctx, src, format_id, goal, out)
    elif format_id in VIDEO_FORMATS:
        compress_video(ctx, src, format_id, goal, out)
    else:
        raise JobError(f"{formats.get(format_id).label} files cannot be compressed")
