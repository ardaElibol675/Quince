"""The PDF Editor's page list, splitting, grid geometry and writing (real PyMuPDF files)."""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from quince.backends import pdfedit
from quince.backends.pdfedit import BLANK, Page
from quince.core.jobs import JobError, JobQueue, JobState
from quince.core.selection import Selection
from quince.ui.editors.pdf._grid import GridLayout
from quince.ui.editors.pdf._split import overlay
from tests.unit.test_jobs import wait_finished


def make_pdf(path: Path, count: int, *, label: str = "Page", size=(300, 400), toc=None) -> Path:
    doc = pymupdf.open()
    for n in range(count):
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_text((40, 60), f"{label} {n + 1}", fontsize=20)
    if toc:
        doc.set_toc(toc)
    doc.set_metadata({"title": f"{label} document", "author": "Quince"})
    doc.save(path)
    doc.close()
    return path


def texts(path: Path) -> list[str]:
    with pymupdf.open(path) as doc:
        return [page.get_text().strip() for page in doc]


def run(work, source: Path):
    """Run ``work(ctx)`` as a job; the snapshot."""
    queue = JobQueue(workers=1)
    try:
        snap = wait_finished(
            queue, queue.submit(work, Selection.from_paths([source]), title="t"), 30
        )
    finally:
        queue.shutdown()
    return snap


def write(tmp_path, sources, pages, name="out"):
    def work(ctx):
        out = ctx.new_output(sources[0].path, extension="pdf", name=name)
        pdfedit.write(ctx, sources, pages, out)

    snap = run(work, sources[0].path)
    assert snap.state is JobState.SUCCEEDED, snap.error
    return snap.outputs[0], snap


def pages_of(*specs) -> tuple[Page, ...]:
    return tuple(Page(uid, source, number) for uid, (source, number) in enumerate(specs))


# --- the page list --------------------------------------------------------------------------


def test_moved_keeps_order_and_counts_the_gap_in_the_current_list():
    pages = pages_of((0, 0), (0, 1), (0, 2), (0, 3), (0, 4))
    numbers = lambda ps: [p.number for p in ps]  # noqa: E731
    assert numbers(pdfedit.moved(pages, {3, 1}, 0)) == [1, 3, 0, 2, 4]
    assert numbers(pdfedit.moved(pages, {0}, 5)) == [1, 2, 3, 4, 0]
    assert numbers(pdfedit.moved(pages, {0, 1}, 4)) == [2, 3, 0, 1, 4]  # gap before page 4
    assert numbers(pdfedit.moved(pages, {2}, 2)) == [0, 1, 2, 3, 4]  # onto itself
    assert numbers(pdfedit.moved(pages, {2}, 99)) == [0, 1, 3, 4, 2]


def test_shifted_moves_each_page_one_place_and_stops_at_the_ends():
    pages = pages_of((0, 0), (0, 1), (0, 2), (0, 3))
    numbers = lambda ps: [p.number for p in ps]  # noqa: E731
    assert numbers(pdfedit.shifted(pages, {1, 3}, -1)) == [1, 0, 3, 2]
    assert numbers(pdfedit.shifted(pages, {0, 1}, -1)) == [0, 1, 2, 3]
    assert numbers(pdfedit.shifted(pages, {0, 2}, 1)) == [1, 0, 3, 2]
    assert numbers(pdfedit.shifted(pages, {2, 3}, 1)) == [0, 1, 2, 3]


def test_rotate_duplicate_remove_insert():
    pages = pages_of((0, 0), (0, 1), (0, 2))
    turned = pdfedit.rotated(pages, {1}, -1)
    assert [p.turns for p in turned] == [0, 3, 0]
    assert pdfedit.rotated(turned, {1}, 1)[1].turns == 0
    doubled, copies = pdfedit.duplicated(turned, {0, 1}, itertools.count(10))
    assert [(p.uid, p.number, p.turns) for p in doubled] == [
        (0, 0, 0), (10, 0, 0), (1, 1, 3), (11, 1, 3), (2, 2, 0),
    ]  # fmt: skip
    assert copies == (10, 11)
    assert [p.uid for p in pdfedit.without(doubled, {10, 2})] == [0, 1, 11]
    blank = Page(20, BLANK, blank_size=(100, 200))
    assert [p.uid for p in pdfedit.inserted(pages, 1, [blank])] == [0, 20, 1, 2]


