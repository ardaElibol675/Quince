"""The ffmpeg conversion planner (``quince.backends.media.plan``), on synthetic stream lists."""

import json

import pytest

from quince.backends import media
from quince.backends.ffmpeg import MediaInfo, Stream, parse_probe
from quince.core.jobs import JobError


def A(index=1, codec="aac", channels=2, rate=48000, bits=0, fmt="fltp"):
    return Stream(
        index, "audio", codec, channels=channels, sample_rate=rate, bits=bits, sample_fmt=fmt
    )


def V(index=0, codec="h264", w=1920, h=1080, fps=30.0, cover=False):
    return Stream(index, "video", codec, width=w, height=h, fps=fps, attached_pic=cover)


def S(index, codec="subrip"):
    return Stream(index, "subtitle", codec)


def info(*streams, duration=10.0):
    return MediaInfo(tuple(streams), duration)


def pairs(args):
    """Option/value pairs of an argument list, for order-independent checks."""
    return list(zip(args[::2], args[1::2], strict=False))


# --- probing ------------------------------------------------------------------------


def test_parse_probe():
    text = json.dumps(
        {
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1280,
                 "height": 720, "avg_frame_rate": "30000/1001", "disposition": {"attached_pic": 0}},
                {"index": 1, "codec_type": "audio", "codec_name": "flac", "channels": 2,
                 "sample_rate": "96000", "sample_fmt": "s32", "bits_per_raw_sample": "24"},
                {"index": 2, "codec_type": "audio", "codec_name": "mp3", "channels": 2,
                 "sample_rate": "44100", "sample_fmt": "fltp"},
                {"index": 3, "codec_type": "video", "codec_name": "mjpeg",
                 "avg_frame_rate": "0/0", "disposition": {"attached_pic": 1}},
                {"index": 4, "codec_type": "subtitle", "codec_name": "subrip"},
            ],
            "format": {"duration": "12.5"},
        }
    )  # fmt: skip
    parsed = parse_probe(text)
    assert parsed.duration == 12.5
    assert parsed.video.codec == "h264" and parsed.video.fps == pytest.approx(29.97, 0.01)
    assert [a.bits for a in parsed.audio] == [24, 0]  # lossy MP3 has no stored depth
    assert parsed.audio[0].sample_rate == 96000
    assert parsed.cover.index == 3 and parsed.subtitles[0].codec == "subrip"
    assert parse_probe('{"format": {"duration": "N/A"}}').duration is None
    assert parse_probe("").streams == ()


# --- audio targets ------------------------------------------------------------------------


def test_aac_from_a_video_is_copied_into_m4a():
    plan = media.plan(info(V(0), A(1, "aac")), "mp4", "m4a")
    assert plan.args[:4] == ["-map", "0:1", "-c:a", "copy"]
    assert plan.notes == [media.COPIED_NOTE]


def test_audio_encoders():
    src = info(A(0, "flac", bits=16, fmt="s16"))
    expected = {
        "mp3": ("-c:a", "libmp3lame"),
        "m4a": ("-c:a", "aac"),
        "wav": ("-c:a", "pcm_s16le"),
        "ogg": ("-c:a", "libvorbis"),
        "opus": ("-c:a", "libopus"),
        "aiff": ("-c:a", "pcm_s16be"),
        "wma": ("-c:a", "wmav2"),
    }
    for target, codec in expected.items():
        plan = media.plan(src, "flac", target)
        assert codec in pairs(plan.args), target
        assert media.COPIED_NOTE not in plan.notes
    assert ("-q:a", "2") in pairs(media.plan(src, "flac", "mp3").args)  # VBR V2
    assert ("-b:a", "192k") in pairs(media.plan(src, "flac", "m4a").args)
    assert ("-id3v2_version", "3") in pairs(media.plan(src, "flac", "mp3").args)


