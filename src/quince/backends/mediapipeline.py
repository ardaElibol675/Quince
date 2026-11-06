"""The Video and Sound Editors' result as data (``MediaEdit``) and the one ffmpeg run that
makes it.

Every edit is applied in one ffmpeg command (``edit_args`` is pure, so the rules are unit
tested without running ffmpeg):

- **Trim**: the kept part is read with ``-ss`` / ``-t``; the picture and the sound are
  encoded so both start exactly there (a lossy sound alone is copied, cut at its frame
  boundaries). With "Fast" and nothing else changed, every stream is copied and the part
  starts at the keyframe at or before In.
- **Picture**: redacted areas (in their time ranges) → crop → speed; a GIF gets its palette.
  The picture is copied when nothing changes it and the container can store its codec.
- **Sound** (every track; channels only on the first): channels → bleeps → speed →
  loudness (EBU R128, two passes: ``measure_args`` first). Copied when nothing changes it.
- **Times** (redacted areas, bleeps) are seconds of the source, as the editor shows them;
  they are moved to the trimmed part and cut to it.
- **Targets**: any video format, GIF, or a sound format (the first track). A sound becomes
  an MP4 video with a ``Visual`` (waveform, bars, spectrum or a still picture) made from the
  edited sound.
- Speed changes drop subtitles (their timing would not match); redacted copies carry no
  metadata; trims and speed changes drop chapters.
"""

from __future__ import annotations

import itertools
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from quince.backends import ffmpeg, media, mediaedit, mediatools
from quince.backends.ffmpeg import Stream
from quince.backends.media import AUDIO_CODECS, AUDIO_TARGETS, VIDEO_TARGETS
from quince.backends.mediaedit import (
    MIN_PART_S,
    Box,
    Region,
    Source,
    Span,
    _attachment_maps,
    _cover_maps,
    _t,
    _tail,
    encode_audio,
    encode_video,
    even_box,
)
from quince.backends.mediatools import Loudness
from quince.core.jobs import JobContext, JobError

RGB = tuple[int, int, int]
LOSSLESS_CODECS = frozenset({"flac", "alac"})
EVEN = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
FAST_NOTE = "Cut without re-encoding (no quality loss)"
SUBTITLES_NOTE = "Subtitles were not kept (their timing would not match)"
FIRST_TRACK_NOTE = "Only the first sound track's channels were changed"
ONE_TRACK_NOTE = "Only the first audio track was kept"
NO_TIME = 1e9  # a redacted area visible on every frame (snapshots)


@dataclass(frozen=True)
class Visual:
    """The picture of a sound turned into a video."""

    style: str = "waveform"
    size: tuple[int, int] = (1920, 1080)
    background: RGB = (24, 24, 27)
    color: RGB = (240, 151, 60)
    picture: Path | None = None  # "still": a chosen picture (None: the cover art)


@dataclass(frozen=True)
class MediaEdit:
    trim: Span | None = None
    fast: bool = False
    crop: Box | None = None  # pixels of the video as shown
    regions: tuple[Region, ...] = ()
    speed: float = 1.0
    keep_pitch: bool = True
    channels: str | None = None
    bleeps: tuple[Span, ...] = ()
    bleep_style: str = "bleep"
    normalize: bool = False
    remove_audio: bool = False
    visual: Visual = field(default_factory=Visual)

    def span(self, duration: float) -> Span:
        return self.trim if self.trim is not None else (0.0, duration)

    @property
    def changes_picture(self) -> bool:
        return self.crop is not None or bool(self.regions) or self.speed_changed

    @property
    def changes_sound(self) -> bool:
        return (
            self.channels is not None or bool(self.bleeps) or self.speed_changed or self.normalize
        )

    @property
    def speed_changed(self) -> bool:
        return abs(self.speed - 1.0) > 1e-6


def is_trimmed(source: Source, start: float, end: float) -> bool:
    return start > 1e-3 or end < source.duration - 1e-3


def fast_possible(source: Source, edit: MediaEdit, target: str) -> bool:
    """A fast (copied) cut: only a trim or split of a video into its own format."""
    return (
        source.is_video
        and target == source.format_id
        and not edit.changes_picture
        and not edit.changes_sound
    )