def test_shown_size_and_blank_size_follow_the_neighbour():
    source = pdfedit.Source(Path("a.pdf"), "pdf", ((300, 400), (500, 200)))
    pages = (Page(0, 0, 0), Page(1, 0, 1, turns=1))
    assert pdfedit.shown_size(pages[1], [source]) == (200, 500)
    assert pdfedit.blank_size_near(pages, [source], 2) == (200, 500)
    assert pdfedit.blank_size_near(pages, [source], 0) == (300, 400)
    assert pdfedit.blank_size_near((), [source], 0) == pdfedit.A4


# --- splitting ------------------------------------------------------------------------------


def test_parse_ranges():
    assert pdfedit.parse_ranges("1-3, 5; 8-", 10) == [(0, 1, 2), (4,), (7, 8, 9)]
    assert pdfedit.parse_ranges(" -2 , 2 – 3 ", 4) == [(0, 1), (1, 2)]  # overlaps allowed
    assert pdfedit.parse_ranges("", 4) == []
    for text, reason in (
        ("1-x", "not a page or a range"),
        ("-", "not a page or a range"),
        ("0", "There is no page 0"),
        ("3-12", "There is no page 12 (10 pages)"),
        ("5-2", "the first page comes after the last"),
    ):
        with pytest.raises(ValueError, match=re.escape(reason)):
            pdfedit.parse_ranges(text, 10)


def test_parts_every_and_at():
    assert pdfedit.parts_every(5, 2) == [(0, 1), (2, 3), (4,)]
    assert pdfedit.parts_every(3, 10) == [(0, 1, 2)]
    assert pdfedit.parts_at(5, [3, 1, 0]) == [(0,), (1, 2), (3, 4)]
    assert pdfedit.parts_at(3, []) == [(0, 1, 2)]
    assert pdfedit.parts_at(0, [1]) == []


def test_split_overlay_labels_cuts_and_dims():
    shown = overlay([(0, 1), (2, 3), (2,)], 6)
    assert shown.labels == {0: "Part 1", 2: "Parts 2, 3"}
    assert shown.cuts == frozenset({2})
    assert shown.dimmed == frozenset({4, 5})


def test_bookmark_levels_move_up_under_a_removed_one():
    def levels(*entries):
        return pdfedit.kept_levels([(abs(n), n > 0) for n in entries])  # negative: removed

    assert levels(1, 2, 3, 2, 1) == [1, 2, 3, 2, 1]
    assert levels(1, -1, 2, 3, 2, 1) == [1, 1, 2, 1, 1]  # the children of a removed one
    assert levels(1, -2, 3, 3, 2) == [1, 2, 2, 2]
    assert levels(-1, -2, 3) == [1]
    assert levels() == []


# --- grid geometry --------------------------------------------------------------------------


def test_grid_layout_cells_hits_and_gaps():
    grid = GridLayout(width=500, count=7, cell=100, margin=10, gap=20)
    assert grid.columns == 4  # 4 * 120 - 20 = 460 <= 480
    assert grid.rows == 2
    x0, y0, _w, _h = grid.box(0)
    assert grid.box(5) == (x0 + 120, y0 + grid.step_y, 100, 100)
    assert grid.index_at(x0 + 50, y0 + 50) == 0
    assert grid.index_at(x0 + 110, y0 + 50) is None  # in the gap
    assert grid.index_at(x0 + 3 * 120 + 50, y0 + grid.step_y + 50) is None  # past the last
    assert grid.insertion_at(x0 + 10, y0 + 50) == (0, 0)
    assert grid.insertion_at(x0 + 70, y0 + 50) == (1, 0)
    assert grid.insertion_at(x0 + 1000, y0 + 50) == (4, 0)  # end of the first row
    assert grid.insertion_at(x0 + 1000, y0 + grid.step_y + 50) == (7, 1)
    assert grid.insertion_at(x0, y0 + 9999) == (4, 1)
    assert grid.marker(4, 0)[0] == x0 + 4 * 120 - 10  # right of the row's last page
    assert grid.marker(4, 1)[0] == x0 - 10  # left of the next row's first page
    assert list(grid.visible(0, 50)) == [0, 1, 2, 3]
    assert grid.touching((x0 + 50, y0 + 50, 130, grid.step_y)) == [0, 1, 4, 5]


