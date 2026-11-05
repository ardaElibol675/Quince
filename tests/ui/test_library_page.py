import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from quince.core.history import History
from quince.ui.library.model import LibraryModel
from quince.ui.library.page import LibraryPage, format_date, sources_text


@pytest.fixture
def setup(qtbot, tmp_path):
    model = LibraryModel(History(tmp_path / "h.sqlite3"))
    a = tmp_path / "report.md"
    a.write_text("x")
    b = tmp_path / "photo.jpg"
    b.write_text("x")
    model.history.record([a], op_id="convert.text.md", title="Convert to Markdown",
                         sources=[tmp_path / "report.pdf"], when=100)  # fmt: skip
    model.history.record([b], op_id="convert.image.jpg", title="Convert to JPG", when=200)
    model.history.record([tmp_path / "gone.png"], op_id="convert.image.png",
                         title="Convert to PNG", when=300)  # fmt: skip
    shown, confirms = [], []
    page = LibraryPage(model, open_file=lambda p: None, show_files=shown.append,
                       confirm=lambda _w, text: confirms.append(text) or True)  # fmt: skip
    qtbot.addWidget(page)
    page.requests = []
    page.wheel_requested.connect(lambda paths, at: page.requests.append(paths))
    return page, model, a, b, shown, confirms


def _names(page):
    return [page.tree.topLevelItem(i).text(0) for i in range(page.tree.topLevelItemCount())]


def test_lists_newest_first_with_details(setup):
    page, _model, a, _b, _shown, _c = setup
    assert _names(page) == ["gone.png", "photo.jpg", "report.md"]
    report = page.tree.topLevelItem(2)
    assert report.text(1) == "Convert to Markdown"
    assert report.text(2) == "report.pdf"
    assert report.text(4) == str(a.parent)
    assert "no longer exists" in page.tree.topLevelItem(0).toolTip(0)
    assert page.count.text() == "3 files"


def test_search_filters(setup, qtbot):
    page, *_ = setup
    page.search.setText("markdown")
    qtbot.waitUntil(lambda: _names(page) == ["report.md"], timeout=1000)
    assert page.count.text() == "1 match"
    page.search.setText("nothing like this")
    qtbot.waitUntil(lambda: page.tree.topLevelItemCount() == 0, timeout=1000)
    assert "matches" in page.empty.text()


def test_enter_reuses_existing_selected_files(setup):
    page, _model, a, b, _shown, _c = setup
    for i in range(3):
        page.tree.topLevelItem(i).setSelected(True)
    page.tree.setCurrentItem(page.tree.topLevelItem(0), 0)
    for i in range(3):
        page.tree.topLevelItem(i).setSelected(True)
    QTest.keyClick(page.tree, Qt.Key.Key_Return)
    assert page.requests == [[b, a]]  # the missing file is left out


def test_show_in_files_remove_and_clear(setup):
    page, model, a, b, shown, confirms = setup
    page.tree.topLevelItem(2).setSelected(True)
    page.show_selected()
    assert shown == [[a]]
    QTest.keyClick(page.tree, Qt.Key.Key_Delete)
    assert _names(page) == ["gone.png", "photo.jpg"]
    assert a.exists()  # only forgotten
    page.clear()
    assert confirms and model.entries() == [] and b.exists()
    assert "Nothing here yet" in page.empty.text()


def test_format_date_and_sources():
    now = time.mktime((2026, 11, 5, 15, 0, 0, 0, 0, -1))
    assert format_date(now - 3600, now) == "Today 14:00"
    assert format_date(now - 86400, now) == "Yesterday 15:00"
    assert format_date(now - 3 * 86400, now) == "2026-11-02 15:00"

    class E:
        sources = (Path("/a/1.png"), Path("/a/2.png"), Path("/a/3.png"))

    assert sources_text(E) == "1.png +2 more"
