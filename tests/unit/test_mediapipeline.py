"""The Video and Sound Editors' one-command plans (pure argument builders, no ffmpeg run)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from quince.backends import mediapipeline as mp
from quince.backends.ffmpeg import MediaInfo, Stream
from quince.backends.mediaedit import Region, Source
from quince.backends.mediapipeline import MediaEdit, Visual
from quince.backends.mediatools import Loudness
from quince.core.jobs import JobError

VIDEO = Stream(0, "video", "h264", width=1920, height=1080, fps=25.0)
AUDIO = Stream(1, "audio", "aac", channels=2, sample_rate=48000)
SUBS = Stream(2, "subtitle", "mov_text")
AUDIO2 = Stream(3, "audio", "aac", channels=2, sample_rate=48000)


def video_source(fmt="mp4", streams=(VIDEO, AUDIO), duration=10.0) -> Source:
    return Source(Path(f"/v/clip.{fmt}"), fmt, MediaInfo(tuple(streams), duration), (1920, 1080))


def audio_source(fmt="mp3", codec="mp3", channels=2, cover=False) -> Source:
    streams = [Stream(0, "audio", codec, channels=channels, sample_rate=44100)]
    if cover:
        streams.append(Stream(1, "video", "mjpeg", attached_pic=True))
    return Source(Path(f"/a/song.{fmt}"), fmt, MediaInfo(tuple(streams), 30.0))


def after(args, flag):
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


def graph(cmd) -> str:
    found = after(cmd.args, "-filter_complex")
    return found[0] if found else ""


def test_nothing_changed_copies_every_stream():
    cmd = mp.edit_args(video_source(streams=(VIDEO, AUDIO, SUBS)), MediaEdit(), "mp4")
    assert "-filter_complex" not in cmd.args and "-ss" not in cmd.args
    assert after(cmd.args, "-c:v") == ["copy"] and after(cmd.args, "-c:a:0") == ["copy"]
    assert after(cmd.args, "-map") == ["0:0", "0:1", "0:2"]  # subtitles kept
    assert after(cmd.args, "-map_metadata") == ["0"] and cmd.args[-2:] == ["-f", "mp4"]
    assert not cmd.encoded and cmd.duration == 10.0


def test_exact_trim_encodes_and_moves_times_to_the_part():
    edit = MediaEdit(
        trim=(2.0, 6.0),
        regions=(Region((0, 0, 100, 100), 1.0, 3.0, "solid"), Region((0, 0, 10, 10), 7, 8)),
        bleeps=((5.0, 9.0),),
    )
    cmd = mp.edit_args(video_source(), edit, "mp4")
    assert cmd.args[:5] == ["-ss", "2.000", "-t", "4.000", "-i"]
    g = graph(cmd)
    assert "between(t,0.000,1.000)" in g  # 1..3 seen from 2, cut at the start
    assert "drawbox" in g and g.count("drawbox") == 1  # the area at 7..8 is outside
    assert "between(t,3.000,4.000)" in g  # the bleep 5..9 becomes 3..4
    assert "libx264" in cmd.args and after(cmd.args, "-c:a:0") == ["aac"]
    assert after(cmd.args, "-map_chapters") == ["-1"] and after(cmd.args, "-map_metadata") == ["-1"]
    assert mp.mediaedit.REDACT_NOTE in cmd.notes


def test_trimmed_lossy_sound_alone_is_copied_lossless_is_encoded():
    trim = MediaEdit(trim=(1.0, 2.0))
    assert after(mp.edit_args(audio_source(), trim, "mp3").args, "-c:a:0") == ["copy"]
    flac = mp.edit_args(audio_source("flac", "flac"), trim, "flac")
    assert after(flac.args, "-c:a:0") == ["flac"]


def test_fast_trim_copies_only_without_other_changes():
    src = video_source()
    fast = mp.edit_args(src, MediaEdit(trim=(2.0, 5.0), fast=True), "mp4")
    assert after(fast.args, "-c:v") == ["copy"] and after(fast.args, "-c:a:0") == ["copy"]
    assert mp.FAST_NOTE in fast.notes
    assert mp.fast_possible(src, MediaEdit(), "mp4")
    assert not mp.fast_possible(src, MediaEdit(speed=2.0), "mp4")
    assert not mp.fast_possible(src, MediaEdit(), "mkv")
    slow = mp.edit_args(src, MediaEdit(trim=(2.0, 5.0), fast=True, normalize=True), "mp4")
    assert "libx264" in slow.args and mp.FAST_NOTE not in slow.notes


def test_picture_chain_redact_crop_speed():
    edit = MediaEdit(
        crop=(100.0, 50.0, 1101.0, 651.0),
        regions=(Region((10, 10, 110, 60), 0.0, 10.0, "blur"),),
        speed=2.0,
    )
    cmd = mp.edit_args(video_source(streams=(VIDEO, AUDIO, SUBS)), edit, "mp4")
    g = graph(cmd)
    assert g.index("boxblur") < g.index("crop=1000:600:100:50") < g.index("setpts=PTS/2,fps=25")
    assert g.endswith("[vout];[0:1]atempo=2[a0]")
    assert after(cmd.args, "-map") == ["[vout]", "[a0]"]  # subtitles left out
    assert mp.SUBTITLES_NOTE in cmd.notes and cmd.duration == 5.0


def test_sound_chain_order_channels_only_on_the_first_track():
    edit = MediaEdit(channels="mono", bleeps=((1.0, 2.0),), speed=0.5, keep_pitch=False)
    cmd = mp.edit_args(video_source(streams=(VIDEO, AUDIO, AUDIO2)), edit, "mkv")
    g = graph(cmd)
    first = g[g.index("[0:1]") : g.index("[a0]")]
    assert first.index("pan=mono") < first.index("aeval") < first.index("asetrate")
    second = g[g.index("[0:3]") : g.index("[a1]")]
    assert "pan" not in second and "aeval" in second
    assert mp.FIRST_TRACK_NOTE in cmd.notes


def test_normalize_uses_the_measurement_and_keeps_the_rate():
    m = Loudness(-20.0, -3.0, 5.0, -30.0, 0.5)
    cmd = mp.edit_args(audio_source(), MediaEdit(normalize=True), "mp3", loudness=[m])
    assert "measured_I=-20.0" in graph(cmd) and after(cmd.args, "-ar:a:0") == ["44100"]
    measure = mp.measure_args(audio_source(), MediaEdit(normalize=True, trim=(1, 3)), "mp3", 0)
    assert (
        measure[:2] == ["-ss", "1.000"]
        and "print_format=json" in measure[measure.index("-filter_complex") + 1]
    )
    assert measure[-3:] == ["-f", "null", "-"]


def test_remove_audio_and_sound_targets():
    src = video_source()
    silent = mp.edit_args(src, MediaEdit(remove_audio=True), "mp4")
    assert after(silent.args, "-map") == ["0:0"]
    sound = mp.edit_args(video_source(streams=(VIDEO, AUDIO, AUDIO2)), MediaEdit(), "mp3")
    assert after(sound.args, "-map") == ["0:1"] and "libmp3lame" in sound.args
    assert mp.ONE_TRACK_NOTE in sound.notes
    with pytest.raises(JobError):
        mp.edit_args(src, MediaEdit(remove_audio=True), "mp3")


def test_gif_has_a_palette_and_no_sound():
    cmd = mp.edit_args(video_source(), MediaEdit(crop=(0, 0, 640, 360)), "gif")
    g = graph(cmd)
    assert "crop=640:360:0:0" in g and "palettegen" in g and g.endswith("[vout]")
    assert after(cmd.args, "-map") == ["[vout]"] and after(cmd.args, "-loop") == ["0"]


def test_cover_art_follows_sound_targets():
    cmd = mp.edit_args(audio_source(cover=True), MediaEdit(), "m4a")
    assert "attached_pic" in cmd.args
    assert "attached_pic" not in mp.edit_args(audio_source(cover=True), MediaEdit(), "ogg").args


def test_sound_as_a_video():
    src = audio_source()
    plain = mp.edit_args(src, MediaEdit(), "mp4")
    g = graph(plain)
    assert g.startswith("[0:0]anull[a];") and "showwaves" in g
    assert after(plain.args, "-map") == ["[v]", "0:0"]  # MP3 sound copied into the MP4
    edited = mp.edit_args(src, MediaEdit(normalize=True), "mp4")
    assert ",asplit[a][a0]" in graph(edited) and after(edited.args, "-map") == ["[v]", "[a0]"]
    still = replace(MediaEdit(), visual=Visual(style="still"))
    cmd = mp.edit_args(src, still, "mp4", picture=Path("/p.png"))
    assert after(cmd.args, "-loop") == ["1"] and "[1:v]null[pic]" in graph(cmd)
    assert "stillimage" in cmd.args
    with pytest.raises(JobError):
        mp.edit_args(src, still, "mp4")  # no picture
    with pytest.raises(JobError):
        mp.edit_args(src, MediaEdit(), "mkv")


def test_too_short_a_part_is_refused():
    with pytest.raises(JobError):
        mp.edit_args(video_source(), MediaEdit(trim=(1.0, 1.05)), "mp4")


def test_part_spans_inside_the_trim():
    src = video_source()
    edit = MediaEdit(trim=(2.0, 8.0))
    assert mp.part_spans(src, edit, parts=3) == ((2.0, 4.0), (4.0, 6.0), (6.0, 8.0))
    assert mp.part_spans(src, edit, markers=(1.0, 5.0, 9.0)) == ((2.0, 5.0), (5.0, 8.0))
    keys = (0.0, 3.0, 6.0)
    fast = mp.part_spans(src, MediaEdit(), markers=(4.0, 7.5), keys=keys)
    assert fast == ((0.0, 3.0), (3.0, 6.0), (6.0, 10.0))
    assert mp.part_spans(src, MediaEdit(), markers=(1.0,), keys=keys) == ((0.0, 10.0),)


def test_frame_args_crop_and_redact_at_that_time():
    src = video_source()
    edit = MediaEdit(
        crop=(0, 0, 640, 360),
        regions=(Region((0, 0, 100, 100), 0.0, 1.0), Region((0, 0, 50, 50), 2.0, 3.0)),
    )
    args = mp.frame_args(src, edit, 0.5, "jpg")
    g = args[args.index("-filter_complex") + 1]
    assert g.count("drawbox") == 1 and "crop=640:360:0:0" in g and "mjpeg" in args
    assert args[:2] == ["-ss", "0.500"] and after(args, "-frames:v") == ["1"]
    end = mp.frame_args(src, MediaEdit(), 10.0, "png", before=True)
    assert end[:4] == ["-ss", "8.960", "-t", "1.0"] and "-update" in end