def test_grid_pages_stand_on_their_place_and_only_they_hit():
    sizes = [(300, 400), (400, 200), (300, 400)]
    grid = GridLayout(width=500, count=3, cell=100, margin=10, gap=20, sizes=sizes)
    x0, y0, _w, _h = grid.box(0)
    assert grid.picture(0) == (x0 + 12.5, y0, 75, 100)
    px, py, pw, ph = grid.picture(1)  # landscape: on the bottom of its place
    assert (pw, ph) == (100, 50) and py + ph == y0 + 100
    assert grid.index_at(px + 50, py + 25) == 1
    assert grid.index_at(px + 50, py - 20) is None  # above a landscape page: empty
    assert grid.index_at(x0 + 5, y0 + 50) is None  # beside a portrait page: empty
    assert grid.index_at(x0 + 50, y0 + 100 + 12) == 0  # its number
    assert grid.index_at(x0 + 50, y0 - 3) == 0  # a few pixels off still hits
    assert grid.touching((x0, py - 20, 230, 10)) == [0]  # only the portrait page is there


def test_grid_navigation_keys():
    grid = GridLayout(width=500, count=7, cell=100, margin=10, gap=20)
    assert grid.moved_index(1, "down") == 5
    assert grid.moved_index(3, "down") == 6  # no page under it: the last
    assert grid.moved_index(5, "up") == 1
    assert grid.moved_index(1, "up") == 1
    assert grid.moved_index(0, "left") == 0
    assert grid.moved_index(6, "right") == 6
    assert grid.moved_index(4, "home") == 0 and grid.moved_index(0, "end") == 6
    assert grid.moved_index(6, "page_up", 3) == 0


# --- writing --------------------------------------------------------------------------------


def test_reorder_turn_remove_duplicate_keeps_text_and_metadata(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", 4)
    source = pdfedit.load_source(src, "pdf")
    pages = (Page(0, 0, 2, turns=1), Page(1, 0, 0), Page(2, 0, 0), Page(3, 0, 3, turns=3))
    out, _snap = write(tmp_path, [source], pages)
    assert out.name == "out.pdf"
    assert texts(out) == ["Page 3", "Page 1", "Page 1", "Page 4"]
    with pymupdf.open(out) as doc:
        assert [p.rotation for p in doc] == [90, 0, 0, 270]
        assert doc.metadata["title"] == "Page document"
    assert texts(src) == ["Page 1", "Page 2", "Page 3", "Page 4"]  # the original untouched


def test_turn_adds_to_the_page_own_rotation(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", 1)
    with pymupdf.open(src) as doc:
        doc[0].set_rotation(90)
        doc.save(tmp_path / "turned.pdf")
    source = pdfedit.load_source(tmp_path / "turned.pdf", "pdf")
    assert source.sizes == ((400.0, 300.0),)  # as shown
    out, _ = write(tmp_path, [source], (Page(0, 0, 0, turns=1),))
    with pymupdf.open(out) as doc:
        assert doc[0].rotation == 180


def test_links_follow_their_page_and_bookmarks_are_remapped(tmp_path):
    src = make_pdf(
        tmp_path / "a.pdf",
        4,
        toc=[[1, "One", 1], [1, "Two", 2], [2, "Under two", 3], [1, "Four", 4]],
    )
    with pymupdf.open(src) as doc:
        doc[0].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(40, 40, 140, 70),
                            "page": 3, "to": pymupdf.Point(0, 0)})  # fmt: skip
        doc[0].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(40, 80, 140, 100),
                            "uri": "https://example.org/"})  # fmt: skip
        doc.saveIncr()
    source = pdfedit.load_source(src, "pdf")
    # Page 4, page 1, page 3: page 2 (and its bookmark) removed.
    out, _ = write(tmp_path, [source], pages_of((0, 3), (0, 0), (0, 2)))
    with pymupdf.open(out) as doc:
        links = doc[1].get_links()
        assert {link["kind"] for link in links} == {pymupdf.LINK_GOTO, pymupdf.LINK_URI}
        goto = next(link for link in links if link["kind"] == pymupdf.LINK_GOTO)
        assert goto["page"] == 0  # page 4 is now the first page
        assert doc.get_toc() == [[1, "One", 2], [1, "Under two", 3], [1, "Four", 1]]