def output_tracks(source: Source, edit: MediaEdit, target: str) -> tuple[Stream, ...]:
    """The sound tracks the output has."""
    if edit.remove_audio or target == "gif":
        return ()
    audio = source.info.audio
    if target in VIDEO_TARGETS and source.info.video is not None:
        return audio
    return audio[:1]


def _shifted_regions(regions: Sequence[Region], start: float, end: float) -> list[Region]:
    found = []
    for r in regions:
        a, b = max(r.start, start), min(r.end, end)
        if b - a > 1e-3:
            found.append(Region(r.box, a - start, b - start, r.style))
    return found


def _shifted_spans(spans: Sequence[Span], start: float, end: float) -> list[Span]:
    found = []
    for a, b in mediaedit.merge_spans(spans):
        a, b = max(a, start), min(b, end)
        if b - a > 1e-3:
            found.append((a - start, b - start))
    return found


def sound_chain(
    stream: Stream,
    first: bool,
    edit: MediaEdit,
    start: float,
    end: float,
    loudness: Loudness | None = None,
    *,
    measure: bool = False,
) -> tuple[list[str], int]:
    """The filters of one sound track and its channel count after them. ``measure``: end
    with the loudness measurement (the first pass)."""
    chain: list[str] = []
    channels = stream.channels
    if first and edit.channels is not None:
        graph, channels = mediaedit.channel_filter(edit.channels, stream.channels)
        chain.append(graph)
    spans = _shifted_spans(edit.bleeps, start, end)
    if spans:
        chain.append(mediaedit.redact_audio_filter(spans, edit.bleep_style))
    if edit.speed_changed:
        chain.append(mediaedit.speed_audio_filter(edit.speed, stream, edit.keep_pitch))
    if measure:
        chain.append(mediatools._loudnorm())
    elif edit.normalize:
        chain.append(mediatools._loudnorm(loudness))
    return chain, channels


def _can_copy_sound(stream: Stream, target: str) -> bool:
    if target in AUDIO_TARGETS:
        allowed = AUDIO_TARGETS[target].copy
    else:
        allowed = VIDEO_TARGETS[target].audio_copy
    codec = AUDIO_CODECS[mediaedit._audio_codec_key(target)]
    too_many = codec.max_channels is not None and stream.channels > codec.max_channels
    too_fast = codec.max_rate is not None and stream.sample_rate > codec.max_rate
    return stream.codec in allowed and not too_many and not too_fast


def _lossless(stream: Stream) -> bool:
    return stream.codec in LOSSLESS_CODECS or stream.codec.startswith("pcm_")


@dataclass
class Command:
    """ffmpeg arguments without the output file, the output's length and notes."""

    args: list = field(default_factory=list)
    duration: float = 0.0
    notes: list[str] = field(default_factory=list)
    encoded: bool = False

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


