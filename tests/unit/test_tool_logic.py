"""Pure logic of the Phase 12 tools (no external programs)."""

import io
import struct
from pathlib import Path

import pytest
from PIL import Image

from quince.backends import compress, mediatools, metadata, qr
from quince.backends.compress import Goal, search
from quince.backends.ffmpeg import MediaInfo, Stream
from quince.core import formats
from quince.core.deps import DEPENDENCIES, Availability, DependencyStatus
from quince.core.jobs import JobError
from quince.core.operations import Kind
from quince.core.registry import discover
from quince.core.selection import Item, Selection
from quince.operations.actions import create_archive
from quince.operations.tools import compress as compress_tool
from quince.operations.tools import metadata as metadata_tool
from quince.operations.tools import read_qr

# --- compress ---------------------------------------------------------------------------------


def test_goal_targets():
    assert Goal("quality").target(1000) is None
    assert Goal("percent", percent=30).target(1000) == 300
    assert Goal("size", size_bytes=4096).target(10**6) == 4096
    compress.check_target(1000, 500)
    with pytest.raises(JobError, match=r"Already 1\.0 KB"):
        compress.check_target(1000, 2000)


def test_search_finds_the_best_step_that_fits():
    sizes = [100, 80, 60, 40, 20]
    calls = []

    def size_of(step):
        calls.append(step)
        return sizes[step]

    assert search(range(5), size_of, 70) == 2
    assert search(range(5), size_of, 100) == 0
    assert search(range(5), size_of, 10) is None
    assert len(calls) < 15


def test_compress_form_and_goal():
    from quince.core.formats import Category

    form = compress_tool.form_for(Category.IMAGE)
    assert form.id == "compress.image"
    values = form.clean({"mode": "size", "size": 1.5, "unit": "MB"})
    goal = compress_tool.goal_from(values)
    assert goal.mode == "size" and goal.size_bytes == int(1.5 * 1024 * 1024)
    assert compress_tool.title_for(goal, values) == "Compress to 1.5 MB"
    assert not form.is_shown("level", values) and form.is_shown("unit", values)


def test_video_bitrate_helpers():
    assert compress.video_output("avi") == "mp4" and compress.video_output("webm") == "webm"
    assert compress.output_extension("wmv") == "mp4"
    assert compress.height_cap(400) == 480 and compress.height_cap(5000) is None
    info = MediaInfo((Stream(0, "video", "h264", width=1920, height=1080),))
    args, resized = compress._scale_filter(info, 720)
    assert args == ["-vf", "scale=-2:720"] and resized == (1280, 720)
    assert compress._scale_filter(info, None)[1] is None


def test_image_ladders_go_from_best_to_smallest():
    im = Image.new("RGB", (40, 30), "orange")
    ladder = compress.ImageEncoder(im, "jpg").ladder()
    assert ladder[0].quality == 92 and ladder[0].scale == 1.0
    assert ladder[-1].scale == min(compress.SCALE_LADDER)
    png = compress.ImageEncoder(im, "png").ladder()
    assert png[0].colors is None  # lossless first


# --- metadata ----------------------------------------------------------------------------------


def _jpeg(**save) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 8), "red").save(buffer, "JPEG", **save)
    return buffer.getvalue()


def test_strip_jpeg_keeps_only_orientation_and_icc():
    exif = Image.Exif()
    exif[0x010F] = "Cam"
    exif[0x0112] = 3
    data = _jpeg(exif=exif.tobytes(), comment=b"hello", icc_profile=b"\0" * 64)
    clean = metadata.strip_jpeg(data)
    with Image.open(io.BytesIO(clean)) as im:
        assert dict(im.getexif()) == {0x0112: 3}
        assert "comment" not in im.info and im.info.get("icc_profile") == b"\0" * 64
    assert clean.endswith(b"\xff\xd9")
    # plain JPEG: unchanged structure, no EXIF added
    plain = _jpeg()
    assert metadata.strip_jpeg(plain) == plain


def test_strip_jpeg_drops_appended_images():
    data = _jpeg() + _jpeg(exif=Image.Exif().tobytes())  # an MPF-like second image
    clean = metadata.strip_jpeg(data)
    assert clean.count(b"\xff\xd8") == 1


