"""The imaging backend's rules, on small synthetic images."""

import pymupdf
import pytest
from PIL import Image, ImageCms

from quince.backends import imaging
from quince.core.jobs import JobQueue, JobState
from quince.core.registry import discover
from quince.core.routes import Route
from quince.core.selection import Selection
from quince.operations.convert import make_run
from tests.unit.test_jobs import wait_finished

REGISTRY = discover()
EXIF_MAKE, EXIF_GPS = 0x010F, 0x8825


def convert(paths, target, category="image"):
    op = REGISTRY.get(f"convert.{category}.{target}")
    queue = JobQueue(workers=1)
    try:
        return wait_finished(queue, queue.submit(op.run, Selection.from_paths(paths), title="t"))
    finally:
        queue.shutdown()


def only(snap):
    assert snap.state is JobState.SUCCEEDED, snap.error
    (out,) = snap.outputs
    return out


def oriented_jpeg(path, orientation=6, size=(40, 20)):
    im = Image.new("RGB", size, (255, 0, 0))
    im.paste((0, 0, 255), (0, 0, 10, 10))  # a blue top-left corner
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[EXIF_MAKE] = "QuinceCam"
    exif[EXIF_GPS] = {1: "N", 2: (41.0, 0.0, 0.0)}
    im.save(path, exif=exif.tobytes(), quality=95)
    return path


def test_exif_orientation_is_applied_and_metadata_kept(tmp_path):
    src = oriented_jpeg(tmp_path / "photo.jpg")
    out = only(convert([src], "png"))
    with Image.open(out) as im:
        assert im.size == (20, 40)  # rotated 90°
        assert im.convert("RGB").getpixel((15, 2))[2] > 200  # blue corner is now top-right
        exif = im.getexif()
        assert 0x0112 not in exif  # no double rotation in viewers
        assert exif[EXIF_MAKE] == "QuinceCam"
        assert exif.get_ifd(EXIF_GPS)  # GPS kept (user decision 2026-11-04)
    webp = only(convert([src], "webp"))
    with Image.open(webp) as im:
        assert im.getexif()[EXIF_MAKE] == "QuinceCam"


def test_icc_profile_is_kept_or_applied(tmp_path):
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")).tobytes()
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    src = tmp_path / "p.png"
    Image.new("RGB", (8, 8), (10, 200, 30)).save(src, icc_profile=srgb)
    with Image.open(only(convert([src], "jpg"))) as im:
        assert im.info.get("icc_profile") == srgb
    with Image.open(only(convert([src], "gif"))) as im:
        assert "icc_profile" not in im.info  # GIF has no profile: pixels made sRGB
    bad = tmp_path / "bad.png"
    Image.new("RGB", (8, 8)).save(bad, icc_profile=profile[:40])  # broken profile
    assert convert([bad], "gif").state is JobState.SUCCEEDED


def test_cmyk_becomes_rgb_where_needed(tmp_path):
    src = tmp_path / "print.jpg"
    Image.new("CMYK", (8, 8), (0, 255, 255, 0)).save(src)
    with Image.open(only(convert([src], "png"))) as im:
        assert im.mode == "RGB"
        r, g, b = im.getpixel((4, 4))
        assert r > 200 and g < 60 and b < 60  # cyan-free magenta+yellow = red
    with Image.open(only(convert([src], "tiff"))) as im:
        assert im.mode == "CMYK"


def test_16_bit_is_scaled_not_clipped(tmp_path):
    src = tmp_path / "deep.png"
    im = Image.new("I;16", (4, 1))
    for x, value in enumerate((0, 0x4000, 0x8000, 0xFFFF)):
        im.putpixel((x, 0), value)
    im.save(src)
    with Image.open(only(convert([src], "jpg"))) as out:
        values = [out.getpixel((x, 0)) for x in range(4)]
    values = [v if isinstance(v, int) else v[0] for v in values]
    assert values[0] < 5 and 55 < values[1] < 75 and 120 < values[2] < 136 and values[3] > 250
    with Image.open(only(convert([src], "tiff"))) as out:
        assert out.mode.startswith("I;16")  # TIFF keeps 16 bits


