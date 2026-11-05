"""Text, source code, sheets and slides → Markdown, TXT, HTML, EPUB and PDF (``text`` backend)."""

import shutil
from pathlib import Path

import pytest

from quince.core import filetype, formats
from quince.core.deps import Availability
from quince.core.jobs import JobState
from quince.core.operations import Kind
from quince.core.registry import discover
from quince.core.selection import Selection
from tests.integration.test_document_routes import convert, only, text_of

pytestmark = pytest.mark.integration

GENERATED = Path(__file__).resolve().parent.parent / "fixtures" / "generated"
AVAILABLE = Availability.probe()
REGISTRY = discover()
ROUTES = [r for r in REGISTRY.routes if r.backend == "text"]
CODE = formats.Category.CODE
# A word each fixture's text contains.
WORDS = {"document": "Quince", "slides": "Ayva", "spreadsheet": "quince", "code": "Merhaba"}

PYTHON_MD = '''````python
"""Quince örnek kodu."""


def greet(name: str) -> str:
    # ``` is not a fence here | neither is a pipe
    return f"Merhaba, {name}!"
````
'''

SLIDES_MD = """## Quince slide one

- Ayva ağacı
  - Çiçek \\*açar\\* \\| 2 kat
- Meyve verir

> **Notes:** Konuşmacı notu

## Quince slide two

| Ad   | Şehir |
| ---- | ----- |
| Ayşe | İzmir |
"""

SHEETS_MD = """## Fruits

| name   | count |
| ------ | ----- |
| apple  | 3     |
| quince | 5     |

## Totals

| total | 8   |
| ----- | --- |
"""


def fixture(source: str, tmp_path: Path) -> Path:
    """A copy of the source's fixture; code formats get the Python sample's text."""
    fmt = formats.get(source)
    name = "sample.py" if fmt.category is CODE else f"sample.{fmt.extension}"
    if not (GENERATED / name).exists():
        pytest.skip("run scripts/make_fixtures.py first")
    return Path(shutil.copy(GENERATED / name, tmp_path / f"sample.{fmt.extension}"))


def skip_without(*deps: str) -> None:
    if missing := AVAILABLE.missing(deps):
        pytest.skip(f"needs {', '.join(missing)}")


@pytest.mark.parametrize("route", ROUTES, ids=[f"{r.source}-{r.target}" for r in ROUTES])
def test_route(route, tmp_path):
    skip_without(*route.all_requires)
    src = fixture(route.source, tmp_path)
    original = src.read_bytes()
    out = only(convert([src], route.target))
    assert src.read_bytes() == original
    assert out.is_file() and filetype.detect(out).id == route.target
    word = WORDS[formats.get(route.source).category.value]
    assert word.lower() in text_of(out, route.target).lower()


def test_python_file_offers_markdown_on_the_convert_wheel(tmp_path):
    src = fixture("py", tmp_path)
    labels = [op.label for op in REGISTRY.query(Selection.from_paths([src]), Kind.CONVERT)]
    assert set(labels) == {"MD", "TXT", "HTML", "PDF"}
    js = tmp_path / "app.js"
    js.write_text("let a = 1;\n")
    both = REGISTRY.query(Selection.from_paths([src, js]), Kind.CONVERT)
    assert {op.label for op in both} == {"MD", "TXT", "HTML", "PDF"}


def test_python_to_markdown_is_a_fenced_block(tmp_path):
    out = only(convert([fixture("py", tmp_path)], "md"))
    assert out.read_text(encoding="utf-8") == PYTHON_MD


def test_code_html_and_pdf_are_highlighted(tmp_path):
    skip_without("pandoc", "soffice")
    src = fixture("py", tmp_path).rename(tmp_path / "__init__.py")
    html = only(convert([src], "html")).read_text(encoding="utf-8")
    assert 'class="kw">def</span>' in html and ">__init__.py</h1>" in html
    import pymupdf

    with pymupdf.open(only(convert([src], "pdf"))) as doc:
        spans = [
            s
            for block in doc[0].get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for s in line["spans"]
        ]
    assert any(s["text"].strip() == "def" and s["color"] != 0 for s in spans)
    assert any("__init__.py" in s["text"] for s in spans)


def test_slides_to_markdown(tmp_path):
    for source in ("pptx", "odp"):
        out = only(convert([fixture(source, tmp_path)], "md"))
        assert out.read_text(encoding="utf-8") == SLIDES_MD, source
        out.unlink()


def test_sheets_to_markdown_in_workbook_order(tmp_path):
    skip_without("soffice")
    for source in ("ods", "xlsx", "xls"):
        out = only(convert([fixture(source, tmp_path)], "md"))
        assert out.read_text(encoding="utf-8") == SHEETS_MD, source
        out.unlink()


def test_turkish_semicolon_csv_to_markdown(tmp_path):
    src = tmp_path / "rapor.csv"
    src.write_bytes("Şehir;Oran\nİzmir;3,5\nMuğla;*10*\n".encode("cp1254"))
    snap = convert([src], "md")
    assert only(snap).read_text(encoding="utf-8") == (
        "| Şehir | Oran   |\n| ----- | ------ |\n| İzmir | 3,5    |\n| Muğla | \\*10\\* |\n"
    )
    assert "Read as Windows-1254 (Turkish) text" in snap.notes
    assert "Read with semicolons as separator" in snap.notes


def test_text_to_markdown_keeps_lines_and_markup_characters(tmp_path):
    src = tmp_path / "not.txt"
    src.write_text("# Liste\n- bir\n1. iki\n\nSon *paragraf*\n", encoding="utf-8")
    out = only(convert([src], "md"))
    assert out.read_text(encoding="utf-8") == (
        "\\# Liste\\\n\\- bir\\\n1\\. iki\n\nSon \\*paragraf\\*\n"
    )


def test_empty_text_is_explained(tmp_path):
    src = tmp_path / "empty.txt"
    src.write_text("\n\n")
    snap = convert([src], "md")
    assert snap.state is JobState.FAILED and "no text" in snap.error
