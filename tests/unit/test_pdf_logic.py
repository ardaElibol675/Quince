"""The PDF → Markdown clean-up rules, the subtitle and archive checks, on synthetic input."""

import xml.dom.minidom
from pathlib import Path

import pymupdf
import pytest

from quince.backends import archive, pdf
from quince.core.jobs import JobError

# --- Markdown tidying -------------------------------------------------------------------


def test_headings_lose_emphasis_and_start_at_level_one():
    text = "##### **Bölüm** **Bir**\nmetin\n###### _Alt_\n"
    assert pdf.tidy_markdown(text) == "# Bölüm Bir\n\nmetin\n\n## Alt\n"


def test_lists_are_tight_and_indented_by_level():
    text = "   - bir\n\n       - iki\n\n   - üç\n\n1. a\n2. b\n"
    assert pdf.tidy_markdown(text) == "- bir\n    - iki\n- üç\n\n1. a\n2. b\n"


def test_paragraph_lines_are_joined_and_hyphens_mended():
    text = "Bu bir uzun\nparagraf ve bölün-\nmüş kelime.\n\n|a|b|\n|---|---|\n|1<br>2|3|\n"
    assert pdf.tidy_markdown(text) == (
        "Bu bir uzun paragraf ve bölünmüş kelime.\n\n|a|b|\n|---|---|\n|1 2|3|\n"
    )


def test_empty_headings_are_dropped():
    assert pdf.tidy_markdown("# Başlık\n###\nmetin\n") == "# Başlık\n\nmetin\n"


def test_pages_are_joined_where_a_paragraph_continues():
    pages = ["Cümle burada\ndevam ediyor ve", "bitiyor.", "## Başlık", "kelime-", "ler"]
    assert pdf.join_pages(pages) == (
        "Cümle burada\ndevam ediyor ve bitiyor.\n\n## Başlık\n\nkelimeler"
    )
    assert pdf.join_pages(["Bitti.", "yeni sayfa"]) == "Bitti.\n\nyeni sayfa"


# --- running headers and footers --------------------------------------------------------


def _doc(pages):
    """pages: list of [(y, text, size)]"""
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page(width=595, height=842)
        for y, text, size in lines:
            page.insert_text((56, y), text, fontsize=size)
    return doc


def test_small_repeated_edge_lines_and_page_numbers_are_running():
    body = [(300 + 20 * i, f"Body line {i} with enough text to be body", 11) for i in range(6)]
    doc = _doc(
        [[(30, "ACME Report - Confidential", 8), *body, (820, f"{n}", 9)] for n in range(1, 4)]
    )
    running = pdf.running_lines(doc)
    assert running[0] == {"acme report - confidential", "#"}


def test_body_sized_repeats_are_never_running():
    body = [(300 + 20 * i, f"Body line {i} with enough text to be body", 11) for i in range(6)]
    doc = _doc([[(30, "Same first line on every page", 11), *body] for _ in range(3)])
    assert pdf.running_lines(doc) == [set(), set(), set()]


def test_only_edges_of_a_page_are_stripped():
    page = "Header\nbody\nbody\nbody\nHeader\nbody\nbody\nbody\nHeader"
    keys = {"header"}
    out = pdf.strip_running_lines([page], [keys])[0].split("\n")
    assert out.count("Header") == 1  # the middle one stays: it is not at an edge
    merged = "CH2 Basic Physics of Semiconductors 10"
    assert pdf._only_running(merged, {"ch# basic physics of semiconductors", "#"})
    assert not pdf._only_running("Chapter 10 summary", {"#"})


def test_page_number_patterns():
    for text in ("12", "- 12 -", "Page 3 of 10", "Sayfa 3 / 10", "s. 4"):
        assert pdf._PAGE_NUMBER.match(text), text
    for text in ("12 Ekim 2026", "Bölüm 3", "3.5 kg"):
        assert not pdf._PAGE_NUMBER.match(text), text


