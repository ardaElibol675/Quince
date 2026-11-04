"""Every PDF, subtitle and archive route, plus malicious archives."""

import io
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

import pytest

from quince.backends import office
from quince.core import filetype, formats
from quince.core.deps import Availability
from quince.core.jobs import JobQueue, JobState
from quince.core.registry import discover
from quince.core.selection import Selection
from tests.unit.test_jobs import wait_finished

pytestmark = pytest.mark.integration

GENERATED = Path(__file__).resolve().parent.parent / "fixtures" / "generated"
REGISTRY = discover()
AVAILABLE = Availability.probe()
ROUTES = [r for r in REGISTRY.routes if r.backend in ("pdf", "subtitles", "archive")]


def convert(paths, target):
    op = REGISTRY.get(f"convert.{formats.get(target).category.value}.{target}")
    queue = JobQueue(workers=1)
    try:
        return wait_finished(
            queue, queue.submit(op.run, Selection.from_paths(paths), title="t"), 300
        )
    finally:
        queue.shutdown()


def only(snap) -> Path:
    assert snap.state is JobState.SUCCEEDED, snap.error
    (out,) = snap.outputs
    return out


def members(path: Path, fmt: str) -> list[str]:
    if fmt == "zip":
        return sorted(n.rstrip("/") for n in zipfile.ZipFile(path).namelist())
    if fmt in ("tar", "targz"):
        return sorted(tarfile.open(path).getnames())
    out = subprocess.run(["7z", "l", "-slt", "-ba", str(path)], capture_output=True, text=True)
    return sorted(line[7:] for line in out.stdout.splitlines() if line.startswith("Path = "))


@pytest.mark.parametrize("route", ROUTES, ids=[f"{r.source}-{r.target}" for r in ROUTES])
def test_route(route, tmp_path):
    if missing := AVAILABLE.missing(route.all_requires):
        pytest.skip(f"needs {', '.join(missing)}")
    name = (
        "golden/rich.pdf"
        if route.source == "pdf"
        else f"sample.{formats.get(route.source).extension}"
    )
    fixture = GENERATED / name
    if not fixture.exists():
        pytest.skip("run scripts/make_fixtures.py first")
    src = Path(shutil.copy(fixture, tmp_path))
    original = src.read_bytes()
    out = only(convert([src], route.target))
    assert src.read_bytes() == original
    files = sorted(p for p in out.iterdir()) if out.is_dir() else [out]
    for path in files:
        assert filetype.detect(path).id == route.target, path
    if route.backend == "archive":
        assert members(out, route.target) == ["folder", "folder/inner.txt", "hello.txt"]


# --- PDF ------------------------------------------------------------------------------------


def _pdf_text(path: Path) -> str:
    import pymupdf

    with pymupdf.open(path) as doc:
        return "".join(page.get_text() for page in doc)


def test_pdf_pages_as_images(tmp_path):
    src = Path(shutil.copy(GENERATED / "golden/rich.pdf", tmp_path))
    out = only(convert([src], "png"))
    assert sorted(p.name for p in out.iterdir()) == ["rich-01.png", "rich-02.png"]
    from PIL import Image

    with Image.open(out / "rich-01.png") as im:
        assert im.width == pytest.approx(595 / 72 * 300, abs=3)  # A4 at 300 DPI


def test_pdf_to_text_without_headers(tmp_path):
    src = Path(shutil.copy(GENERATED / "golden/rich.pdf", tmp_path))
    text = only(convert([src], "txt")).read_text(encoding="utf-8")
    assert "Proje Raporu: Çağrı Merkezi" in text and "ğüşıöç ĞÜŞİÖÇ" in text
    assert "Gizli" not in text and "Sayfa 1 / 2" not in text


def test_pdf_to_docx_is_editable_text(tmp_path):
    src = Path(shutil.copy(GENERATED / "golden/rich.pdf", tmp_path))
    out = only(convert([src], "docx"))
    md = subprocess.run(
        ["pandoc", "-f", "docx", "-t", "gfm", str(out)], capture_output=True, text=True
    ).stdout
    assert "# Proje Raporu: Çağrı Merkezi" in md
    assert "| İstanbul" in md and "Ölçümleri topla" in md


@pytest.mark.skipif(AVAILABLE.missing(("soffice",)), reason="needs soffice")
def test_pdf_to_pptx_has_a_slide_per_page(tmp_path):
    src = Path(shutil.copy(GENERATED / "golden/rich.pdf", tmp_path))
    out = only(convert([src], "pptx"))
    slides = [n for n in zipfile.ZipFile(out).namelist() if n.startswith("ppt/slides/slide")]
    assert len(slides) == 2
    with tempfile.TemporaryDirectory() as tmp:
        (pdf,) = office.convert_file(None, out, "pptx", "pdf", Path(tmp))
        import pymupdf

        with pymupdf.open(pdf) as doc:
            assert doc.page_count == 2 and doc[0].get_images()


@pytest.mark.skipif(AVAILABLE.missing(("soffice",)), reason="needs soffice")
def test_pdf_tables_to_xlsx(tmp_path):
    src = Path(shutil.copy(GENERATED / "golden/rich.pdf", tmp_path))
    snap = convert([src], "xlsx")
    assert snap.notes == ("1 table found",)
    with tempfile.TemporaryDirectory() as tmp:
        (csv,) = office.convert_file(None, only(snap), "xlsx", "csv", Path(tmp))
        assert csv.read_text(encoding="utf-8").splitlines()[:2] == [
            "Şehir,Çağrı,Bekleme (sn)",
            "İstanbul,1250,42",
        ]
    plain = Path(shutil.copy(GENERATED / "sample.pdf", tmp_path))
    snap = convert([plain], "xlsx")
    assert snap.error == (
        "sample.pdf: No tables found in this PDF (only tables with ruled lines are detected)"
    )