def test_palette_and_grayscale_sources(tmp_path):
    pal = tmp_path / "pal.png"
    palette = Image.new("P", (8, 8), 0)
    palette.putpalette([255, 0, 0, 0, 255, 0])
    palette.putpixel((7, 7), 1)
    palette.save(pal, transparency=0)
    with Image.open(pal) as check:
        assert check.convert("RGBA").getpixel((0, 0))[3] == 0  # the source really is clear
    with Image.open(only(convert([pal], "webp"))) as im:
        assert im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0
    gray = tmp_path / "gray.png"
    Image.new("L", (8, 8), 77).save(gray)
    with Image.open(only(convert([gray], "jpg"))) as im:
        assert im.mode == "L"
    with Image.open(only(convert([gray], "webp"))) as im:
        assert im.mode == "RGB"
    opaque = tmp_path / "opaque.png"
    Image.new("RGBA", (8, 8), (1, 2, 3, 255)).save(opaque)
    snap = convert([opaque], "jpg")
    assert not snap.notes  # fully opaque alpha is simply dropped


def test_multi_page_tiff(tmp_path):
    src = tmp_path / "scan.tiff"
    pages = [Image.new("RGB", (30, 40), c) for c in ("red", "green")]
    pages[0].save(src, save_all=True, append_images=pages[1:], dpi=(150, 150))
    pdf = only(convert([src], "pdf", "pdf"))
    with pymupdf.open(pdf) as doc:
        assert doc.page_count == 2
        assert doc[0].rect.width == pytest.approx(30 * 72 / 150)
    snap = convert([src], "jpg")
    assert snap.notes == ("Only the first page was kept",)


def test_jpeg_goes_into_pdf_unchanged(tmp_path):
    src = tmp_path / "photo.jpg"
    Image.new("RGB", (60, 30), (90, 20, 200)).save(src, quality=80, dpi=(300, 300))
    pdf = only(convert([src], "pdf", "pdf"))
    with pymupdf.open(pdf) as doc:
        xref = doc[0].get_images()[0][0]
        image = doc.extract_image(xref)
        assert image["ext"] == "jpeg" and image["image"] == src.read_bytes()
        assert doc[0].rect.width == pytest.approx(60 * 72 / 300)
    # A rotated JPEG cannot be embedded as it is: it is re-encoded upright.
    rotated = oriented_jpeg(tmp_path / "rot.jpg")
    with pymupdf.open(only(convert([rotated], "pdf", "pdf"))) as doc:
        rect = doc[0].rect
        assert rect.height > rect.width


def test_transparent_png_into_pdf_keeps_alpha(tmp_path):
    src = tmp_path / "logo.png"
    im = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    im.paste((255, 0, 0, 255), (5, 5, 15, 15))
    im.save(src)
    with pymupdf.open(only(convert([src], "pdf", "pdf"))) as doc:
        smask = doc[0].get_images()[0][1]
        assert smask > 0  # an alpha mask, not a white or black box


def test_size_limit_is_explained(tmp_path):
    src = tmp_path / "wide.png"
    Image.new("L", (16384, 1)).save(src)
    snap = convert([src], "webp")
    assert snap.state is JobState.FAILED
    assert snap.error == (
        "wide.png: WebP images can be at most 16383 pixels wide and high; this one is 16384 x 1"
    )
    assert not list(tmp_path.glob("*.webp"))


def test_unreadable_files_are_explained(tmp_path):
    src = tmp_path / "broken.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"garbage" * 10)
    snap = convert([src], "jpg")
    assert snap.state is JobState.FAILED
    assert snap.error.startswith("broken.png: ")
    assert "broken" in snap.error