# --- links, scans, OCR messages ----------------------------------------------------------------


def test_links_are_cut_to_their_words():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((56, 100), "Read the Quince page for more.", fontsize=11)
    rect = page.search_for("Quince page")[0]
    page.insert_link({"kind": pymupdf.LINK_URI, "from": rect, "uri": "https://x.test/q"})
    page = doc.reload_page(page)
    md = "[Read the Quince page for more.](https://x.test/q)"
    assert pdf.fix_links(md, page) == "Read the [Quince page](https://x.test/q) for more."


def test_scanned_pages_are_recognized():
    doc = pymupdf.open()
    text_page = doc.new_page()
    text_page.insert_text((56, 100), "A page with plenty of real text.", fontsize=11)
    scan = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), False)
    pix.clear_with(200)
    scan.insert_image(scan.rect, pixmap=pix)
    doc.new_page()  # blank
    # Page objects go stale as pages are added; look them up again.
    assert [pdf.is_scanned(doc[i]) for i in range(3)] == [False, True, False]


def test_missing_ocr_is_explained(monkeypatch):
    monkeypatch.setattr(pdf, "tessdata_dir", lambda: None)
    with pytest.raises(JobError, match=r"^Page 2 is scanned and needs OCR"):
        pdf.require_ocr([1])
    with pytest.raises(JobError, match=r"^Pages 2, 5 are scanned and need OCR .*tesseract-ocr-tur"):
        pdf.require_ocr([1, 4])


def test_tessdata_needs_both_languages(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    monkeypatch.setattr(pdf.glob, "glob", lambda pattern: [])
    (tmp_path / "eng.traineddata").write_bytes(b"x")
    assert pdf.tessdata_dir() is None
    (tmp_path / "tur.traineddata").write_bytes(b"x")
    assert pdf.tessdata_dir() == str(tmp_path)


def test_broken_and_locked_pdfs(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 garbage")
    with pytest.raises(JobError, match="Not a readable PDF"):
        pdf.open_pdf(broken)
    locked = tmp_path / "locked.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "secret")
    doc.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    with pytest.raises(JobError, match="password-protected"):
        pdf.open_pdf(locked)


# --- generated office XML -----------------------------------------------------------------


def test_flat_spreadsheet_is_valid_xml():
    xml_text = pdf._fods([("Page 1", [["Şehir", "Çağrı"], ["İzmir", "830"], [None, "1.5"]])])
    dom = xml.dom.minidom.parseString(xml_text)
    cells = dom.getElementsByTagName("table:table-cell")
    assert [c.getAttribute("office:value-type") for c in cells] == [
        "string", "string", "string", "float", "string", "float",
    ]  # fmt: skip
    assert dom.getElementsByTagName("table:table")[0].getAttribute("table:name") == "Page 1"


def test_flat_presentation_is_valid_xml():
    xml_text = pdf._fodp([b"jpeg-1", b"jpeg-2"], 595, 842, [(100, 140), (140, 100)])
    dom = xml.dom.minidom.parseString(xml_text)
    assert len(dom.getElementsByTagName("draw:page")) == 2


# --- archives ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["../evil.txt", "a/../../evil", "/etc/passwd", "C:/x", "..\\x"])
def test_unsafe_names_are_refused(name):
    with pytest.raises(archive.UnsafeArchive, match="unsafe path"):
        archive.check_name(name)


@pytest.mark.parametrize("name", ["a.txt", "folder/b.txt", "folder/", "..dots..txt", "x/.hidden"])
def test_safe_names_pass(name):
    archive.check_name(name)


def test_links_leading_outside_are_refused(tmp_path):
    root = tmp_path / "root"
    (root / "dir").mkdir(parents=True)
    (root / "dir" / "ok").symlink_to("../file")
    archive.check_tree(root)
    (root / "dir" / "bad").symlink_to("../../outside")
    with pytest.raises(archive.UnsafeArchive, match="link leading outside"):
        archive.check_tree(root)
    assert Path(root).exists()