def test_locked_pdf_is_explained(tmp_path):
    import pymupdf

    locked = tmp_path / "locked.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "secret")
    doc.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    for target in ("md", "png"):
        assert convert([locked], target).error == "locked.pdf: The PDF is password-protected"


# --- subtitles ------------------------------------------------------------------------------


def test_turkish_subtitles_in_windows_1254(tmp_path):
    src = tmp_path / "film.srt"
    src.write_bytes(
        "1\r\n00:00:01,000 --> 00:00:02,500\r\nGüle güle, <i>ağabey</i>!\r\n\r\n"
        "2\r\n00:00:03,000 --> 00:00:04,000\r\nIşık söndü.\r\n".encode("cp1254")
    )
    snap = convert([src], "vtt")
    vtt = only(snap).read_text(encoding="utf-8")
    assert vtt.startswith("WEBVTT") and "00:00:01.000 --> 00:00:02.500" in vtt
    assert "ağabey" in vtt and "Işık söndü." in vtt
    assert "Read as Windows-1254 (Turkish) text" in snap.notes
    txt = only(convert([src], "txt")).read_text(encoding="utf-8")
    assert txt == "Güle güle, ağabey!\n\nIşık söndü.\n"


def test_empty_subtitles_are_explained(tmp_path):
    src = tmp_path / "empty.srt"
    src.write_text("\n", encoding="utf-8")
    assert convert([src], "vtt").error == "empty.srt: The file has no subtitles"


# --- archives -------------------------------------------------------------------------------


def _zip(path: Path, entries: dict) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return path


def _tar(path: Path, add) -> Path:
    with tarfile.open(path, "w") as t:
        add(t)
    return path


def _file(t: tarfile.TarFile, name: str, data: bytes = b"x", **kw) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    for k, v in kw.items():
        setattr(info, k, v)
    t.addfile(info, io.BytesIO(data))


def _link(t: tarfile.TarFile, name: str, target: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    t.addfile(info)


MALICIOUS = {
    "zip-dotdot": lambda d: _zip(d / "evil.zip", {"ok.txt": "x", "../evil.txt": "x"}),
    "zip-absolute": lambda d: _zip(d / "evil.zip", {f"{d.parent}/abs-evil.txt": "x"}),
    "tar-dotdot": lambda d: _tar(d / "evil.tar", lambda t: _file(t, "a/../../evil.txt")),
    "tar-absolute": lambda d: _tar(d / "evil.tar", lambda t: _file(t, f"{d.parent}/abs-evil.txt")),
    "tar-link-out": lambda d: _tar(d / "evil.tar", lambda t: _link(t, "passwd", "/etc/passwd")),
    "tar-link-up": lambda d: _tar(d / "evil.tar", lambda t: _link(t, "up", "../../outside")),
    "tar-device": lambda d: _tar(
        d / "evil.tar", lambda t: _file(t, "dev", b"", type=tarfile.CHRTYPE, devmajor=1)
    ),
}


@pytest.mark.parametrize("case", MALICIOUS)
def test_malicious_archives_are_refused(case, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    src = MALICIOUS[case](work)
    before = sorted(os.listdir(work))
    target = "tar" if src.suffix == ".zip" else "zip"
    snap = convert([src], target)
    assert snap.state is JobState.FAILED
    assert "unsafe" in snap.error or "outside" in snap.error, snap.error
    assert sorted(os.listdir(work)) == before  # no output, nothing extracted next to it
    # Absolute names point into tmp_path, so even a broken check never writes elsewhere.
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "abs-evil.txt").exists()


def test_links_and_modes_inside_an_archive_are_kept(tmp_path):
    def add(t):
        _file(t, "dir/run.sh", b"#!/bin/sh\n", mode=0o755, mtime=1_700_000_000)
        _link(t, "dir/latest", "run.sh")

    src = _tar(tmp_path / "tools.tar", add)
    zipped = only(convert([src], "zip"))
    with zipfile.ZipFile(zipped) as z:
        info = {i.filename: i for i in z.infolist()}
        assert stat.S_ISLNK(info["dir/latest"].external_attr >> 16)
        assert z.read("dir/latest") == b"run.sh"
        assert stat.S_IMODE(info["dir/run.sh"].external_attr >> 16) == 0o755
    back = only(convert([zipped], "targz"))
    with tarfile.open(back) as t:
        assert t.getmember("dir/latest").issym() and t.getmember("dir/latest").linkname == "run.sh"


def test_password_protected_zip_is_explained(tmp_path):
    src = _zip(tmp_path / "secret.zip", {"a.txt": "x"})
    data = bytearray(src.read_bytes())
    # Set the "encrypted" flag in both the local and the central header.
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        pos = data.find(signature)
        data[pos + offset] |= 0x1
    src.write_bytes(bytes(data))
    assert (
        convert([src], "tar").error == "secret.zip: Password-protected archives are not supported"
    )


def test_broken_archive_is_explained(tmp_path):
    src = tmp_path / "broken.zip"
    src.write_bytes(b"PK\x03\x04 broken")
    snap = convert([src], "tar")
    assert snap.state is JobState.FAILED and snap.error.startswith("broken.zip: ")


def test_old_timestamps_fit_into_zip(tmp_path):
    src = _tar(tmp_path / "old.tar", lambda t: _file(t, "old.txt", b"1970", mtime=0))
    with zipfile.ZipFile(only(convert([src], "zip"))) as z:
        assert z.getinfo("old.txt").date_time[0] == 1980
