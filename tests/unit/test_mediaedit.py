"""The pieces of the media editors' ffmpeg work (pure builders, no ffmpeg run)."""

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


def test_split_points():
    assert me.split_points(10.0, parts=4) == (2.5, 5.0, 7.5)
    assert me.split_points(10.0, every=3.0) == (3.0, 6.0, 9.0)
    assert me.split_points(10.0, every=5.0) == (5.0,)  # no empty last part
    # markers outside, at the very edges or too close together are ignored
    assert me.split_points(10.0, markers=(0.01, 4.0, 4.05, 12.0, 9.99)) == (4.0,)


# --- crop, speed -------------------------------------------------------------------------------


def test_even_box():
    assert me.even_box((11.0, 13.0, 101.4, 90.0), (160, 120)) == (10, 12, 90, 78)
    assert me.even_box((0.0, 0.0, 161.0, 121.0), (161, 121)) == (0, 0, 160, 120)
    assert me.even_box((159.0, 119.0, 160.0, 120.0), (160, 120))[2:] == (2, 2)


def test_atempo_chain_stays_in_range():
    assert me.atempo_chain(1.5) == "atempo=1.5"
    assert me.atempo_chain(4.0) == "atempo=2,atempo=2"
    assert me.atempo_chain(0.25) == "atempo=0.5,atempo=0.5"
    assert me.atempo_chain(3.0) == "atempo=2,atempo=1.5"


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