def test_one_bad_file_does_not_stop_the_others(tmp_path):
    good = tmp_path / "good.png"
    Image.new("RGB", (4, 4)).save(good)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 40)
    snap = convert([good, bad], "jpg")
    assert snap.state is JobState.SUCCEEDED and snap.partial
    assert snap.outputs == (tmp_path / "good.jpg",)
    assert len(snap.failures) == 1 and snap.failures[0].startswith("bad.png: ")
    assert not (tmp_path / "bad.jpg").exists()
    both_bad = convert([bad, bad.rename(tmp_path / "bad2.png")], "jpg")
    assert both_bad.state is JobState.FAILED


def test_files_already_in_the_target_format_are_skipped(tmp_path):
    jpg = tmp_path / "a.jpg"
    png = tmp_path / "b.png"
    Image.new("RGB", (4, 4)).save(jpg)
    Image.new("RGB", (4, 4)).save(png)
    snap = convert([jpg, png], "jpg")
    assert snap.outputs == (tmp_path / "b.jpg",)
    assert snap.notes == ("1 file was already JPG",)


def test_progress_messages(tmp_path):
    files = []
    for name in ("a.png", "b.png"):
        Image.new("RGB", (4, 4)).save(tmp_path / name)
        files.append(tmp_path / name)
    events = []
    op = REGISTRY.get("convert.image.jpg")
    queue = JobQueue(workers=1, on_event=events.append)
    try:
        wait_finished(queue, queue.submit(op.run, Selection.from_paths(files), title="t"))
    finally:
        queue.shutdown()
    messages = [e.message for e in events if e.message]
    assert messages[:2] == ["Converting 1 of 2: a.png", "Converting 2 of 2: b.png"]


@pytest.fixture
def no_pdf_backend(monkeypatch):
    """Every backend exists now; pretend the "pdf" one does not, as in earlier phases."""
    from types import MappingProxyType

    import quince.operations.convert as convert_pkg

    modules = {k: v for k, v in convert_pkg.BACKEND_MODULES.items() if k != "pdf"}
    monkeypatch.setattr(convert_pkg, "BACKEND_MODULES", MappingProxyType(modules))


def test_make_run_without_implemented_backends(no_pdf_backend):
    assert make_run("txt", [Route("pdf", "txt", "pdf")]) is None
    run = make_run("txt", [Route("docx", "txt", "libreoffice"), Route("pdf", "txt", "pdf")])
    assert run is not None


def test_not_yet_available_routes_are_explained(no_pdf_backend, tmp_path):
    pdf = tmp_path / "letter.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    run = make_run("txt", [Route("docx", "txt", "libreoffice"), Route("pdf", "txt", "pdf")])
    queue = JobQueue(workers=1)
    try:
        snap = wait_finished(queue, queue.submit(run, Selection.from_paths([pdf]), title="t"))
    finally:
        queue.shutdown()
    assert snap.state is JobState.FAILED
    assert snap.error == "letter.pdf: PDF → TXT is not available yet"


def test_prepare_rules():
    target = imaging.TARGETS["jpg"]
    rgba = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
    prepared = imaging.prepare(rgba, target)
    assert prepared.flattened and prepared.image.getpixel((0, 0)) == (255, 255, 255)
    la = Image.new("LA", (2, 2), (0, 0))
    assert imaging.prepare(la, imaging.TARGETS["png"]).image.mode == "RGBA"
    one = Image.new("1", (2, 2))
    assert imaging.prepare(one, imaging.TARGETS["webp"]).image.mode == "RGB"
    ycbcr = Image.new("YCbCr", (2, 2))
    assert imaging.prepare(ycbcr, imaging.TARGETS["png"]).image.mode == "RGB"


def test_pdf_dpi():
    im = Image.new("RGB", (1, 1))
    assert imaging._pdf_dpi(im) == imaging.DEFAULT_PDF_DPI
    im.info["dpi"] = (300.0, 300.0)
    assert imaging._pdf_dpi(im) == 300.0
    im.info["dpi"] = (1.0, 1.0)  # nonsense values are ignored
    assert imaging._pdf_dpi(im) == imaging.DEFAULT_PDF_DPI
