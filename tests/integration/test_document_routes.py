"""Every document, slide and spreadsheet route with real LibreOffice and pandoc."""

import csv
import io
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from quince.backends import office, pandoc, process
from quince.core import filetype, formats
from quince.core.deps import Availability
from quince.core.jobs import JobQueue, JobState
from quince.core.registry import discover
from quince.core.selection import Selection
from tests.unit.test_jobs import wait_finished

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        Availability.probe().missing(("soffice", "pandoc")), reason="needs soffice and pandoc"
    ),
]

GENERATED = Path(__file__).resolve().parent.parent / "fixtures" / "generated"
REGISTRY = discover()
BACKENDS = ("libreoffice", "pandoc", "table")
ROUTES = [r for r in REGISTRY.routes if r.backend in BACKENDS]
# Words each fixture's content contains, to check that the text came through.
WORDS = {"document": "Quince", "slides": "Quince", "spreadsheet": "quince"}


def convert(paths, target, workers=1):
    op = REGISTRY.get(f"convert.{formats.get(target).category.value}.{target}")
    queue = JobQueue(workers=workers)
    try:
        job = queue.submit(op.run, Selection.from_paths(paths), title="t")
        return wait_finished(queue, job, timeout=300)
    finally:
        queue.shutdown()


def only(snap) -> Path:
    assert snap.state is JobState.SUCCEEDED, snap.error
    (out,) = snap.outputs
    return out


def text_of(path: Path, fmt: str) -> str:
    """Readable text of an output, whatever its format."""
    if fmt == "pdf":
        import pymupdf

        with pymupdf.open(path) as doc:
            return "".join(page.get_text() for page in doc)
    if fmt in ("docx", "odt", "epub", "html", "pptx", "odp"):
        if fmt in ("pptx", "odp"):
            with tempfile.TemporaryDirectory() as tmp:
                (pdf,) = office.convert_file(None, path, fmt, "pdf", Path(tmp))
                return text_of(pdf, "pdf")
        reader = {"html": "html"}.get(fmt, fmt)
        out = subprocess.run(
            ["pandoc", "-f", reader, "-t", "plain", str(path)], capture_output=True, text=True
        )
        return out.stdout
    if fmt in ("xlsx", "ods"):
        with tempfile.TemporaryDirectory() as tmp:
            files = office.convert_file(None, path, fmt, "csv", Path(tmp))
            return "\n".join(f.read_text(encoding="utf-8") for f in files)
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("route", ROUTES, ids=[f"{r.source}-{r.target}" for r in ROUTES])
def test_route(route, tmp_path):
    fixture = GENERATED / f"sample.{formats.get(route.source).extension}"
    if not fixture.exists():
        pytest.skip("run scripts/make_fixtures.py first")
    src = Path(shutil.copy(fixture, tmp_path))
    original = src.read_bytes()
    out = only(convert([src], route.target))
    assert src.read_bytes() == original
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".quince-tmp-")]

    files = sorted(p for p in out.rglob("*") if p.is_file()) if out.is_dir() else [out]
    if out.is_dir():
        assert out.name == "sample" and len(files) > 1
    words = WORDS[formats.get(route.source).category.value]
    texts = []
    for path in files:
        if route.target == "md":
            assert path.suffix == ".md" or path.parent.name == "media"
            continue
        assert filetype.detect(path).id == route.target, path
        if route.target in ("png", "jpg"):
            from PIL import Image

            with Image.open(path) as im:
                assert min(im.size) > 300  # 150 DPI pages and slides
        else:
            texts.append(text_of(path, route.target))
    if texts:  # every page or sheet together holds the source's text
        assert words.lower() in "\n".join(texts).lower()
    if route.target == "md":
        md = next(p for p in files if p.suffix == ".md")
        assert words.lower() in md.read_text(encoding="utf-8").lower()


def test_slides_become_numbered_images_in_a_folder(tmp_path):
    src = Path(shutil.copy(GENERATED / "sample.pptx", tmp_path))
    out = only(convert([src], "png"))
    assert out.is_dir() and sorted(p.name for p in out.iterdir()) == [
        "sample-01.png",
        "sample-02.png",
    ]


