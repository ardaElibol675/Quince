"""Every Phase 12 tool on real files, through the registry, a stand-in Host and the job queue.

Inputs are made here (large enough to compress) or taken from the generated fixtures. Tests
that need a missing program (ffmpeg, gs, zbarimg, 7z) are skipped with the reason.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from quince.backends import metadata
from quince.core import filetype
from quince.core.deps import Availability
from quince.core.jobs import JobQueue, JobState
from quince.core.operations import Kind
from quince.core.registry import discover
from quince.core.selection import Selection
from tests.unit.test_jobs import wait_finished

pytestmark = pytest.mark.integration

GENERATED = Path(__file__).resolve().parent.parent / "fixtures" / "generated"
REGISTRY = discover()
AVAILABLE = Availability.probe()


def needs(*deps):
    missing = AVAILABLE.missing(deps)
    return pytest.mark.skipif(bool(missing), reason=f"needs {', '.join(missing)}")


class FakeHost:
    """Answers the dialogs like a user would; records the report it was shown."""

    def __init__(self, options=None, order=None, report_answer=None):
        self.options = options or {}
        self.order = order  # callable(paths) -> paths, or None for "as offered"
        self.report_answer = report_answer
        self.reports = []
        self.offered_order = None

    def ask_options(self, form):
        return form.clean({**form.defaults(), **self.options})

    def ask_order(self, title, paths, *, accept_label):
        self.offered_order = list(paths)
        return self.order(list(paths)) if self.order else list(paths)

    def compute(self, message, work):
        return work()

    def show_report(self, report):
        self.reports.append(report)
        keys = {action.key for action in report.actions}
        return self.report_answer if self.report_answer in keys else None

    def notify(self, text):
        pass


def run_op(op_id, paths, host=None, timeout=120.0):
    op = REGISTRY.get(op_id)
    selection = Selection.from_paths(paths)
    assert op in REGISTRY.query(selection, op.kind, available=AVAILABLE), "not offered"
    run = op.run
    title = op.title
    if op.start is not None:
        request = op.start(selection, host or FakeHost())
        if request is None:
            return None
        run, title = request.run, request.title
    queue = JobQueue(workers=1)
    try:
        return wait_finished(queue, queue.submit(run, selection, title=title), timeout)
    finally:
        queue.shutdown()


def ok(snap):
    assert snap is not None
    assert snap.state is JobState.SUCCEEDED, snap.error or snap.failures
    return snap.outputs


def failed(snap):
    assert snap.state is JobState.FAILED, snap
    return snap.error


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def photo(path: Path, size=(1200, 900), **save) -> Path:
    """A detailed picture (gradients and noise) that compresses like a photo."""
    noise = Image.effect_noise(size, 40).convert("L")
    gradient = Image.linear_gradient("L")
    across = gradient.transpose(Image.Transpose.ROTATE_90).resize(size)
    im = Image.merge("RGB", (noise, gradient.resize(size), across))
    im.save(path, **save)
    return path


def copy_fixture(name: str, tmp_path: Path) -> Path:
    src = GENERATED / name
    if not src.exists():
        pytest.skip(f"{name} missing; run scripts/make_fixtures.py")
    return Path(shutil.copy(src, tmp_path / name))


# --- the Tools wheel --------------------------------------------------------------------------


def test_tools_offered_per_kind_of_file(tmp_path):
    def tools(*names):
        paths = [copy_fixture(n, tmp_path) for n in names]
        sel = Selection.from_paths(paths)
        return [op.id for op in REGISTRY.query(sel, Kind.TOOL)]

    assert tools("sample.jpg") == [
        "tool.edit_image", "tool.compress", "tool.metadata", "tool.read_qr",
    ]  # fmt: skip
    assert "tool.images_to_pdf" in tools("sample.jpg", "sample.png")
    assert "tool.create_collage" in tools("sample.jpg", "sample.png")
    assert "tool.edit_image" not in tools("sample.jpg", "sample.png")
    assert tools("sample.pdf") == ["tool.edit_pdf", "tool.compress", "tool.metadata"]
    assert "tool.merge_pdfs" not in tools("sample.pdf")
    pdf = copy_fixture("sample.pdf", tmp_path)
    two = Selection.from_paths([pdf, shutil.copy(pdf, tmp_path / "second.pdf")])
    both_pdfs = [op.id for op in REGISTRY.query(two, Kind.TOOL)]
    assert both_pdfs[0] == "tool.edit_pdf" and "tool.merge_pdfs" in both_pdfs  # pages of both
    assert tools("sample-video.mp4") == ["tool.edit_video", "tool.compress", "tool.metadata"]
    both = tools("sample-video.mp4", "sample-video.mkv")
    assert "tool.join_videos" in both and "tool.edit_video" not in both
    assert "tool.remove_audio" in both and "tool.normalize_audio" in both  # batch tools
    assert tools("sample-audio.wav") == ["tool.edit_audio", "tool.metadata"]
    assert tools("sample.zip") == ["tool.extract_archive"]
    assert tools("sample.svg") == ["tool.edit_image"]
    svg = Selection.from_paths([copy_fixture("sample.svg", tmp_path)])
    shown = REGISTRY.query(svg, Kind.TOOL, available=AVAILABLE)
    assert bool(shown) == AVAILABLE.is_available("rsvg-convert")  # SVG is rendered to edit it


# --- Compress ---------------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["jpg", "webp", "png"])
def test_compress_image_by_quality(tmp_path, fmt):
    src = photo(tmp_path / f"p.{fmt}", **({"quality": 95} if fmt != "png" else {}))
    (out,) = ok(run_op("tool.compress", [src], FakeHost({"mode": "quality", "level": "low"})))
    assert out.name == f"p-compressed.{fmt}"
    assert out.stat().st_size < src.stat().st_size
    assert filetype.detect(out).id == fmt
    with Image.open(out) as im:
        assert im.size == (1200, 900)


def test_compress_image_keeps_exif_and_reaches_a_size(tmp_path):
    exif = Image.Exif()
    exif[0x010F] = "QuinceCam"  # Make
    src = photo(tmp_path / "p.jpg", quality=97, exif=exif.tobytes())
    target_kb = src.stat().st_size // 1024 // 3
    host = FakeHost({"mode": "size", "size": target_kb, "unit": "KB"})
    snap = run_op("tool.compress", [src], host)
    (out,) = ok(snap)
    assert out.stat().st_size <= target_kb * 1024
    with Image.open(out) as im:
        assert im.getexif().get(0x010F) == "QuinceCam"


def test_compress_image_percentage_and_resize_note(tmp_path):
    src = photo(tmp_path / "p.jpg", quality=80)
    snap = run_op("tool.compress", [src], FakeHost({"mode": "percent", "percent": 5}))
    (out,) = ok(snap)
    assert out.stat().st_size <= src.stat().st_size * 0.05
    assert any("Resized to" in n for n in snap.notes)


def test_compress_image_unreachable_and_already_small(tmp_path):
    src = photo(tmp_path / "p.png")
    host = FakeHost({"mode": "size", "size": 0.1, "unit": "KB"})
    error = failed(run_op("tool.compress", [src], host))
    assert "Cannot reach" in error and "the smallest Quince can make is" in error
    small = tmp_path / "tiny.png"
    Image.new("RGB", (16, 16), "white").save(small, optimize=True)
    error = failed(run_op("tool.compress", [small], FakeHost({"mode": "quality", "level": "high"})))
    assert "Already well compressed" in error
    assert not [p for p in tmp_path.iterdir() if "compressed" in p.name or ".quince" in p.name]


@needs("gs")
def test_compress_pdf(tmp_path):
    import pymupdf

    src = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    picture = photo(tmp_path / "pic.png", size=(1600, 1200))
    for _ in range(2):
        page = doc.new_page()
        page.insert_text((72, 72), "Quince PDF compression")
        page.insert_image(pymupdf.Rect(72, 100, 520, 436), filename=str(picture))
    doc.save(src)
    doc.close()
    (out,) = ok(run_op("tool.compress", [src], FakeHost({"mode": "quality", "level": "medium"})))
    assert out.stat().st_size < src.stat().st_size
    with pymupdf.open(out) as result:
        assert result.page_count == 2
        assert "Quince PDF compression" in result[0].get_text()
    error = failed(run_op("tool.compress", [src], FakeHost({"mode": "size", "size": 0.1,
                                                            "unit": "KB"})))  # fmt: skip
    assert "Cannot reach" in error


@needs("ffmpeg", "ffprobe")
def test_compress_audio_to_a_size(tmp_path):
    src = tmp_path / "song.mp3"
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=20", "-b:a", "320k",
           "-metadata", "title=Ayva", src)  # fmt: skip
    size_kb = src.stat().st_size // 1024 // 3
    snap = run_op("tool.compress", [src], FakeHost({"mode": "size", "size": size_kb,
                                                    "unit": "KB"}))  # fmt: skip
    (out,) = ok(snap)
    assert out.stat().st_size <= size_kb * 1024
    assert metadata.read(out, "mp3").rows  # tags kept
    (low,) = ok(run_op("tool.compress", [src], FakeHost({"mode": "quality", "level": "low"})))
    assert low.stat().st_size < src.stat().st_size


@needs("ffmpeg", "ffprobe")
def test_compress_video_quality_and_size(tmp_path):
    src = tmp_path / "clip.avi"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=4",
           "-f", "lavfi", "-i", "sine=duration=4", "-c:v", "mpeg4", "-q:v", "1",
           "-c:a", "libmp3lame", "-b:a", "192k", src)  # fmt: skip
    snap = run_op("tool.compress", [src], FakeHost({"mode": "quality", "level": "medium"}))
    (out,) = ok(snap)
    assert out.suffix == ".mp4" and filetype.detect(out).id == "mp4"
    assert any("Saved as MP4" in n for n in snap.notes)
    target = src.stat().st_size // 4
    snap = run_op("tool.compress", [src], FakeHost({"mode": "percent", "percent": 25}))
    (sized,) = ok(snap)
    assert sized.stat().st_size <= target


# --- Metadata ---------------------------------------------------------------------------------


def _gps_jpeg(path: Path) -> Path:
    exif = Image.Exif()
    exif[0x010F] = "QuinceCam"
    exif[0x0112] = 6  # rotated: must survive
    gps = exif.get_ifd(0x8825)
    gps.update({1: "N", 2: (41.0, 0.0, 30.0), 3: "E", 4: (29.0, 0.0, 0.0)})
    Image.new("RGB", (40, 30), "orange").save(path, exif=exif.tobytes(), comment=b"secret")
    return path


def test_metadata_report_and_strip_jpeg(tmp_path):
    src = _gps_jpeg(tmp_path / "trip.jpg")
    host = FakeHost(report_answer="strip")
    (out,) = ok(run_op("tool.metadata", [src], host))
    [report] = host.reports
    rows = dict(report.sections[1].rows)
    assert rows["Make"] == "QuinceCam"
    assert rows["GPS position"] == "41.008333, 29.000000"
    assert out.name == "trip-no-metadata.jpg"
    with Image.open(out) as im:
        assert dict(im.getexif()) == {0x0112: 6}
        assert "comment" not in im.info
    # the pixels are the same bytes (no re-encoding)
    assert out.read_bytes()[-200:] == src.read_bytes()[-200:]
    assert metadata.read(out, "jpg").rows == [("Orientation", "6")]


def test_metadata_strip_png_and_webp(tmp_path):
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "Arda")
    png = tmp_path / "a.png"
    Image.new("RGBA", (20, 10), (1, 2, 3, 128)).save(png, pnginfo=info)
    exif = Image.Exif()
    exif[0x010F] = "QuinceCam"
    webp = tmp_path / "b.webp"
    Image.new("RGB", (20, 10), "blue").save(webp, exif=exif.tobytes(), lossless=True)
    outs = ok(run_op("tool.metadata", [png, webp], FakeHost(report_answer="strip")))
    for out in outs:
        fmt = filetype.detect(out).id
        assert metadata.read(out, fmt).rows == []
        with (
            Image.open(out) as im,
            Image.open(tmp_path / out.name.replace("-no-metadata", "")) as orig,
        ):
            assert im.tobytes() == orig.tobytes()


def test_metadata_without_any_offers_no_removal(tmp_path):
    src = tmp_path / "plain.png"
    Image.new("RGB", (4, 4)).save(src)
    host = FakeHost(report_answer="strip")
    assert run_op("tool.metadata", [src], host) is None
    assert host.reports[0].actions == () and "No metadata" in host.reports[0].note


def test_metadata_pdf(tmp_path):
    import pymupdf

    src = tmp_path / "doc.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "hello")
    doc.set_metadata({"author": "Arda Elibol", "title": "Gizli"})
    doc.save(src)
    doc.close()
    host = FakeHost(report_answer="strip")
    (out,) = ok(run_op("tool.metadata", [src], host))
    assert ("Author", "Arda Elibol") in host.reports[0].sections[1].rows
    assert metadata.read(out, "pdf").rows == []
    with pymupdf.open(out) as result:
        assert "hello" in result[0].get_text()


@needs("ffmpeg", "ffprobe")
def test_metadata_media(tmp_path):
    src = tmp_path / "v.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=64x48:duration=1", "-f", "lavfi", "-i",
           "sine=duration=1", "-c:v", "libx264", "-c:a", "aac", "-metadata", "artist=Arda",
           "-metadata:s:a:0", "title=Voice", src)  # fmt: skip
    host = FakeHost(report_answer="strip")
    (out,) = ok(run_op("tool.metadata", [src], host))
    rows = dict(host.reports[0].sections[1].rows)
    assert rows["artist"] == "Arda"
    remaining = dict(metadata.read(out, "mp4").rows)
    assert "artist" not in remaining and not any("Voice" in v for v in remaining.values())


# --- audio and video tools --------------------------------------------------------------------


@needs("ffmpeg", "ffprobe")
def test_remove_audio(tmp_path):
    src = copy_fixture("sample-video.mkv", tmp_path)
    other = Path(shutil.copy(src, tmp_path / "other.mkv"))
    outs = ok(run_op("tool.remove_audio", [src, other]))
    assert sorted(o.name for o in outs) == ["other-no-audio.mkv", "sample-video-no-audio.mkv"]
    from quince.backends import ffmpeg as ff

    for out in outs:
        info = ff.probe(out)
        assert info.video is not None and not info.audio
    assert "no sound" in failed(run_op("tool.remove_audio", list(outs)))


@needs("ffmpeg", "ffprobe")
def test_normalize_audio(tmp_path):
    from quince.backends import ffmpeg as ff
    from quince.backends import mediatools

    src = tmp_path / "quiet.flac"
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=300:duration=6,volume=0.05", "-ar", "44100", src)
    other = Path(shutil.copy(src, tmp_path / "quiet 2.flac"))
    snap = run_op("tool.normalize_audio", [src, other])
    out = next(o for o in ok(snap) if o.name == "quiet-normalized.flac")
    assert any("→ -16 LUFS" in n for n in snap.notes)
    info = ff.probe(out)
    assert info.audio[0].codec == "flac" and info.audio[0].sample_rate == 44100
    loud = mediatools.measure(None, out, 0, info.duration, (0, 1))
    assert abs(loud.integrated - mediatools.TARGET_LUFS) < 1.0
    silent = tmp_path / "silent.wav"
    ffmpeg("-f", "lavfi", "-i", "anullsrc=duration=2", silent)
    silent2 = Path(shutil.copy(silent, tmp_path / "silent 2.wav"))
    assert "silent" in failed(run_op("tool.normalize_audio", [silent, silent2]))
    one = Selection.from_paths([src])
    assert REGISTRY.get("tool.normalize_audio") not in REGISTRY.query(one, Kind.TOOL)


@needs("ffmpeg", "ffprobe")
def test_join_videos_copy_and_reencode(tmp_path):
    from quince.backends import ffmpeg as ff

    a, b = tmp_path / "part 10.mp4", tmp_path / "part 2.mp4"
    for path, color in ((a, "red"), (b, "blue")):
        ffmpeg("-f", "lavfi", "-i", f"color={color}:size=160x120:rate=25:duration=1",
               "-f", "lavfi", "-i", "sine=duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-shortest", path)  # fmt: skip
    host = FakeHost()
    snap = run_op("tool.join_videos", [a, b], host)
    (out,) = ok(snap)
    assert host.offered_order == [b, a]  # natural order: 2 before 10
    assert out.name == "part 2-joined.mp4"
    assert "without re-encoding" in snap.notes[0]
    assert abs(ff.probe(out).duration - 2.0) < 0.2

    odd = tmp_path / "odd.mkv"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=1", "-c:v", "libx264", odd
    )  # fmt: skip  (no sound)
    snap = run_op("tool.join_videos", [a, odd], FakeHost())
    (out,) = ok(snap)
    info = ff.probe(out)
    # natural order puts odd.mkv first: its size wins
    assert out.suffix == ".mp4" and (info.video.width, info.video.height) == (320, 180)
    assert info.audio and abs(info.duration - 2.0) < 0.3


# --- PDF tools --------------------------------------------------------------------------------


def test_merge_pdfs_keeps_pages_links_and_bookmarks(tmp_path):
    import pymupdf

    paths = []
    for name, pages in (("b.pdf", 2), ("a.pdf", 1)):
        doc = pymupdf.open()
        for n in range(pages):
            page = doc.new_page()
            page.insert_text((72, 72), f"{name} page {n + 1}")
        doc[0].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(70, 60, 200, 80),
                            "uri": "https://example.org"})  # fmt: skip
        doc.set_toc([[1, f"Chapter of {name}", 1]])
        path = tmp_path / name
        doc.save(path)
        doc.close()
        paths.append(path)
    host = FakeHost(order=lambda ps: list(reversed(ps)))  # user puts b first
    (out,) = ok(run_op("tool.merge_pdfs", paths, host))
    assert host.offered_order == [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    assert out.name == "b-merged.pdf"
    with pymupdf.open(out) as merged:
        assert merged.page_count == 3
        assert "b.pdf page 1" in merged[0].get_text() and "a.pdf" in merged[2].get_text()
        assert merged[2].get_links()[0]["uri"] == "https://example.org"
        assert merged.get_toc() == [[1, "b", 1], [2, "Chapter of b.pdf", 1], [1, "a", 3],
                                    [2, "Chapter of a.pdf", 3]]  # fmt: skip


def test_merge_refuses_locked_pdf(tmp_path):
    import pymupdf

    paths = []
    for name in ("x.pdf", "y.pdf"):
        doc = pymupdf.open()
        doc.new_page()
        options = (
            {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": "pw"} if name == "y.pdf" else {}
        )
        doc.save(tmp_path / name, **options)
        doc.close()
        paths.append(tmp_path / name)
    assert "y.pdf: The PDF is password-protected" in failed(run_op("tool.merge_pdfs", paths))


def test_images_to_pdf(tmp_path):
    import pymupdf

    jpg = photo(tmp_path / "1.jpg", size=(200, 100), quality=90, dpi=(100, 100))
    png = tmp_path / "2.png"
    Image.new("RGBA", (50, 80), (0, 0, 255, 128)).save(png)
    (out,) = ok(run_op("tool.images_to_pdf", [png, jpg]))
    assert out.name == "1-combined.pdf"
    with pymupdf.open(out) as doc:
        assert doc.page_count == 2
        assert round(doc[0].rect.width) == 144  # 200 px at 100 dpi = 2 in
        assert (
            doc[0].get_images()[0][-1] == "DCTDecode"
            or doc.extract_image(doc[0].get_images()[0][0])["ext"] == "jpeg"
        )


# --- archives ---------------------------------------------------------------------------------


def test_extract_single_folder_lands_next_to_archive(tmp_path):
    src = tmp_path / "project.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("project/readme.txt", "hi")
        z.writestr("project/src/main.py", "print(1)")
    (tmp_path / "project").mkdir()  # taken: the result gets the next free name
    (out,) = ok(run_op("tool.extract_archive", [src]))
    assert out == tmp_path / "project (1)"
    assert (out / "src" / "main.py").read_text() == "print(1)"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["project", "project (1)", "project.zip"]


def test_extract_many_items_into_a_folder_and_refuse_unsafe(tmp_path):
    src = tmp_path / "bundle.tar.gz"
    with tarfile.open(src, "w:gz") as t:
        for name in ("a.txt", "b.txt"):
            data = name.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    (out,) = ok(run_op("tool.extract_archive", [src]))
    assert out == tmp_path / "bundle" and sorted(p.name for p in out.iterdir()) == [
        "a.txt",
        "b.txt",
    ]
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../escape.txt", "x")
    assert "unsafe path" in failed(run_op("tool.extract_archive", [evil]))
    assert not (tmp_path / "escape.txt").exists() and not (tmp_path / "evil").exists()


@pytest.mark.parametrize("fmt", ["zip", "targz"])
def test_create_archive_from_files_and_folders(tmp_path, fmt):
    folder = tmp_path / "Belgeler"
    (folder / "alt").mkdir(parents=True)
    (folder / "alt" / "not.txt").write_text("içerik")
    note = tmp_path / "liste.md"
    note.write_text("# Liste")
    host = FakeHost({"format": fmt, "name": "Yedek"})
    (out,) = ok(run_op("action.create_archive", [folder, note], host))
    if fmt == "zip":
        assert out.name == "Yedek.zip"
        with zipfile.ZipFile(out) as z:
            names = set(z.namelist())
            assert {"Belgeler/alt/not.txt", "liste.md"} <= names
            assert z.read("Belgeler/alt/not.txt").decode() == "içerik"
    else:
        assert out.name == "Yedek.tar.gz"
        with tarfile.open(out) as t:
            assert {"Belgeler/alt/not.txt", "liste.md"} <= set(t.getnames())


@needs("7z")
def test_create_and_extract_7z(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "x.txt").write_text("x")
    (out,) = ok(run_op("action.create_archive", [folder], FakeHost({"format": "7z"})))
    assert out.name == "data.7z"
    shutil.rmtree(folder)
    (extracted,) = ok(run_op("tool.extract_archive", [out]))
    assert (extracted / "x.txt").read_text() == "x"


# --- QR codes ---------------------------------------------------------------------------------


@needs("zbarimg")
def test_read_qr_code(tmp_path, make_fixtures):
    src = copy_fixture("sample-qr.png", tmp_path)
    host = FakeHost()
    assert run_op("tool.read_qr", [src], host) is None  # nothing is written
    [report] = host.reports
    assert report.sections[0].rows == (("QR code", make_fixtures.QR_TEXT),)
    assert report.copy_all == make_fixtures.QR_TEXT
    blank = tmp_path / "blank.png"
    Image.new("RGB", (100, 100), "white").save(blank)
    run_op("tool.read_qr", [blank], host)
    assert host.reports[1].sections[0].rows == () and "No QR code" in host.reports[1].note