def test_bit_depth():
    deep = info(A(0, "flac", bits=24, fmt="s32"))
    assert ("-c:a", "pcm_s24le") in pairs(media.plan(deep, "flac", "wav").args)
    assert ("-c:a", "pcm_s24be") in pairs(media.plan(deep, "flac", "aiff").args)
    assert ("-sample_fmt:a", "s32") in pairs(
        media.plan(info(A(0, "pcm_s24le", bits=24)), "wav", "flac").args
    )
    lossy = info(A(0, "mp3", bits=0))  # decodes to float, but is no 24-bit source
    assert ("-c:a", "pcm_s16le") in pairs(media.plan(lossy, "mp3", "wav").args)
    assert "-sample_fmt:a" not in media.plan(lossy, "mp3", "flac").args


def test_channel_and_rate_limits():
    surround = info(A(0, "flac", channels=6, rate=96000, bits=24))
    mp3 = media.plan(surround, "flac", "mp3")
    assert ("-ac:a", "2") in pairs(mp3.args) and media.DOWNMIX_NOTE in mp3.notes
    assert ("-ar:a", "48000") in pairs(mp3.args)
    wma = media.plan(surround, "flac", "wma")
    assert ("-ac:a", "2") in pairs(wma.args) and ("-ar:a", "48000") in pairs(wma.args)
    m4a = media.plan(surround, "flac", "m4a")
    assert ("-b:a", "384k") in pairs(m4a.args) and not m4a.notes
    opus = media.plan(surround, "flac", "opus")
    assert ("-b:a", "320k") in pairs(opus.args) and "-ac:a" not in opus.args
    # A copyable codec still gets encoded when it breaks a limit.
    wide_wma = info(A(0, "wmav2", channels=2, rate=96000))
    assert ("-c:a", "copy") not in pairs(media.plan(wide_wma, "wma", "wma").args)


def test_cover_art():
    with_cover = info(A(0, "mp3"), V(1, "mjpeg", cover=True))
    for target in ("flac", "m4a"):
        plan = media.plan(with_cover, "mp3", target)
        assert ("-map", "0:1") in pairs(plan.args)
        assert ("-disposition:v:0", "attached_pic") in pairs(plan.args)
    for target in ("ogg", "opus", "wav", "wma"):
        plan = media.plan(with_cover, "mp3", target)
        assert "0:1" not in plan.args and "Cover art was not kept" in plan.notes
    odd = info(A(0, "flac"), V(1, "bmp", cover=True))
    assert "Cover art was not kept" in media.plan(odd, "flac", "mp3").notes


def test_audio_target_takes_the_first_track():
    plan = media.plan(info(V(0), A(1, "aac"), A(2, "ac3")), "mkv", "mp3")
    assert ("-map", "0:1") in pairs(plan.args) and "0:2" not in plan.args
    assert "Only the first audio track was kept" in plan.notes
    with pytest.raises(JobError, match="No sound to convert"):
        media.plan(info(V(0)), "mp4", "wav")


# --- video targets ------------------------------------------------------------------------


def test_everything_copied_when_the_container_allows_it():
    plan = media.plan(info(V(0, "h264"), A(1, "aac")), "mp4", "mkv")
    assert pairs(plan.args)[:4] == [
        ("-map", "0:0"),
        ("-c:v", "copy"),
        ("-map", "0:1"),
        ("-c:a:0", "copy"),
    ]
    assert plan.notes == [media.COPIED_NOTE]
    mov = media.plan(info(V(0, "h264"), A(1, "aac")), "mp4", "mov")
    assert ("-movflags", "+faststart") in pairs(mov.args)


def test_only_the_incompatible_stream_is_encoded():
    plan = media.plan(info(V(0, "h264"), A(1, "vorbis")), "mkv", "mp4")
    assert ("-c:v", "copy") in pairs(plan.args)
    assert ("-c:a:0", "aac") in pairs(plan.args)
    assert media.COPIED_NOTE not in plan.notes


def test_video_encoders():
    src = info(V(0, "vp9"), A(1, "opus"))
    mp4 = media.plan(src, "webm", "mp4")
    assert ("-c:v", "libx264") in pairs(mp4.args) and ("-crf", "20") in pairs(mp4.args)
    assert ("-vf", media._EVEN) in pairs(mp4.args)
    webm = media.plan(info(V(0, "h264"), A(1, "aac")), "mp4", "webm")
    assert ("-c:v", "libvpx-vp9") in pairs(webm.args) and ("-crf", "31") in pairs(webm.args)
    assert ("-c:a:0", "libopus") in pairs(webm.args)
    avi = media.plan(src, "webm", "avi")
    assert ("-c:v", "mpeg4") in pairs(avi.args) and ("-c:a:0", "libmp3lame") in pairs(avi.args)
    wmv = media.plan(src, "webm", "wmv")
    assert ("-c:v", "wmv2") in pairs(wmv.args) and ("-c:a:0", "wmav2") in pairs(wmv.args)