def test_every_sheet_becomes_a_csv(tmp_path):
    src = Path(shutil.copy(GENERATED / "sample.ods", tmp_path))
    snap = convert([src], "csv")
    out = only(snap)
    assert sorted(p.name for p in out.iterdir()) == ["sample-Fruits.csv", "sample-Totals.csv"]
    assert "2 sheets, one file each" in snap.notes
    single = tmp_path / "one.csv"
    single.write_text("a,b\n1,2\n")
    xlsx = only(convert([single], "xlsx"))
    back = only(convert([xlsx], "csv"))
    assert back.is_file() and back.read_text() == "a,b\n1,2\n"


def test_turkish_excel_csv(tmp_path):
    """Semicolons, decimal commas and Windows-1254, as Turkish Excel writes CSV."""
    src = tmp_path / "rapor.csv"
    src.write_bytes("Şehir;Oran\nİzmir;3,5\nMuğla;10\n".encode("cp1254"))
    snap = convert([src], "xlsx")
    assert "Read as Windows-1254 (Turkish) text" in snap.notes
    rows = list(csv.reader(io.StringIO(text_of(only(snap), "xlsx"))))
    assert rows == [["Şehir", "Oran"], ["İzmir", "3.5"], ["Muğla", "10"]]


def test_csv_to_tsv_keeps_every_value(tmp_path):
    src = tmp_path / "codes.csv"
    src.write_text('kod;ad\n007;"Ayşe; Fatma"\n1234567890123456;Çağan\n', encoding="utf-8")
    snap = convert([src], "tsv")
    assert only(snap).read_text(encoding="utf-8") == (
        "kod\tad\n007\tAyşe; Fatma\n1234567890123456\tÇağan\n"
    )
    assert "Read with semicolons as separator" in snap.notes
    back = only(convert([only(snap)], "csv"))
    assert back.read_text(encoding="utf-8").startswith("kod,ad\n007,Ayşe; Fatma\n")


def test_windows_1254_text_document(tmp_path):
    src = tmp_path / "not.txt"
    src.write_bytes("Ağaç gölgesi, ışık ve ÇİÇEK.\n".encode("cp1254"))
    snap = convert([src], "odt")
    assert "Ağaç gölgesi, ışık ve ÇİÇEK." in text_of(only(snap), "odt")


def test_remote_images_are_never_fetched(tmp_path):
    src = tmp_path / "remote.md"
    src.write_text("# Uzak\n\n![logo](https://example.com/logo.png)\n", encoding="utf-8")
    for target in ("docx", "html", "epub"):
        snap = convert([src], target)
        assert snap.state is JobState.SUCCEEDED, (target, snap.error)
        assert pandoc.REMOTE_NOTE in snap.notes
    html = (tmp_path / "remote.html").read_text(encoding="utf-8")
    assert 'href="https://example.com/logo.png"' in html


@pytest.mark.skipif(not process.offline_prefix(), reason="no network namespaces")
def test_offline_programs_have_no_network():
    result = process.run(
        ["python3", "-c", "import socket; socket.getaddrinfo('example.com', 80)"],
        offline=True,
        check=False,
    )
    assert result.returncode != 0
    assert any("gaierror" in line or "resolution" in line for line in result.stderr_tail)


def test_works_while_another_libreoffice_runs(tmp_path):
    """The user's LibreOffice keeps its own profile; Quince uses its own ones."""
    profile = tmp_path / "user-profile"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    running = subprocess.Popen(
        ["soffice", f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
         "--invisible", "--accept=pipe,name=quince-test-user;urp;"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )  # fmt: skip
    try:
        src = Path(shutil.copy(GENERATED / "sample.docx", tmp_path))
        assert only(convert([src], "pdf")).exists()
        assert running.poll() is None, "the other LibreOffice is still running"
    finally:
        os.killpg(running.pid, 15)
        running.wait(10)


def test_two_conversions_at_once(tmp_path):
    files = []
    for name in ("a", "b"):
        folder = tmp_path / name
        folder.mkdir()
        files.append(Path(shutil.copy(GENERATED / "sample.odt", folder)))
    results = {}

    def run(path):
        results[path] = convert([path], "docx")

    threads = [threading.Thread(target=run, args=(p,)) for p in files]
    for t in threads:
        t.start()
    for t in threads:
        t.join(300)
    assert all(s.state is JobState.SUCCEEDED for s in results.values())


def test_unreadable_document_is_explained(tmp_path):
    src = tmp_path / "broken.docx"
    src.write_bytes(b"PK\x03\x04 not really a zip")
    snap = convert([src], "pdf")
    assert snap.state is JobState.FAILED
    assert snap.error.startswith("broken.docx: ")