def edit_args(
    source: Source,
    edit: MediaEdit,
    target: str,
    *,
    span: Span | None = None,
    loudness: Sequence[Loudness | None] = (),
    picture: Path | None = None,
) -> Command:
    """The command writing ``edit`` of ``source`` as ``target``; ``span``: another part
    than the trim (Split); ``loudness``: the first pass per output track (Normalize);
    ``picture``: the still picture of a sound turned into a video."""
    info = source.info
    start, end = span if span is not None else edit.span(source.duration)
    if end - start < MIN_PART_S:
        raise JobError("The part to keep is too short")
    length = end - start
    trimmed = is_trimmed(source, start, end)
    fast = edit.fast and trimmed and fast_possible(source, edit, target)
    is_gif = target == "gif"
    video_target = target in VIDEO_TARGETS or is_gif
    if target not in VIDEO_TARGETS and target not in AUDIO_TARGETS and not is_gif:
        raise JobError(f"Cannot save as {target.upper()}")
    visual = edit.visual if video_target and info.video is None else None
    if visual is not None and (is_gif or target != "mp4"):
        raise JobError("A sound can only become an MP4 video")
    if target in AUDIO_TARGETS and (edit.remove_audio or not info.audio):
        raise JobError("There is no sound to save")
    cmd = Command(duration=length / edit.speed)
    window = ["-ss", _t(start), "-t", _t(length)] if trimmed else []
    args: list = [*window, "-i", source.path]
    graph: list[str] = []
    outputs: list = []

    # --- the picture ------------------------------------------------------------------
    video = info.video
    if video_target and video is not None:
        regions = _shifted_regions(edit.regions, start, end)
        linear: list[str] = []
        if edit.crop is not None:
            x, y, w, h = even_box(edit.crop, source.size)
            linear.append(f"crop={w}:{h}:{x}:{y}")
        if edit.speed_changed:
            rate = f",fps={source.fps:.6g}" if source.fps else ""
            linear.append(f"setpts=PTS/{edit.speed:.6g}{rate}")
        copy_ok = (
            not is_gif
            and not regions
            and not linear
            and (fast or not trimmed)
            and video.codec in VIDEO_TARGETS[target].video_copy
        )
        if copy_ok:
            outputs += ["-map", f"0:{video.index}", "-c:v", "copy"]
            if video.codec == "hevc" and target in ("mp4", "mov"):
                outputs += ["-tag:v", "hvc1"]
        else:
            cmd.encoded = True
            label = f"[0:{video.index}]"
            if regions:
                graph.append(mediaedit.redact_graph(regions, source.size, video.index))
                label = f"[v{len(regions)}]"
            linear.append(media.gif_filter(video) if is_gif else EVEN)
            graph.append(f"{label}{','.join(linear)}[vout]")
            outputs += ["-map", "[vout]"]
            outputs += ["-loop", "0"] if is_gif else encode_video(target)
        if regions:
            cmd.note(mediaedit.REDACT_NOTE)

    # --- the sound --------------------------------------------------------------------
    tracks = output_tracks(source, edit, target)
    with_picture = video_target and (video is not None or visual is not None)
    for i, stream in enumerate(tracks):
        measured = loudness[i] if i < len(loudness) else None
        chain, channels = sound_chain(stream, i == 0, edit, start, end, measured)
        spec = f"a:{i}"
        must_encode = trimmed and not fast and (with_picture or _lossless(stream))
        if not chain and not must_encode and _can_copy_sound(stream, target):
            outputs += ["-map", f"0:{stream.index}", f"-c:{spec}", "copy"]
            continue
        cmd.encoded = True
        if chain:
            graph.append(f"[0:{stream.index}]{','.join(chain)}[a{i}]")
            outputs += ["-map", f"[a{i}]"]
        else:
            outputs += ["-map", f"0:{stream.index}"]
        outputs += encode_audio(stream, target, spec, channels=channels)
        if edit.normalize:
            codec = AUDIO_CODECS[mediaedit._audio_codec_key(target)]
            rate = stream.sample_rate or 48000
            if codec.max_rate:
                rate = min(rate, codec.max_rate)
            outputs += [f"-ar:{spec}", str(rate)]
    if edit.channels is not None and len(tracks) > 1:
        cmd.note(FIRST_TRACK_NOTE)
    if target in AUDIO_TARGETS and len(info.audio) > 1:
        cmd.note(ONE_TRACK_NOTE)

    # --- a sound as a video -----------------------------------------------------------
    if visual is not None:
        if not tracks:
            raise JobError("There is no sound to show")
        cmd.encoded = True
        first = tracks[0]
        sound = next((g for g in graph if g.endswith("[a0]")), None)
        if visual.style == "still":
            if picture is None:
                raise JobError("Choose a picture for the video")
            args += ["-loop", "1", "-framerate", str(mediaedit.STILL_FPS),
                     "-t", _t(cmd.duration), "-i", picture]  # fmt: skip
            graph.append("[1:v]null[pic]")
        elif sound is not None:
            graph[graph.index(sound)] = sound[: -len("[a0]")] + ",asplit[a][a0]"
        else:
            graph.append(f"[0:{first.index}]anull[a]")
        graph.append(
            mediaedit.visual_graph(visual.style, visual.size, visual.background, visual.color)
        )
        video_args = ["-map", "[v]", *encode_video("mp4")]
        if visual.style == "still":
            video_args += ["-tune", "stillimage"]
        outputs = video_args + outputs

    # --- the rest ---------------------------------------------------------------------
    if target in VIDEO_TARGETS and video is not None:
        if edit.speed_changed:
            if info.subtitles:
                cmd.note(SUBTITLES_NOTE)
        else:
            plan = media.Plan()
            media._subtitles(plan, info, target, VIDEO_TARGETS[target])
            outputs += plan.args
            for text in plan.notes:
                cmd.note(text)
        outputs += _attachment_maps(info, target)
    if target in AUDIO_TARGETS and video is None:
        outputs += _cover_maps(info, target)
    if fast:
        cmd.note(FAST_NOTE)
    metadata = "-1" if edit.regions else "0"
    tail = ["-map_metadata", metadata]
    if trimmed or edit.speed_changed:
        tail += ["-map_chapters", "-1"]
    if graph:
        args += ["-filter_complex", ";".join(graph)]
    cmd.args = [*args, *outputs, *tail, *_tail(target)]
    return cmd