def test_strip_rejects_damaged_files():
    with pytest.raises(JobError):
        metadata.strip_jpeg(b"not a jpeg")
    with pytest.raises(JobError):
        metadata.strip_png(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 999) + b"IHDR")
    with pytest.raises(JobError):
        metadata.strip_webp(b"RIFF\0\0\0\0WAVE")


def test_gps_position():
    gps = {1: "S", 2: (33.0, 52.0, 4.8), 3: "W", 4: (151.0, 12.0, 26.4)}
    assert metadata.gps_position(gps) == "-33.868000, -151.207333"
    assert metadata.gps_position({}) is None


def test_metadata_report_offers_removal_only_when_something_was_found():
    item = Item(Path("/x/a.jpg"))
    with_rows = metadata.Metadata([("Format", "JPEG")], [("Make", "Cam")])
    report = metadata_tool.build_report([(item, with_rows)])
    assert [a.key for a in report.actions] == ["strip"]
    assert report.sections[1].rows == (("Make", "Cam"),)
    empty = metadata_tool.build_report([(item, metadata.Metadata([], [])), (item, "broken")])
    assert empty.actions == () and empty.sections[2].rows == (("Cannot read", "broken"),)


# --- QR -----------------------------------------------------------------------------------------

ZBAR_XML = """<barcodes xmlns='http://zbar.sourceforge.net/2008/barcode'>
<source href='image.png'><index num='0'>
<symbol type='QR-Code' quality='1' orientation='UP'><data><![CDATA[line one
line two]]></data></symbol>
<symbol type='EAN-13' quality='1'><data format='base64'>
<![CDATA[NDAwNjM4MTMzMzkzMQ==]]></data></symbol>
</index></source></barcodes>"""


def test_parse_zbar_xml():
    symbols = qr.parse_xml(ZBAR_XML)
    assert symbols == [qr.Symbol("QR code", "line one\nline two"),
                       qr.Symbol("EAN-13 barcode", "4006381333931")]  # fmt: skip
    assert qr.parse_xml("") == []
    with pytest.raises(JobError):
        qr.parse_xml("<broken")


def test_qr_report():
    item = Item(Path("/x/code.png"))
    report = read_qr.build_report([(item, [qr.Symbol("QR code", "hi")])])
    assert report.copy_all == "hi" and "1 code found" in report.note
    report = read_qr.build_report([(item, [])])
    assert report.copy_all == "" and "No QR code" in report.note


def test_qr_fixture_encoder_matches_reference_values(make_fixtures):
    m = make_fixtures
    assert m._rs_divisor(7) == [127, 122, 154, 164, 11, 68, 117]
    data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
    expected = [196, 35, 39, 119, 235, 215, 231, 226, 93, 23]
    assert m._rs_remainder(data, m._rs_divisor(10)) == expected
    matrix = m.qr_matrix(m.QR_TEXT)
    assert len(matrix) == 29  # version 3
    assert matrix[0][:7] == [True] * 7  # finder pattern
    assert matrix[len(matrix) - 8][8] is True  # the dark module


# --- audio / video ------------------------------------------------------------------------------

LOUDNORM = """[Parsed_loudnorm_0 @ 0x55]
{
\t"input_i" : "-27.31",
\t"input_tp" : "-20.12",
\t"input_lra" : "0.00",
\t"input_thresh" : "-37.31",
\t"output_i" : "-16.0",
\t"target_offset" : "0.11"
}"""


def test_parse_loudnorm():
    m = mediatools.parse_loudnorm("noise\n" + LOUDNORM)
    assert (m.integrated, m.true_peak, m.offset) == (-27.31, -20.12, 0.11)
    silent = LOUDNORM.replace('"-27.31"', '"-inf"')
    with pytest.raises(JobError, match="silent"):
        mediatools.parse_loudnorm(silent)
    with pytest.raises(JobError):
        mediatools.parse_loudnorm("no json here")
    assert "measured_I=-27.31" in mediatools._loudnorm(m) and "linear=true" in mediatools._loudnorm(
        m
    )


def _video(w=640, h=360, codec="h264", audio=True, duration=2.0):
    streams = [Stream(0, "video", codec, width=w, height=h, fps=25.0)]
    if audio:
        streams.append(Stream(1, "audio", "aac", channels=2, sample_rate=48000))
    return MediaInfo(tuple(streams), duration)