def test_several_pdfs_pictures_and_blank_pages_in_one(tmp_path):
    a = make_pdf(tmp_path / "a.pdf", 2, label="A", toc=[[1, "A start", 1]])
    b = make_pdf(tmp_path / "b.pdf", 2, label="B", size=(500, 300), toc=[[1, "B start", 1]])
    Image.new("RGB", (200, 100), (200, 30, 30)).save(tmp_path / "pic.png", dpi=(72, 72))
    sources = [pdfedit.load_source(a, "pdf"), pdfedit.load_source(b, "pdf"),
               pdfedit.load_source(tmp_path / "pic.png", "png")]  # fmt: skip
    assert sources[2].sizes == (pytest.approx((200, 100), abs=0.1),) and sources[2].data
    pages = (
        Page(0, 1, 1),
        Page(1, 0, 0),
        Page(2, BLANK, blank_size=(500, 300)),
        Page(3, 2, 0),
        Page(4, 1, 0),
        Page(5, BLANK, blank_size=(500, 300)),
    )
    out, _ = write(tmp_path, sources, pages)
    with pymupdf.open(out) as doc:
        assert doc.page_count == 6
        assert [p.get_text().strip() for p in doc] == ["B 2", "A 1", "", "", "B 1", ""]
        assert (doc[2].rect.width, doc[2].rect.height) == (500, 300)
        assert len(doc[3].get_images()) == 1 and round(doc[3].rect.width) == 200
        assert doc.get_toc() == [[1, "A start", 2], [1, "B start", 5]]
        assert doc.metadata["title"] == "A document"  # from the first PDF


def test_unchanged_single_pdf_keeps_its_page_labels(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", 3)
    with pymupdf.open(src) as doc:
        doc.set_page_labels([{"startpage": 0, "prefix": "A-", "style": "D", "firstpagenum": 1}])
        doc.saveIncr()
    source = pdfedit.load_source(src, "pdf")
    out, _ = write(tmp_path, [source], pdfedit.first_pages([source]))
    with pymupdf.open(out) as doc:
        assert doc[1].get_label() == "A-2"


def test_parts_are_written_as_separate_files(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", 5)
    source = pdfedit.load_source(src, "pdf")
    pages = pdfedit.first_pages([source])
    parts = [(0, 1), (2,), (1, 3, 4)]

    def work(ctx):
        folder = ctx.new_output(src, extension="", directory=True, name="a-parts")
        outs = [folder / f"a-parts-{n:02d}.pdf" for n in (1, 2, 3)]
        pdfedit.write_parts(ctx, [source], pages, parts, outs)

    snap = run(work, src)
    assert snap.state is JobState.SUCCEEDED, snap.error
    folder = snap.outputs[0]
    assert folder.name == "a-parts"
    assert sorted(p.name for p in folder.iterdir()) == [
        "a-parts-01.pdf", "a-parts-02.pdf", "a-parts-03.pdf",
    ]  # fmt: skip
    assert texts(folder / "a-parts-03.pdf") == ["Page 2", "Page 4", "Page 5"]


def test_a_changed_or_unreadable_source_is_explained(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", 3)
    source = pdfedit.load_source(src, "pdf")
    make_pdf(src, 2)  # changed after it was opened

    def work(ctx):
        out = ctx.new_output(src, extension="pdf", name="x")
        pdfedit.write(ctx, [source], pdfedit.first_pages([source]), out)

    snap = run(work, src)
    assert snap.state is JobState.FAILED
    assert "changed after it was opened" in snap.error
    assert not (tmp_path / "x.pdf").exists()
    (tmp_path / "bad.pdf").write_bytes(b"not a pdf")
    with pytest.raises(JobError, match=r"bad\.pdf: Not a readable PDF"):
        pdfedit.load_source(tmp_path / "bad.pdf", "pdf")
    with pytest.raises(JobError, match="only PDFs and pictures"):
        pdfedit.load_source(tmp_path / "notes.txt", "txt")


def test_no_pages_is_an_error(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", 1)
    source = pdfedit.load_source(src, "pdf")
    snap = run(lambda ctx: pdfedit.write(ctx, [source], (), ctx.new_output(src, name="x")), src)
    assert snap.state is JobState.FAILED and "no pages" in snap.error
