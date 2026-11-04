"""Golden-output tests for the priority route PDF → Markdown.

Fixtures (``scripts/make_fixtures.py``, from ``tests/fixtures/golden/rich-pdf.html``):

- ``rich.pdf``: two pages written by LibreOffice — headings, bold/italic, a link, nested and
  numbered lists, a ruled table, Turkish letters — with a running header and a
  "Sayfa N / M" footer on every page;
- ``columns.pdf``: a heading over two text columns (reading order);
- ``scanned.pdf``: a text page, then the same page as a picture (needs OCR).

Expected Markdown is in ``tests/fixtures/golden/expected/``; ``QUINCE_UPDATE_GOLDEN=1``
rewrites it. PDFs in ``tests/fixtures/private/`` (git-ignored) are checked for the basics.
"""

import os
import re
import shutil
from pathlib import Path

import pytest

from quince.backends import pdf
from quince.core.jobs import JobQueue, JobState
from quince.core.registry import discover
from quince.core.selection import Selection
from tests.unit.test_jobs import wait_finished

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
GENERATED = FIXTURES / "generated" / "golden"
EXPECTED = FIXTURES / "golden" / "expected"
PRIVATE = FIXTURES / "private"
UPDATE = os.environ.get("QUINCE_UPDATE_GOLDEN") == "1"
RAW_HTML = re.compile(r"<(?!https?://)[a-zA-Z!/][^>\n]*>")
OCR = pdf.tessdata_dir() is not None


def to_markdown(src: Path):
    op = discover().get("convert.document.md")
    queue = JobQueue(workers=1)
    try:
        return wait_finished(
            queue, queue.submit(op.run, Selection.from_paths([src]), title="t"), 300
        )
    finally:
        queue.shutdown()


def fixture(name: str, tmp_path: Path) -> Path:
    src = GENERATED / name
    if not src.exists():
        pytest.skip("run scripts/make_fixtures.py first")
    return Path(shutil.copy(src, tmp_path))


def text_of(snap) -> str:
    assert snap.state is JobState.SUCCEEDED, snap.error
    (out,) = snap.outputs
    md = next(out.glob("*.md")) if out.is_dir() else out
    return md.read_text(encoding="utf-8")


def check_golden(name: str, text: str) -> None:
    expected = EXPECTED / f"{name}.md"
    if UPDATE:
        expected.write_text(text, encoding="utf-8")
    assert text == expected.read_text(encoding="utf-8")


def test_report(tmp_path):
    text = text_of(to_markdown(fixture("rich.pdf", tmp_path)))
    check_golden("rich.pdf", text)
    assert not RAW_HTML.search(text)
    assert text.startswith("# Proje Raporu: Çağrı Merkezi\n")
    assert "## Görevler" in text and "### Adımlar" in text and "## Ek: Yöntem" in text
    assert "**kalın**" in text and "_italik_" in text
    assert "[Quince sayfası](https://example.com/quince)" in text  # just the linked words
    assert re.search(r"^- Ölçümleri topla\n    - Günlük çağrı sayısı$", text, re.M)
    assert re.search(r"^1\. Verileri dışa aktar\n2\. Tabloyu güncelle\n3\. ", text, re.M)
    assert "|İstanbul|1250|42|" in text
    assert "Gizli" not in text, "the running header is gone"
    assert "Sayfa" not in text, "the page numbers are gone"
    assert "ğüşıöç ĞÜŞİÖÇ" in text
    assert "\n\n\n" not in text


def test_two_columns_are_read_in_order(tmp_path):
    text = text_of(to_markdown(fixture("columns.pdf", tmp_path)))
    check_golden("columns.pdf", text)
    order = [text.index(part) for part in ("# İki Sütunlu Bülten", "Sol sütun birinci",
             "Sol sütun ikinci", "Sağ sütun birinci", "Sağ sütun ikinci")]  # fmt: skip
    assert order == sorted(order)


@pytest.mark.skipif(OCR, reason="OCR is installed; the OCR test covers scanned pages")
def test_scanned_page_without_ocr_is_explained(tmp_path):
    src = fixture("scanned.pdf", tmp_path)
    snap = to_markdown(src)
    assert snap.state is JobState.FAILED
    assert snap.error == (
        "scanned.pdf: Page 2 is scanned and needs OCR (Turkish and English). "
        "Install it with: sudo apt install tesseract-ocr tesseract-ocr-tur"
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["scanned.pdf"]  # no partial file


@pytest.mark.skipif(not OCR, reason="needs tesseract-ocr and tesseract-ocr-tur")
def test_scanned_page_is_read_with_ocr(tmp_path):
    snap = to_markdown(fixture("scanned.pdf", tmp_path))
    text = text_of(snap)
    assert "Page 2 is read with OCR" in snap.notes
    second = text.split("ğüşıöç", 1)[-1] if text.count("Proje Raporu") > 1 else text
    for words in ("Proje Raporu", "Çağrı Merkezi", "Ölçümleri topla", "İstanbul", "Muğla"):
        assert text.count(words) >= 2, f"{words!r} read from the scan too"
    assert second


PRIVATE_PDFS = sorted(PRIVATE.glob("*.pdf")) if PRIVATE.is_dir() else []


@pytest.mark.skipif(not PRIVATE_PDFS, reason="no private sample PDFs")
@pytest.mark.parametrize("path", PRIVATE_PDFS, ids=[p.name for p in PRIVATE_PDFS])
def test_private_pdfs(path, tmp_path):
    snap = to_markdown(Path(shutil.copy(path, tmp_path)))
    if snap.state is JobState.FAILED and "needs OCR" in snap.error and not OCR:
        pytest.skip(snap.error)
    text = text_of(snap)
    assert text.strip()
    assert not RAW_HTML.search(text), RAW_HTML.search(text)
    assert "�" not in text