def test_join_decides_copy_or_encode():
    assert mediatools.can_copy([_video(), _video()], ["mp4", "mp4"])
    assert not mediatools.can_copy([_video(), _video()], ["mp4", "mkv"])
    assert not mediatools.can_copy([_video(), _video(w=320)], ["mp4", "mp4"])
    assert not mediatools.can_copy([_video(), _video(audio=False)], ["mp4", "mp4"])


def test_join_filter_fills_silence_for_videos_without_sound():
    graph = mediatools.join_filter([_video(), _video(audio=False, duration=1.5)], 640, 360, 25)
    assert "aevalsrc=0|0:d=1.500:s=48000[a1]" in graph
    assert graph.endswith("[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
    silent = mediatools.join_filter([_video(audio=False), _video(audio=False)], 640, 360, 25)
    assert "[a" not in silent and silent.endswith("concat=n=2:v=1:a=0[v]")


def test_concat_list_escapes_quotes():
    text = mediatools._concat_list([Path("/a/it's.mp4"), Path("/b/c.mp4")])
    assert text == "file '/a/it'\\''s.mp4'\nfile '/b/c.mp4'\n"


# --- create archive -----------------------------------------------------------------------------


def test_create_archive_names(tmp_path):
    folder = tmp_path / "Tatil 2026"
    folder.mkdir()
    file = tmp_path / "rapor.final.pdf"
    file.write_text("x")
    assert create_archive.default_name(Selection.from_paths([folder])) == "Tatil 2026"
    assert create_archive.default_name(Selection.from_paths([file])) == "rapor.final"
    assert create_archive.default_name(Selection.from_paths([folder, file])) == "Archive"
    assert create_archive.clean_name(" a/b ") == "a-b"
    assert create_archive.clean_name("..") == "Archive"
    form = create_archive.form_for(Selection.from_paths([folder]), has_7z=False)
    assert [v for v, _ in form.get("format").options] == ["zip", "targz"]


# --- registry -------------------------------------------------------------------------------------


def _available(*missing):
    return Availability(DependencyStatus(d, d.id not in missing) for d in DEPENDENCIES)


def _selection(tmp_path, *names):
    paths = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"%PDF-1.4\n" if name.endswith(".pdf") else b"")
        paths.append(path)
    return Selection.from_paths(paths)


def test_missing_dependency_hides_a_tool_only_for_its_formats(tmp_path):
    registry = discover()
    pdf = _selection(tmp_path, "a.pdf")
    tools = [op.id for op in registry.query(pdf, Kind.TOOL, available=_available("gs"))]
    assert "tool.compress" not in tools and "tool.metadata" in tools
    jpg = Selection((Item(tmp_path / "a.jpg", format=formats.get("jpg")),))
    tools = [op.id for op in registry.query(jpg, Kind.TOOL, available=_available("gs"))]
    assert "tool.compress" in tools
    hidden = {(h.id, h.label) for h in registry.unavailable(_available("gs", "7z"))}
    assert ("tool.compress", "Compress (PDF)") in hidden
    assert ("tool.extract_archive", "Extract (7Z)") in hidden


def test_every_tool_declares_known_dependencies():
    registry = discover()
    tools = [op for op in registry if op.kind is Kind.TOOL]
    assert {op.id for op in tools} >= {
        "tool.compress", "tool.metadata", "tool.remove_audio", "tool.normalize_audio",
        "tool.extract_archive", "tool.images_to_pdf", "tool.merge_pdfs", "tool.join_videos",
        "tool.read_qr", "tool.edit_image", "tool.edit_video", "tool.edit_audio",
        "tool.edit_pdf", "tool.create_collage",
    }  # fmt: skip
    assert all(op.run or op.start or op.editor for op in tools)


def test_edit_slices_share_one_bundled_icon():
    from quince.ui.theme.icons import ICON_DIR

    registry = discover()
    names = ("tool.edit_image", "tool.edit_video", "tool.edit_audio", "tool.edit_pdf")
    edits = [registry.get(i) for i in names]
    assert {op.icon for op in edits} == {"quince-edit"}
    bundled = {op.icon for op in registry if op.icon.startswith("quince-")}
    assert bundled and all((ICON_DIR / f"{name}.svg").is_file() for name in bundled)


def test_bundled_icons_load(qapp):
    from quince.ui.theme.icons import themed

    for name in (
        "quince-edit",
        "quince-crop",
        "quince-redact",
        "quince-collage",
        "quince-organize",
    ):
        assert not themed(name).isNull(), name