def measure_args(
    source: Source, edit: MediaEdit, target: str, track: int, *, span: Span | None = None
) -> list:
    """The first loudness pass of output track ``track`` (to ``-f null -``)."""
    start, end = span if span is not None else edit.span(source.duration)
    stream = output_tracks(source, edit, target)[track]
    chain, _channels = sound_chain(stream, track == 0, edit, start, end, measure=True)
    window = ["-ss", _t(start), "-t", _t(end - start)] if is_trimmed(source, start, end) else []
    return [
        *window, "-i", source.path, "-filter_complex",
        f"[0:{stream.index}]{','.join(chain)}[m]", "-map", "[m]", "-f", "null", "-",
    ]  # fmt: skip


# --- running ----------------------------------------------------------------------------------


def run_edit(
    ctx: JobContext,
    source: Source,
    edit: MediaEdit,
    target: str,
    out: Path,
    *,
    span: Span | None = None,
    progress: tuple[float, float] = (0.0, 1.0),
) -> list[str]:
    """Write ``edit`` of ``source`` to ``out``; the notes for the result card."""
    low, high = progress
    tracks = output_tracks(source, edit, target)
    steps = (len(tracks) if edit.normalize else 0) + 1
    loudness: list[Loudness] = []
    notes: list[str] = []
    if edit.normalize:
        for i in range(len(tracks)):
            ctx.check()
            a = low + (high - low) * i / steps
            b = low + (high - low) * (i + 1) / steps
            ctx.progress(a, "Measuring loudness")
            lines: list[str] = []
            first, last = span or edit.span(source.duration)
            length = last - first
            ffmpeg.run_ffmpeg(
                measure_args(source, edit, target, i, span=span),
                ctx,
                duration_s=max(0.1, length),
                report=ffmpeg.sub_progress(ctx, a, b),
                on_stderr=lines.append,
            )
            loudness.append(mediatools.parse_loudnorm("\n".join(lines)))
        if loudness:
            before = ", ".join(f"{m.integrated:.1f}" for m in loudness)
            notes.append(f"Loudness {before} → {mediatools.TARGET_LUFS:.0f} LUFS")
    with tempfile.TemporaryDirectory(prefix="quince-cover-") as tmp:
        picture = None
        visual = edit.visual
        if target == "mp4" and source.info.video is None and visual.style == "still":
            picture = visual.picture or mediaedit.extract_cover(source, Path(tmp) / "c.png", ctx)
        cmd = edit_args(source, edit, target, span=span, loudness=loudness, picture=picture)
        a = low + (high - low) * (steps - 1) / steps
        ctx.progress(a, "Saving" if steps > 1 else None)
        ffmpeg.run_ffmpeg(
            [*cmd.args, out],
            ctx,
            duration_s=max(0.1, cmd.duration),
            report=ffmpeg.sub_progress(ctx, a, high),
        )
    return [*cmd.notes, *notes]


# --- parts and frames -------------------------------------------------------------------------