def test_hevc_is_tagged_for_apple_players():
    plan = media.plan(info(V(0, "hevc"), A(1, "aac")), "mkv", "mp4")
    assert ("-tag:v", "hvc1") in pairs(plan.args)


def test_every_audio_track_is_kept_in_a_video():
    plan = media.plan(info(V(0), A(1, "aac", channels=6), A(2, "vorbis")), "mkv", "avi")
    assert ("-map", "0:1") in pairs(plan.args) and ("-map", "0:2") in pairs(plan.args)
    assert ("-c:a:0", "libmp3lame") in pairs(plan.args) and ("-ac:a:0", "2") in pairs(plan.args)
    assert ("-c:a:1", "libmp3lame") in pairs(plan.args)
    assert media.DOWNMIX_NOTE in plan.notes


def test_subtitles():
    src = info(V(0), A(1), S(2, "subrip"), S(3, "hdmv_pgs_subtitle"))
    mp4 = media.plan(src, "mkv", "mp4")
    assert ("-map", "0:2") in pairs(mp4.args) and ("-c:s:0", "mov_text") in pairs(mp4.args)
    assert "0:3" not in mp4.args
    assert "Subtitles were not kept (image-based)" in mp4.notes
    webm = media.plan(src, "mkv", "webm")
    assert ("-c:s:0", "webvtt") in pairs(webm.args)
    mkv = media.plan(info(V(0), A(1), S(2, "mov_text"), S(3, "hdmv_pgs_subtitle")), "mp4", "mkv")
    assert ("-c:s:0", "srt") in pairs(mkv.args) and ("-c:s:1", "copy") in pairs(mkv.args)
    avi = media.plan(src, "mkv", "avi")
    assert "-c:s:0" not in avi.args
    assert "Subtitles were not kept (AVI cannot store them)" in avi.notes


def test_attachments_stay_in_matroska():
    src = info(V(0), A(1), S(2, "ass"), Stream(3, "attachment", "ttf"))
    plan = media.plan(src, "webm", "mkv")
    assert ("-map", "0:3") in pairs(plan.args) and ("-c:t", "copy") in pairs(plan.args)
    assert "0:3" not in media.plan(src, "mkv", "mp4").args


def test_a_cover_is_not_the_video_track():
    with pytest.raises(JobError, match="No video track"):
        media.plan(info(A(0, "mp3"), V(1, "mjpeg", cover=True)), "mkv", "mp4")


# --- GIF -------------------------------------------------------------------------------------


def test_gif_from_video():
    plan = media.plan(info(V(0, fps=30.0), A(1)), "mp4", "gif")
    vf = dict(pairs(plan.args))["-vf"]
    assert vf.startswith("fps=15,")
    assert "min(480,iw)" in vf and "min(480,ih)" in vf  # the longer side, portrait too
    assert "palettegen" in vf and "paletteuse" in vf
    assert "0:1" not in plan.args  # no sound in a GIF
    slow = media.plan(info(V(0, fps=10.0)), "mp4", "gif")
    assert dict(pairs(slow.args))["-vf"].startswith("fps=10,")  # never more frames than the source


def test_gif_into_video():
    plan = media.plan(info(V(0, "gif", w=33, h=21)), "gif", "mp4")
    vf = dict(pairs(plan.args))["-vf"]
    assert "drawbox=c=white" in vf and vf.endswith(media._EVEN)
    assert ("-c:v", "libx264") in pairs(plan.args)
    assert ("-movflags", "+faststart") in pairs(plan.args)


def test_unknown_target():
    with pytest.raises(JobError, match="cannot write"):
        media.plan(info(V(0)), "mp4", "png")
