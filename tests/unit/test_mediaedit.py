"""The media editors' ffmpeg plans (pure argument builders, no ffmpeg run)."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from quince.backends import mediaedit as me
from quince.backends.ffmpeg import MediaInfo, Stream
from quince.backends.mediaedit import Region, Source
from quince.core.jobs import JobError

VIDEO = Stream(0, "video", "h264", width=1920, height=1080, fps=25.0)
AUDIO = Stream(1, "audio", "aac", channels=2, sample_rate=48000)
SUBS = Stream(2, "subtitle", "mov_text")


def video_source(fmt="mp4", streams=(VIDEO, AUDIO), duration=10.0) -> Source:
    return Source(Path(f"/v/clip.{fmt}"), fmt, MediaInfo(tuple(streams), duration), (1920, 1080))


def audio_source(fmt="mp3", codec="mp3", channels=2) -> Source:
    stream = Stream(0, "audio", codec, channels=channels, sample_rate=44100)
    return Source(Path(f"/a/song.{fmt}"), fmt, MediaInfo((stream,), 30.0))


def after(args, flag):
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


# --- keyframes, cut points ---------------------------------------------------------------------


def test_snap_back_to_keyframe():
    keys = (0.0, 2.0, 4.0)
    assert me.snap_back(3.9, keys) == 2.0
    assert me.snap_back(4.0, keys) == 4.0
    assert me.snap_back(1.0, ()) == 0.0


def test_split_points_and_segments():
    assert me.split_points(10.0, parts=4) == (2.5, 5.0, 7.5)
    assert me.split_points(10.0, every=3.0) == (3.0, 6.0, 9.0)
    assert me.split_points(10.0, every=5.0) == (5.0,)  # no empty last part
    # markers outside, at the very edges or too close together are ignored
    assert me.split_points(10.0, markers=(0.01, 4.0, 4.05, 12.0, 9.99)) == (4.0,)
    assert me.segments(10.0, (4.0,)) == ((0.0, 4.0), (4.0, 10.0))
    # a fast cut moves every point back to its keyframe; parts stay gapless
    keys = (0.0, 3.0, 6.0)
    assert me.segments(10.0, (4.0, 7.5), keys) == ((0.0, 3.0), (3.0, 6.0), (6.0, 10.0))
    assert me.segments(10.0, (1.0,), keys) == ((0.0, 10.0),)  # snapped to the start: one part


def test_cut_args_exact_fast_and_audio():
    src = video_source(streams=(VIDEO, AUDIO, SUBS))
    exact = me.cut_args(src, 2.0, 5.0, fast=False)
    assert exact[:5] == ["-ss", "2.000", "-i", src.path, "-t"] and after(exact, "-t") == ["3.000"]
    assert "libx264" in exact and after(exact, "-map")[:3] == ["0:0", "0:1", "0:2"]
    assert after(exact, "-c:a:0") == ["aac"]  # so the sound starts with the picture
    assert after(exact, "-map_chapters") == ["-1"] and exact[-2:] == ["-f", "mp4"]
    fast = me.cut_args(src, 2.0, 5.0, fast=True)
    assert "libx264" not in fast and after(fast, "-c") == ["copy"] and "-c:a:0" not in fast
    lossy = me.cut_args(audio_source(), 1.0, 2.0, fast=False)
    assert after(lossy, "-c:a") == ["copy"]
    lossless = me.cut_args(audio_source("flac", "flac"), 1.0, 2.0, fast=False)
    assert after(lossless, "-c:a") == ["flac"]  # exact, still lossless


def test_subtitles_kept_only_when_the_container_stores_them():
    picture = Stream(3, "subtitle", "hdmv_pgs_subtitle")
    mp4 = me.video_maps(MediaInfo((VIDEO, AUDIO, SUBS, picture)), "mp4")
    assert "0:2" in mp4 and "0:3" not in mp4
    mkv = me.video_maps(MediaInfo((VIDEO, AUDIO, SUBS, picture)), "mkv")
    assert "0:2" in mkv and "0:3" in mkv
    with pytest.raises(JobError):
        me.video_maps(MediaInfo((AUDIO,)), "mp4")


# --- crop, speed -------------------------------------------------------------------------------


def test_even_box():
    assert me.even_box((11.0, 13.0, 101.4, 90.0), (160, 120)) == (10, 12, 90, 78)
    assert me.even_box((0.0, 0.0, 161.0, 121.0), (161, 121)) == (0, 0, 160, 120)
    assert me.even_box((159.0, 119.0, 160.0, 120.0), (160, 120))[2:] == (2, 2)


def test_crop_args():
    args = me.crop_args(video_source(), (100.0, 50.0, 1100.0, 650.0))
    assert after(args, "-filter:v") == ["crop=1000:600:100:50"]


def test_atempo_chain_stays_in_range():
    assert me.atempo_chain(1.5) == "atempo=1.5"
    assert me.atempo_chain(4.0) == "atempo=2,atempo=2"
    assert me.atempo_chain(0.25) == "atempo=0.5,atempo=0.5"
    assert me.atempo_chain(3.0) == "atempo=2,atempo=1.5"


def test_speed_args():
    args = me.speed_args(video_source(streams=(VIDEO, AUDIO, SUBS)), 2.0)
    assert after(args, "-filter:v") == ["setpts=PTS/2,fps=25"]
    assert after(args, "-filter:a:0") == ["atempo=2"]
    assert "0:2" not in after(args, "-map")  # subtitles left out
    tape = me.speed_args(audio_source(), 0.5, keep_pitch=False)
    assert after(tape, "-filter:a:0") == ["asetrate=22050,aresample=44100"]
    for wrong in (1.0, 0.1, 5.0):
        with pytest.raises(JobError):
            me.speed_args(audio_source(), wrong)


# --- snapshots ----------------------------------------------------------------------------------


def test_snapshot_args_stay_before_the_last_frame():
    src = video_source()
    args = me.snapshot_args(src, 10.0, "jpg")
    assert args[:2] == ["-ss", "9.960"] and "mjpeg" in args and after(args, "-frames:v") == ["1"]
    before = me.snapshot_args(src, 10.0, "png", before=True)
    assert before[:4] == ["-ss", "8.960", "-t", "1.0"] and "-update" in before


# --- redact video -------------------------------------------------------------------------------


def test_redact_graph_chains_regions_with_time_ranges():
    regions = [
        Region((10, 10, 110, 60), 1.0, 2.5, "solid"),
        Region((0, 0, 200, 100), 0.0, 10.0, "blur"),
        Region((50, 50, 90, 70), 3.0, 4.0, "pixelate"),
    ]
    graph = me.redact_graph(regions, (1920, 1080), 0)
    assert graph.startswith("[0:0]null[v0];[v0]drawbox=x=10:y=10:w=100:h=50")
    assert "enable='between(t,1.000,2.500)'[v1]" in graph
    assert "crop=200:100:0:0,boxblur=lr=25:lp=3:cr=12:cp=3" in graph
    assert "scale=8:4:flags=area,scale=40:20:flags=neighbor" in graph and graph.endswith("[v3]")
    args = me.redact_video_args(video_source(), regions)
    assert after(args, "-map")[0] == "[v3]" and after(args, "-map_metadata") == ["-1"]
    with pytest.raises(JobError):
        me.redact_video_args(video_source(), [])


def test_blur_radii_fit_small_areas():
    assert me.blur_radii(4, 4) == (1, 0)
    luma, chroma = me.blur_radii(40, 400)
    assert luma <= 19 and chroma <= 9


def test_region_visibility():
    region = Region((0, 0, 1, 1), 1.0, 2.0)
    assert region.visible(1.0) and region.visible(2.0) and not region.visible(2.1)


# --- channels -----------------------------------------------------------------------------------


def test_channel_modes_and_filters():
    assert me.channel_modes(1) == ("stereo",)
    assert "swap" in me.channel_modes(2) and me.channel_modes(6) == ("stereo", "mono")
    assert me.channel_filter("swap", 2) == ("pan=stereo|c0=c1|c1=c0", 2)
    assert me.channel_filter("mono", 2) == ("pan=mono|c0=0.5*c0+0.5*c1", 1)
    assert me.channel_filter("stereo", 1) == ("pan=stereo|c0=c0|c1=c0", 2)
    assert me.channel_filter("stereo", 6)[0] == "aformat=channel_layouts=stereo"
    with pytest.raises(JobError):
        me.channel_filter("swap", 1)


def test_channel_lanes_preview():
    left, right = (0.1, 0.9), (0.5, 0.2)
    assert me.channel_lanes("swap", (left, right)) == (right, left)
    assert me.channel_lanes("mute_left", (left, right)) == ((0.0, 0.0), right)
    assert me.channel_lanes("mono", (left, right)) == ((0.5, 0.9),)
    assert me.channel_lanes("stereo", ((0.3,),)) == ((0.3,), (0.3,))


def test_channels_args_change_only_the_first_track():
    second = Stream(2, "audio", "aac", channels=2, sample_rate=48000)
    args = me.channels_args(video_source(streams=(VIDEO, AUDIO, second)), "mono")
    assert after(args, "-filter:a:0") == ["pan=mono|c0=0.5*c0+0.5*c1"]
    assert "-filter:a:1" not in args and after(args, "-c:a:0") == ["aac"]
    mp3 = me.channels_args(audio_source(channels=1), "stereo")
    assert after(mp3, "-c:a:0") == ["libmp3lame"]


# --- redact audio -------------------------------------------------------------------------------


def test_merge_spans():
    assert me.merge_spans([(5, 6), (1, 2), (1.5, 3), (4, 4)]) == ((1, 3), (5, 6))
    assert me.merge_spans([(2, 1)]) == ((1, 2),)


def test_redact_audio_filters():
    silence = me.redact_audio_filter([(1, 2), (3, 4)], "silence")
    assert silence == "volume=0:enable='between(t,1.000,2.000)+between(t,3.000,4.000)'"
    bleep = me.redact_audio_filter([(1, 2)], "bleep")
    assert bleep.startswith("aeval=exprs='if(between(t,1.000,2.000),0.3*sin(2*PI*1000*t)")
    assert bleep.endswith(",val(ch))':c=same")
    with pytest.raises(JobError):
        me.redact_audio_filter([], "bleep")


# --- audio to video ------------------------------------------------------------------------------


def test_visual_graphs():
    wave = me.visual_graph("waveform", (1920, 1080), (0, 0, 0), (255, 136, 0))
    assert "showwaves=s=1920x432" in wave and "colors=0xff8800" in wave and wave.endswith("[v]")
    assert "showfreqs" in me.visual_graph("bars", (1080, 1080), (0, 0, 0), (1, 2, 3))
    assert "showcqt=s=1080x1080" in me.visual_graph("spectrum", (1080, 1080), (0, 0, 0), (1, 2, 3))
    still = me.visual_graph("still", (1920, 1080), (16, 30, 58), (0, 0, 0))
    assert "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x101e3a" in still


def test_visual_args_inputs():
    src = audio_source()
    still = me.visual_args(src, "still", (1920, 1080), (0, 0, 0), (0, 0, 0), Path("/p.png"))
    assert still[:2] == ["-loop", "1"] and after(still, "-map") == ["[v]", "1:0"]
    with pytest.raises(JobError):
        me.visual_args(src, "still", (1920, 1080), (0, 0, 0), (0, 0, 0), None)
    copy = me.visual_encode(src, "waveform")
    assert after(copy, "-c:a") == ["copy"] and "-shortest" not in copy
    ogg = me.visual_encode(audio_source("ogg", "vorbis"), "waveform")
    assert after(ogg, "-c:a") == ["aac"]


# --- probing ------------------------------------------------------------------------------------


def test_display_size_follows_rotation():
    info = MediaInfo((Stream(0, "video", "h264", width=1920, height=1080),), 1.0)
    rotated = {"streams": [{"index": 0, "side_data_list": [{"rotation": -90}]}]}
    assert me.display_size(rotated, info) == (1080, 1920)
    tagged = {"streams": [{"index": 0, "tags": {"rotate": "180"}}]}
    assert me.display_size(tagged, info) == (1920, 1080)
    assert me.display_size({}, MediaInfo((AUDIO,), 1.0)) == (0, 0)


def test_read_peaks(tmp_path):
    raw = tmp_path / "w.raw"
    samples = [0, 16384, -32767, 100] * 3  # stereo frames: (0, 16384), (-32767, 100), ...
    raw.write_bytes(struct.pack(f"<{len(samples)}h", *samples))
    wave = me.read_peaks(raw, 2, buckets=3)
    assert wave.channels == 2 and len(wave.lanes[0]) == 3
    assert wave.lanes[0][0] == pytest.approx(1.0) and wave.lanes[1][0] == pytest.approx(0.5, 0.01)
    empty = tmp_path / "e.raw"
    empty.write_bytes(b"")
    assert me.read_peaks(empty, 1).lanes == ((),)
