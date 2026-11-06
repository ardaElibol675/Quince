"""The PDF Editor driven like a user would (keys, mouse, files dropped in), saving through
the real job queue."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
import shiboken6
from PIL import Image
from PySide6.QtCore import QEvent, QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from quince.core.jobs import JobQueue, JobState
from quince.core.operations import Kind
from quince.core.registry import discover as discover_operations
from quince.core.selection import Selection
from quince.ui.editors import manager
from quince.ui.editors.pdf.editor import PdfEditor
from tests.unit.test_jobs import wait_finished

_OPEN: list = []


@pytest.fixture(autouse=True)
def _close_editors(qapp):
    yield
    while _OPEN:
        window = _OPEN.pop()
        if shiboken6.isValid(window):
            window.confirm_discard = lambda: True
            window.close()
    QApplication.processEvents()


def make_pdf(path: Path, count: int, label: str = "Page") -> Path:
    doc = pymupdf.open()
    for n in range(count):
        doc.new_page(width=300, height=400).insert_text((40, 60), f"{label} {n + 1}", fontsize=20)
    doc.save(path)
    doc.close()
    return path


def texts(path: Path) -> list[str]:
    with pymupdf.open(path) as doc:
        return [page.get_text().strip() for page in doc]


def open_editor(paths, cell: int | None = 120) -> PdfEditor:
    selection = Selection.from_paths(paths)
    window = PdfEditor(selection, PdfEditor.load(selection))
    window.confirm_discard = lambda: True
    window.run_save_dialog = lambda dialog: dialog.target()
    window.compute = lambda work, on_slow=None: work()
    window.warnings = []
    window.warn = lambda text, details: window.warnings.append((text, details))
    _OPEN.append(window)
    window.resize(1000, 700)
    window.show()
    QApplication.processEvents()
    if cell is not None:
        window.grid.set_cell(cell)
    window.grid.setFocus()
    return window


def run_request(window, request):
    queue = JobQueue(workers=1)
    try:
        snap = wait_finished(queue, queue.submit(request.run, window.selection, title="t"), 30)
    finally:
        queue.shutdown()
    assert snap.state is JobState.SUCCEEDED, snap.error or snap.failures
    return snap


def emitted(window, action):
    requests = []
    window.saved.connect(requests.append)
    action()
    assert len(requests) == 1, "no job was handed over"
    return run_request(window, requests[0])


def numbers(window) -> list[int]:
    return [p.number for p in window.pages]


def key(window, k, mods=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(window.grid, k, mods)


def center(window, index) -> QPointF:
    x, y, w, h = window.grid.layout_.picture(index)
    return QPointF(x + w / 2, y + h / 2)


def mouse(grid, kind, pos: QPointF, mods=Qt.KeyboardModifier.NoModifier):
    types = {
        "press": QEvent.Type.MouseButtonPress,
        "move": QEvent.Type.MouseMove,
        "release": QEvent.Type.MouseButtonRelease,
    }
    button = Qt.MouseButton.NoButton if kind == "move" else Qt.MouseButton.LeftButton
    held = Qt.MouseButton.NoButton if kind == "release" else Qt.MouseButton.LeftButton
    event = QMouseEvent(types[kind], pos, grid.mapToGlobal(pos), button, held, mods)
    QApplication.sendEvent(grid, event)


def drop_files(grid, paths, pos: QPointF):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
    enter = QDragEnterEvent(pos.toPoint(), actions, mime, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)  # fmt: skip
    QApplication.sendEvent(grid, enter)
    assert enter.isAccepted() and enter.dropAction() == Qt.DropAction.CopyAction
    drop = QDropEvent(pos, actions, mime, Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier)  # fmt: skip
    QApplication.sendEvent(grid, drop)
    assert drop.dropAction() == Qt.DropAction.CopyAction  # never a move: the file stays


# --- opening ----------------------------------------------------------------------------------


def test_edit_pdf_opens_the_pdf_editor(tmp_path):
    ops = discover_operations()
    op = ops.get("tool.edit_pdf")
    assert op.editor == "pdf.edit" and op.icon == "quince-edit" and op.label == "Edit"
    assert manager.discover()["pdf.edit"] is PdfEditor
    one = Selection.from_paths([make_pdf(tmp_path / "a.pdf", 1)])
    two = Selection.from_paths([tmp_path / "a.pdf", make_pdf(tmp_path / "b.pdf", 1)])
    assert op.accepts.matches(one) and op.accepts.matches(two)
    assert op in ops.query(two, Kind.TOOL) and ops.get("tool.merge_pdfs") in ops.query(
        two, Kind.TOOL
    )


def test_the_manager_opens_and_presents_the_pdf_editor(tmp_path):
    """The real way in: load off the GUI thread, then ``present()`` (sizes and centers)."""
    errors = []
    mgr = manager.EditorManager(
        lambda *a: None,
        notify=lambda _t: None,
        notify_error=errors.append,
        compute=lambda work, on_slow=None: work(),
    )
    op = discover_operations().get("tool.edit_pdf")
    window = mgr.open(op, Selection.from_paths([make_pdf(tmp_path / "a.pdf", 3)]))
    assert not errors
    _OPEN.append(window)
    assert isinstance(window, PdfEditor) and window.isVisible()
    assert len(window.pages) == 3 and window.history is not None and not window.history.dirty


def test_several_pdfs_open_as_one_grid_and_save_as_one(tmp_path):
    a = make_pdf(tmp_path / "a.pdf", 2, "A")
    b = make_pdf(tmp_path / "b.pdf", 3, "B")
    window = open_editor([a, b])
    assert window.windowTitle() == "PDF Editor — 2 PDFs"
    assert window.info.text() == "5 pages · 1 selected"
    assert window.save_button.isEnabled()  # unchanged, still one PDF of both
    snap = emitted(window, window.save)
    out = snap.outputs[0]
    assert out == tmp_path / "a-edited.pdf"
    assert texts(out) == ["A 1", "A 2", "B 1", "B 2", "B 3"]


def test_thumbnails_are_rendered_off_the_gui_thread(tmp_path):
    window = open_editor([make_pdf(tmp_path / "a.pdf", 3)])
    window.grid.repaint()
    for _ in range(100):
        QTest.qWait(20)
        if window.thumbnails.idle() and window.thumbnails.image(0, 2, 100) is not None:
            break
    image = window.thumbnails.image(0, 2, 100)
    assert image is not None and max(image.width(), image.height()) == 160


# --- organizing -------------------------------------------------------------------------------


def test_keys_select_turn_move_delete_duplicate_blank_and_undo(tmp_path):
    window = open_editor([make_pdf(tmp_path / "a.pdf", 5)])
    key(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)  # pages 1-2
    assert window.grid.selected_indices() == [0, 1]
    key(window, Qt.Key.Key_R)
    key(window, Qt.Key.Key_R, Qt.KeyboardModifier.ShiftModifier)
    key(window, Qt.Key.Key_R, Qt.KeyboardModifier.ShiftModifier)
    assert [p.turns for p in window.pages] == [3, 3, 0, 0, 0]
    key(window, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    assert numbers(window) == [2, 0, 1, 3, 4]
    key(window, Qt.Key.Key_End, Qt.KeyboardModifier.AltModifier)
    assert numbers(window) == [2, 3, 4, 0, 1]
    key(window, Qt.Key.Key_Delete)
    assert numbers(window) == [2, 3, 4]
    assert window.grid.selected_indices() == [2]  # the page now in that place
    key(window, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    assert numbers(window) == [2, 3, 4, 4]
    key(window, Qt.Key.Key_B)
    assert [p.blank for p in window.pages] == [False, False, False, False, True]
    assert window.info.text() == "5 pages · 1 selected"
    window.undo()
    window.undo()
    window.undo()
    assert numbers(window) == [2, 3, 4, 0, 1]
    window.redo()
    assert numbers(window) == [2, 3, 4]
    key(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    key(window, Qt.Key.Key_Backspace)
    assert window.pages == () and not window.save_button.isEnabled()


def test_navigation_and_ctrl_space(tmp_path):
    window = open_editor([make_pdf(tmp_path / "a.pdf", 12)])
    columns = window.grid.layout_.columns
    assert columns > 2
    key(window, Qt.Key.Key_Down)
    assert window.grid.selected_indices() == [columns]
    key(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ControlModifier)  # moves, keeps
    key(window, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier)
    assert window.grid.selected_indices() == [columns, columns + 1]
    key(window, Qt.Key.Key_End)
    assert window.grid.selected_indices() == [11]
    key(window, Qt.Key.Key_Home, Qt.KeyboardModifier.ShiftModifier)
    assert window.grid.selected_indices() == list(range(12))


def test_clicks_and_dragging_pages_to_a_new_place(tmp_path):
    window = open_editor([make_pdf(tmp_path / "a.pdf", 5)])
    grid = window.grid
    mouse(grid, "press", center(window, 1))
    mouse(grid, "release", center(window, 1))
    mouse(grid, "press", center(window, 3), Qt.KeyboardModifier.ControlModifier)
    mouse(grid, "release", center(window, 3), Qt.KeyboardModifier.ControlModifier)
    assert grid.selected_indices() == [1, 3]
    # Drag both to the very start: they keep their order.
    x, y, _w, _h = grid.layout_.box(0)
    mouse(grid, "press", center(window, 3))
    mouse(grid, "move", center(window, 3) + QPointF(-30, 0))
    mouse(grid, "move", QPointF(x + 5, y + 20))
    assert grid.marker is not None and grid.marker[0] == 0
    mouse(grid, "release", QPointF(x + 5, y + 20))
    assert numbers(window) == [1, 3, 0, 2, 4]
    assert grid.marker is None and grid.selected_indices() == [0, 1]
    # A click on a selected page (no drag) selects only it; Shift+click a run.
    mouse(grid, "press", center(window, 1))
    mouse(grid, "release", center(window, 1))
    assert grid.selected_indices() == [1]
    mouse(grid, "press", center(window, 3), Qt.KeyboardModifier.ShiftModifier)
    mouse(grid, "release", center(window, 3), Qt.KeyboardModifier.ShiftModifier)
    assert grid.selected_indices() == [1, 2, 3]
    window.undo()
    assert numbers(window) == [0, 1, 2, 3, 4]


def test_a_click_between_pages_clears_the_selection(tmp_path):
    window = open_editor([make_pdf(tmp_path / "a.pdf", 3)])
    grid = window.grid
    x, y, w, h = grid.layout_.picture(0)
    between = QPointF(x + w + grid.layout_.gap / 2 + 2, y + h / 2)
    assert grid.layout_.index_at(between.x(), between.y()) is None
    mouse(grid, "press", between)
    mouse(grid, "release", between)
    assert grid.selected == set()


def test_moving_pages_shows_a_copy_next_to_the_pointer(tmp_path):
    window = open_editor([make_pdf(tmp_path / "a.pdf", 4)])
    grid = window.grid
    window.grid.set_pages(window.pages, select=[0, 1], current=1)
    painted = []
    original = grid._paint_ghost
    grid._paint_ghost = lambda painter: (painted.append(len(grid.selected)), original(painter))
    mouse(grid, "press", center(window, 1))
    mouse(grid, "move", center(window, 1) + QPointF(30, 0))
    mouse(grid, "move", center(window, 3))
    grid.repaint()
    assert painted and painted[-1] == 2
    mouse(grid, "release", center(window, 3))
    painted.clear()
    grid.repaint()
    assert not painted  # gone once the pages are dropped


def test_dragging_on_empty_space_selects_what_it_touches(tmp_path):
    window = open_editor([make_pdf(tmp_path / "a.pdf", 4)])
    grid = window.grid
    x, y, _w, _h = grid.layout_.box(0)
    start = QPointF(x - 8, y - 8)  # the margin before the first page
    mouse(grid, "press", start)
    assert grid.selected == set()
    second_x, second_y, _w, _h = grid.layout_.picture(1)
    end = QPointF(second_x + 10, second_y + 10)  # into the second page
    mouse(grid, "move", end)
    mouse(grid, "release", end)
    assert grid.selected_indices() == [0, 1]
    assert window.history.dirty is False  # selecting is not an edit


def test_dropped_pdfs_and_pictures_are_added_where_dropped(tmp_path):
    window = open_editor([make_pdf(tmp_path / "a.pdf", 3)])
    other = make_pdf(tmp_path / "b.pdf", 2, "B")
    Image.new("RGB", (120, 80), (10, 120, 30)).save(tmp_path / "pic.jpg")
    (tmp_path / "notes.txt").write_text("hello")
    drop_files(window.grid, [other, tmp_path / "pic.jpg", tmp_path / "notes.txt"],
               center(window, 1) + QPointF(-20, 0))  # fmt: skip
    pages = window.pages
    assert [(p.source, p.number) for p in pages] == [(0, 0), (1, 0), (1, 1), (2, 0), (0, 1),
                                                     (0, 2)]  # fmt: skip
    assert window.grid.selected_indices() == [1, 2, 3]  # the new pages
    assert window.warnings == [("Some files could not be added",
                                ["notes.txt: only PDFs and pictures can be added"])]  # fmt: skip
    snap = emitted(window, window.save)
    assert texts(snap.outputs[0]) == ["Page 1", "B 1", "B 2", "", "Page 2", "Page 3"]
    assert other.exists() and (tmp_path / "pic.jpg").exists()
    window.undo()  # the added pages go, the files stay readable for redo
    assert len(window.pages) == 3


def test_add_pages_dialog_inserts_after_the_selection(tmp_path):
    window = open_editor([make_pdf(tmp_path / "a.pdf", 3)])
    other = make_pdf(tmp_path / "b.pdf", 1, "B")
    window.choose_files = lambda: [other]
    key(window, Qt.Key.Key_Right)
    QTest.keySequence(window, "Ctrl+O")
    assert [(p.source, p.number) for p in window.pages] == [(0, 0), (0, 1), (1, 0), (0, 2)]


def test_context_menu_follows_the_selection(tmp_path):
    window = open_editor([make_pdf(tmp_path / "a.pdf", 2)])
    menu = window.context_menu()
    enabled = {a.text(): a.isEnabled() for a in menu.actions() if a.text()}
    assert enabled["Delete"] and enabled["Save Selection…"] and enabled["Blank Page After"]
    window.grid.set_pages(window.pages, select=[])
    enabled = {a.text(): a.isEnabled() for a in window.context_menu().actions() if a.text()}
    assert not enabled["Delete"] and enabled["Blank Page After"]
    assert not window.organize_part.delete_button.isEnabled()
    assert not window.selection_button.isEnabled()


# --- saving -----------------------------------------------------------------------------------


def test_save_selection_keeps_the_editor_open(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", 4)
    window = open_editor([src])
    window.grid.set_pages(window.pages, select=[3, 1])
    key(window, Qt.Key.Key_R)
    snap = emitted(window, window.save_selection)
    assert snap.outputs[0] == tmp_path / "a-pages.pdf"
    assert texts(snap.outputs[0]) == ["Page 2", "Page 4"]
    with pymupdf.open(snap.outputs[0]) as doc:
        assert [p.rotation for p in doc] == [90, 90]
    assert window.isVisible()


def test_split_every_selected_and_ranges(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", 5)
    window = open_editor([src])
    QTest.keySequence(window, "Ctrl+2")
    split = window.split_part
    assert window.mode is split.mode
    split.method_combo.setCurrentIndex(split.method_combo.findData("every"))
    split.every_spin.setValue(2)
    assert split.label.text() == "3 parts" and split.save_button.isEnabled()
    assert window.grid.overlay.labels == {0: "Part 1", 2: "Part 2", 4: "Part 3"}
    snap = emitted(window, window.save_parts)
    folder = snap.outputs[0]
    assert folder == tmp_path / "a-parts"
    assert sorted(p.name for p in folder.iterdir()) == [
        "a-parts-01.pdf", "a-parts-02.pdf", "a-parts-03.pdf",
    ]  # fmt: skip
    assert texts(folder / "a-parts-02.pdf") == ["Page 3", "Page 4"]

    split.method_combo.setCurrentIndex(split.method_combo.findData("selected"))
    window.grid.set_pages(window.pages, select=[0])
    assert not split.save_button.isEnabled()
    window.grid.set_pages(window.pages, select=[3])
    assert split.label.text() == "2 parts"

    split.method_combo.setCurrentIndex(split.method_combo.findData("ranges"))
    split.ranges_edit.setText("2-9")
    assert split.label.text() == "There is no page 9 (5 pages)"
    assert not split.save_button.isEnabled()
    split.ranges_edit.setText("4-, 1")
    assert window.grid.overlay.dimmed == frozenset({1, 2})
    snap = emitted(window, window.save_parts)
    assert snap.outputs[0].name == "a-parts (1)"  # a taken name; the files keep the chosen one
    assert texts(snap.outputs[0] / "a-parts-01.pdf") == ["Page 4", "Page 5"]
    QTest.keySequence(window, "Ctrl+1")
    assert window.grid.overlay is None


def test_enter_in_the_ranges_field_does_not_save(tmp_path):
    window = open_editor([make_pdf(tmp_path / "a.pdf", 3)])
    window.set_mode("split")
    split = window.split_part
    split.method_combo.setCurrentIndex(split.method_combo.findData("ranges"))
    split.ranges_edit.setFocus()
    QTest.keyClicks(split.ranges_edit, "1-2")
    QTest.keyClick(split.ranges_edit, Qt.Key.Key_R)  # typed, not a turn
    QTest.keyClick(split.ranges_edit, Qt.Key.Key_Return)
    assert split.ranges_edit.text() == "1-2r"
    assert window.isVisible() and all(p.turns == 0 for p in window.pages)


def test_zoom_is_remembered(tmp_path):
    window = open_editor([make_pdf(tmp_path / "a.pdf", 2)])
    window.zoom.setValue(300)
    assert window.grid.cell == 300
    window.close()
    again = open_editor([tmp_path / "a.pdf"], cell=None)
    assert again.zoom.value() == 300


def test_page_tooltips_name_the_source(tmp_path):
    from quince.ui.editors.pdf._grid import describe

    window = open_editor([make_pdf(tmp_path / "a.pdf", 2)])
    key(window, Qt.Key.Key_R)
    assert describe(window.pages[0], window.sources) == "Page 1 of a.pdf · turned 90°"
    key(window, Qt.Key.Key_B)
    assert describe(window.pages[1], window.sources) == "Blank page"


def test_save_names_and_folder_follow_the_output_settings(tmp_path):
    from quince.core.config import OutputConfig, OutputLocation

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    window = open_editor([make_pdf(tmp_path / "a.pdf", 2)])
    window.output_config = lambda: OutputConfig(
        OutputLocation.FOLDER, out_dir, {"edited": "changed", "pages": "some"}
    )
    assert window.default_stem() == "a-changed"
    assert window.suffixed("pages") == "a-some"
    assert window.suffixed("parts") == "a-parts"
    snap = emitted(window, window.save)
    assert snap.outputs == (out_dir / "a-changed.pdf",)
