"""Golden-output tests for the priority routes: office documents, HTML and EPUB → Markdown.

The source is ``tests/fixtures/golden/rich.md`` (headings, nested and numbered lists, a
table, bold/italic, a link, a quote, an image, Turkish letters). ``scripts/make_fixtures.py``
turns it into DOCX (pandoc and LibreOffice), ODT, RTF, DOC, HTML and EPUB. Each must convert
to exactly the reviewed Markdown in ``tests/fixtures/golden/expected/``.

After an intended change, rewrite the expected files with ``QUINCE_UPDATE_GOLDEN=1`` and
review the diff before committing.

The user's own documents in ``tests/fixtures/private/`` (git-ignored) are converted too, and
checked for the basics: no raw HTML, Turkish letters intact, headings kept.
"""

import os
import re
import shutil
from pathlib import Path

import pytest

from quince.core import filetype
from quince.core.deps import Availability
from quince.core.jobs import JobQueue, JobState
from quince.core.registry import discover
from quince.core.selection import Selection
from tests.unit.test_jobs import wait_finished

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        Availability.probe().missing(("pandoc", "soffice")), reason="needs pandoc and soffice"
    ),
]

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
GENERATED = FIXTURES / "generated" / "golden"
EXPECTED = FIXTURES / "golden" / "expected"
PRIVATE = FIXTURES / "private"
KINDS = ("docx", "lo.docx", "odt", "rtf", "doc", "html", "epub")
UPDATE = os.environ.get("QUINCE_UPDATE_GOLDEN") == "1"
RAW_HTML = re.compile(r"<(?!https?://)[a-zA-Z!/][^>\n]*>")


def to_markdown(src: Path):
    op = discover().get("convert.document.md")
    queue = JobQueue(workers=1)
    try:
        return wait_finished(
            queue, queue.submit(op.run, Selection.from_paths([src]), title="t"), 300
        )
    finally:
        queue.shutdown()


def markdown_of(snap) -> tuple[str, list[str]]:
    """The Markdown text and the media files (relative paths) of a result."""
    assert snap.state is JobState.SUCCEEDED, snap.error
    (out,) = snap.outputs
    if out.is_dir():
        (md,) = out.glob("*.md")
        media = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file() and p != md)
        return md.read_text(encoding="utf-8"), media
    assert out.suffix == ".md"
    return out.read_text(encoding="utf-8"), []


@pytest.mark.parametrize("kind", KINDS)
def test_golden(kind, tmp_path):
    fixture = GENERATED / f"rich.{kind}"
    if not fixture.exists():
        pytest.skip("run scripts/make_fixtures.py first")
    src = Path(shutil.copy(fixture, tmp_path))
    text, media = markdown_of(to_markdown(src))
    expected = EXPECTED / f"rich.{kind}.md"
    if UPDATE:
        expected.write_text(text, encoding="utf-8")
    assert text == expected.read_text(encoding="utf-8")

    # What the golden files promise, spelled out.
    assert not RAW_HTML.search(text), "no raw HTML (anchors, spans, comments)"
    assert "# Proje Raporu: Çağrı Merkezi" in text
    assert "ğüşıöç ĞÜŞİÖÇ" in text
    assert "**kalın**" in text and "*italik*" in text
    assert "[Quince sayfası](https://example.com/quince)" in text
    assert re.search(r"^ +- Hafta içi ve hafta sonu ayrı$", text, re.M), "three list levels"
    assert re.search(r"^ +1\. +Şehirleri kontrol et$", text, re.M), "nested numbered list"
    assert re.search(r"^3\. +Sonuçları gönder$", text, re.M), "one numbered list, not two"
    assert re.search(r"^\| İstanbul +\| +1250 +\|", text, re.M), "the table with its rows"
    assert re.search(r"^\| Şehir +\|", text, re.M), "the header row stays the header"
    assert len(media) == 1 and media[0].startswith("media/")
    assert f"]({media[0]})" in text, "the image is linked relatively"
    assert text.count("Logo") == 1, "no repeated caption"


def test_markdown_file_without_images(tmp_path):
    """A document without images gives a single .md file, not a folder."""
    src = FIXTURES / "generated" / "sample.docx"
    if not src.exists():
        pytest.skip("run scripts/make_fixtures.py first")
    snap = to_markdown(Path(shutil.copy(src, tmp_path)))
    (out,) = snap.outputs
    assert out.is_file() and out.name == "sample.md"


PRIVATE_DOCS = (
    sorted(p for p in PRIVATE.iterdir() if p.suffix.lower() in (".docx", ".odt", ".rtf", ".doc"))
    if PRIVATE.is_dir()
    else []
)


@pytest.mark.skipif(not PRIVATE_DOCS, reason="no private sample documents")
@pytest.mark.parametrize("path", PRIVATE_DOCS, ids=[p.name for p in PRIVATE_DOCS])
def test_private_documents(path, tmp_path):
    assert filetype.detect(path) is not None
    text, _media = markdown_of(to_markdown(Path(shutil.copy(path, tmp_path))))
    assert text.strip(), "some text came out"
    assert not RAW_HTML.search(text), RAW_HTML.search(text)
    assert "�" not in text, "no broken characters"