def part_spans(
    source: Source,
    edit: MediaEdit,
    *,
    markers: Sequence[float] = (),
    parts: int = 0,
    every: float = 0.0,
    keys: Sequence[float] | None = None,
) -> tuple[Span, ...]:
    """The parts of the kept range (the trim): cut at ``markers`` inside it, into ``parts``
    equal parts, or ``every`` seconds. With ``keys`` (a fast split) every cut moves back to
    its keyframe, so the parts still follow each other without a gap."""
    start, end = edit.span(source.duration)
    if keys is not None:
        start = mediaedit.snap_back(start, keys)
    inside = [m - start for m in markers if start < m < end]
    points = mediaedit.split_points(end - start, markers=inside, parts=parts, every=every)
    cuts = [start + p for p in points]
    if keys is not None:
        cuts = [mediaedit.snap_back(c, keys) for c in cuts]
    edges = sorted({start, *(c for c in cuts if start + 1e-3 < c < end - 1e-3), end})
    return tuple((a, b) for a, b in itertools.pairwise(edges) if b - a > 1e-3)


def export_parts(
    ctx: JobContext,
    source: Source,
    edit: MediaEdit,
    target: str,
    folder: Path,
    stem: str,
    parts: Sequence[Span],
) -> None:
    from quince.core import formats

    if not 2 <= len(parts) <= mediaedit.MAX_PARTS:
        raise JobError(f"Choose between 2 and {mediaedit.MAX_PARTS} parts")
    extension = formats.get(target).extension
    width = max(2, len(str(len(parts))))
    for n, part in enumerate(parts, 1):
        ctx.check()
        ctx.progress((n - 1) / len(parts), f"Part {n} of {len(parts)}")
        out = folder / f"{stem}-{n:0{width}d}.{extension}"
        span = ((n - 1) / len(parts), n / len(parts))
        for text in run_edit(ctx, source, edit, target, out, span=part, progress=span):
            ctx.note(text)


def frame_args(
    source: Source, edit: MediaEdit, t: float, image_format: str, *, before: bool = False
) -> list:
    """One picture at ``t`` with the crop and the areas redacted at that time; ``before``:
    the last picture of the second up to ``t`` (see ``mediaedit.snapshot_args``)."""
    video = source.info.video
    if video is None:
        raise JobError("There is no video in this file")
    t = mediaedit.snapshot_time(source, t)
    window = ["-ss", _t(t - 1.0), "-t", "1.0"] if before else ["-ss", _t(t)]
    pick = ["-update", "1"] if before else ["-frames:v", "1"]
    regions = [Region(r.box, 0.0, NO_TIME, r.style) for r in edit.regions if r.visible(t)]
    linear: list[str] = []
    if edit.crop is not None:
        x, y, w, h = even_box(edit.crop, source.size)
        linear.append(f"crop={w}:{h}:{x}:{y}")
    graph: list[str] = []
    label = f"[0:{video.index}]"
    if regions:
        graph.append(mediaedit.redact_graph(regions, source.size, video.index))
        label = f"[v{len(regions)}]"
    graph.append(f"{label}{','.join(linear) or 'null'}[out]")
    return [
        *window, "-i", source.path, "-filter_complex", ";".join(graph), "-map", "[out]",
        *pick, *mediaedit.SNAPSHOT_FORMATS[image_format], "-f", "image2",
    ]  # fmt: skip


def export_frames(
    ctx: JobContext,
    source: Source,
    edit: MediaEdit,
    shots: Sequence[tuple[float, Path]],
    image_format: str,
) -> None:
    for n, (t, out) in enumerate(shots):
        ctx.check()
        ctx.progress(n / len(shots))
        ffmpeg.run_ffmpeg([*frame_args(source, edit, t, image_format), out], ctx)
        if not out.exists() or out.stat().st_size == 0:
            out.unlink(missing_ok=True)
            args = frame_args(source, edit, t, image_format, before=True)
            ffmpeg.run_ffmpeg([*args, out], ctx)
        if not out.exists() or out.stat().st_size == 0:
            raise JobError(f"There is no picture at {mediaedit.clock(t)}")
