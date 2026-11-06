"""The editor framework, the Save window and the Image Editor, driven like a user would
(mouse, keys, controls), saving through the real job queue."""

from __future__ import annotations

from pathlib import Path

import pytest
import shiboken6
from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from quince.backends import imageedit
from quince.core.jobs import JobError, JobQueue, JobState
from quince.core.operations import Accepts, JobRequest, Kind, Operation
from quince.core.registry import discover as discover_operations
from quince.core.selection import Selection
from quince.ui.editors import manager
from quince.ui.editors.base import History
from quince.ui.editors.image.collage import CollageEditor
from quince.ui.editors.image.editor import ImageEditorWindow
from quince.ui.editors.save_dialog import OutputChoice, SaveDialog
from quince.ui.runner import OperationRunner
from tests.unit.test_jobs import wait_finished

MAKE = 0x010F


_OPEN: list = []


@pytest.fixture(autouse=True)
def _private_options(tmp_path, monkeypatch):
    """Editors remember settings; never in the user's state folder."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.fixture(autouse=True)
def _close_editors(qapp):
    """Editor windows delete themselves when closed; close the ones still open."""
    yield
    while _OPEN:
        window = _OPEN.pop()
        if shiboken6.isValid(window):
            window.confirm_discard = lambda: True
            window.close()
    QApplication.processEvents()


def photo(path: Path, size=(400, 300), color=(40, 120, 200), **save) -> Path:
    Image.new("RGB", size, color).save(path, **save)
    return path


def open_editor(qtbot, cls, paths):
    selection = Selection.from_paths(paths)
    window = cls(selection, cls.load(selection))
    window.confirm_discard = lambda: True
    window.run_save_dialog = lambda dialog: dialog.target()  # Save with the defaults
    _OPEN.append(window)
    window.resize(1000, 700)
    window.show()
    QApplication.processEvents()
    return window


def settle():
    """Let debounced previews refresh."""
    QTest.qWait(60)


def run_request(window, request: JobRequest):
    queue = JobQueue(workers=1)
    try:
        snap = wait_finished(queue, queue.submit(request.run, window.selection, title="t"), 30)
    finally:
        queue.shutdown()
    assert snap.state is JobState.SUCCEEDED, snap.error or snap.failures
    return snap


def saved_output(window, **choose):
    """Press Save (``choose``: format_id / stem / folder picked in the Save window), run the
    job the editor emits; the snapshot."""
    if choose:

        def pick(dialog):
            if "format_id" in choose:
                dialog.format_combo.setCurrentIndex(
                    dialog.format_combo.findData(choose["format_id"])
                )
            if "stem" in choose:
                dialog.name_edit.setText(choose["stem"])
            if "folder" in choose:
                dialog.folder = choose["folder"]
            return dialog.target()

        window.run_save_dialog = pick
    requests = []
    window.saved.connect(requests.append)
    window.save()
    assert len(requests) == 1, "Save did not emit a job"
    return run_request(window, requests[0])


def mouse(canvas, kind, point, buttons=Qt.MouseButton.LeftButton, modifiers=None):
    widget = canvas.to_widget(QPointF(*point))
    types = {
        "press": QEvent.Type.MouseButtonPress,
        "move": QEvent.Type.MouseMove,
        "release": QEvent.Type.MouseButtonRelease,
        "double": QEvent.Type.MouseButtonDblClick,
    }
    button = Qt.MouseButton.NoButton if kind == "move" else Qt.MouseButton.LeftButton
    held = Qt.MouseButton.NoButton if kind == "release" else buttons
    event = QMouseEvent(
        types[kind],
        widget,
        canvas.mapToGlobal(widget),
        button,
        held,
        modifiers or Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(canvas, event)


def drag(canvas, a, b, modifiers=None, steps=4):
    mouse(canvas, "press", a, modifiers=modifiers)
    for n in range(1, steps + 1):
        t = n / steps
        mouse(
            canvas,
            "move",
            (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t),
            modifiers=modifiers,
        )
    mouse(canvas, "release", b, modifiers=modifiers)


# --- History ----------------------------------------------------------------------------------


def test_history_undo_redo_and_merge():
    now = [0.0]
    history = History("a", clock=lambda: now[0])
    assert not history.dirty and not history.can_undo
    assert history.push("b")
    assert not history.push("b")  # unchanged
    history.push("c", merge="slider")
    now[0] += 0.1
    history.push("d", merge="slider")  # one step with "c"
    assert history.current == "d" and history.undo() == "b"
    assert history.redo() == "d" and not history.can_redo
    now[0] += 5
    history.push("e", merge="slider")  # too late to merge
    assert history.undo() == "d"
    history.push("x")  # a new change drops the redo steps
    assert not history.can_redo and history.dirty
    assert history.undo() == "d" and history.undo() == "b" and history.undo() == "a"
    assert history.undo() is None and not history.dirty


def test_history_merge_back_to_start_is_no_step():
    now = [0.0]
    history = History(0, clock=lambda: now[0])
    history.push(1, merge="s")
    history.push(0, merge="s")
    assert not history.can_undo and history.current == 0


# --- discovery, manager, runner -----------------------------------------------------------------


def test_every_editor_operation_has_its_editor():
    editors = manager.discover()
    assert set(editors) >= {"image.edit", "image.collage", "video.edit", "audio.edit"}
    named = {op.editor for op in discover_operations().operations(Kind.TOOL) if op.editor}
    assert named <= set(editors)


def _editor_op(editor="image.edit"):
    return Operation("tool.x", Kind.TOOL, "X", "", Accepts(), editor=editor)


def test_manager_opens_and_submits(qtbot, tmp_path):
    src = photo(tmp_path / "a.png")
    submitted, errors = [], []
    mgr = manager.EditorManager(
        lambda op, sel, req: submitted.append((op.id, req)),
        notify=lambda _t: None,
        notify_error=errors.append,
        compute=lambda work, on_slow=None: work(),
    )
    window = mgr.open(_editor_op(), Selection.from_paths([src]))
    _OPEN.append(window)
    assert isinstance(window, ImageEditorWindow) and window.isVisible() and mgr.windows == [window]
    window.run_save_dialog = lambda dialog: dialog.target()
    window.save()
    assert [op_id for op_id, _r in submitted] == ["tool.x"]
    assert not errors


def test_manager_explains_failures(qtbot, tmp_path):
    errors = []

    def failing(work, on_slow=None):
        raise JobError("Not a readable PNG image")

    mgr = manager.EditorManager(
        lambda *a: None, notify=lambda _t: None, notify_error=errors.append, compute=failing
    )
    assert mgr.open(_editor_op(), Selection.from_paths([tmp_path / "x.png"])) is None
    assert mgr.open(_editor_op("nope"), Selection()) is None
    assert errors == ["X: Not a readable PNG image", "X: the editor is missing"]


def test_runner_opens_editor_after_the_drag(tmp_path):
    opened, later = [], []
    host = type("H", (), {"notify": lambda self, t: None})()
    runner = OperationRunner(
        None, host, after_drag=later.append, open_editor=lambda o, s: opened.append(o.id)
    )
    runner.run(_editor_op(), Selection.from_paths([tmp_path]))
    assert opened == [] and len(later) == 1  # waits until the drag is over
    later[0]()
    assert opened == ["tool.x"]


# --- the Save window ----------------------------------------------------------------------------


def test_save_dialog_name_format_folder_and_free_names(qtbot, tmp_path):
    src = photo(tmp_path / "a.jpg")
    (tmp_path / "a-edited.png").touch()
    other = tmp_path / "out"
    other.mkdir()
    choices = [OutputChoice("jpg"), OutputChoice("png", note="Lossless")]
    dialog = SaveDialog(
        "Save", src, choices, format_id="png", stem="a-edited", folder=tmp_path,
        choose_folder=lambda start: other,
    )  # fmt: skip
    assert dialog.extension_label.text() == ".png" and dialog.note_label.text() == "Lossless"
    assert [dialog.format_combo.itemText(i) for i in range(2)] == ["JPG", "PNG"]  # names only
    assert "a-edited (1).png" in dialog.result_label.text()  # never overwritten
    dialog.format_combo.setCurrentIndex(0)
    assert dialog.extension_label.text() == ".jpg" and "Saved as" in dialog.result_label.text()
    dialog.change_folder()
    dialog.name_edit.setText("  holiday/1 ")
    target = dialog.target()
    assert (target.folder, target.stem, target.format_id) == (other, "holiday-1", "jpg")
    dialog.name_edit.setText("   ")
    assert not dialog.save_button.isEnabled()
    dialog.deleteLater()


# --- shared window behavior ---------------------------------------------------------------------


def test_enter_opens_save_escape_asks_before_discarding(qtbot, tmp_path):
    window = open_editor(qtbot, ImageEditorWindow, [photo(tmp_path / "a.png")])
    assert window.save_button.isEnabled()  # even unchanged: another format or place
    window.adjust_part.rotate(1)
    asked = []
    window.confirm_discard = lambda: asked.append(True) or False
    QTest.keyClick(window.canvas, Qt.Key.Key_Escape)
    assert asked and window.isVisible()  # "Cancel" in the question keeps it open
    requests, dialogs = [], []
    window.saved.connect(requests.append)
    window.run_save_dialog = lambda d: dialogs.append(d.target()) and None  # cancelled
    QTest.keyClick(window.canvas, Qt.Key.Key_Return)
    assert len(dialogs) == 1 and not requests and window.isVisible()
    assert dialogs[0].stem == "a-edited" and dialogs[0].format_id == "png"
    window.run_save_dialog = lambda d: d.target()
    QTest.keyClick(window.canvas, Qt.Key.Key_Return)
    assert len(requests) == 1 and not window.isVisible()


def test_enter_in_a_number_field_does_not_save(qtbot, tmp_path):
    window = open_editor(qtbot, ImageEditorWindow, [photo(tmp_path / "a.png")])
    requests = []
    window.saved.connect(requests.append)
    spin = window.crop_part.width_spin
    spin.setFocus()
    spin.lineEdit().selectAll()
    QTest.keyClicks(spin.lineEdit(), "200")
    QTest.keyClick(spin.lineEdit(), Qt.Key.Key_Return)
    assert not requests and window.isVisible()
    assert window.state().crop == (100, 0, 300, 300)  # 200 wide, around the middle


def test_modes_keys_and_one_undo_history(qtbot, tmp_path):
    window = open_editor(qtbot, ImageEditorWindow, [photo(tmp_path / "a.png")])
    assert window.mode.key == "crop"
    QTest.keySequence(window, "Ctrl+2")
    assert window.mode.key == "adjust" and window.side_area.isVisible()
    QTest.keyClick(window, Qt.Key.Key_H)  # Mirror, in Adjust
    assert window.state().adjust.flip_h
    QTest.keySequence(window, "Ctrl+4")
    assert window.mode.key == "annotate" and not window.side_area.isVisible()
    QTest.keyClick(window, Qt.Key.Key_H)  # Highlighter, in Annotate
    assert window.annotate_part.tool_name == "highlight" and window.state().adjust.flip_h
    drag(window.canvas, (50, 50), (150, 120))
    assert len(window.state().shapes) == 1
    QTest.keySequence(window, "Ctrl+Z")  # undoes the drawing, then the mirror
    assert not window.state().shapes and window.state().adjust.flip_h
    QTest.keySequence(window, "Ctrl+Z")
    assert not window.state().adjust.flip_h
    QTest.keySequence(window, "Ctrl+Shift+Z")
    assert window.state().adjust.flip_h


def test_compare_shows_original(qtbot, tmp_path):
    window = open_editor(qtbot, ImageEditorWindow, [photo(tmp_path / "a.png")])
    window.set_mode("adjust")
    window.adjust_part.rotate(1)
    settle()
    assert window.canvas.logical_size.width() == 300
    window.compare_button.toggle()
    assert window.canvas.comparing and window.canvas.logical_size.width() == 400
    window.compare_button.toggle()
    assert not window.canvas.comparing


# --- Crop ---------------------------------------------------------------------------------------


def test_crop_by_dragging_and_saving(qtbot, tmp_path):
    window = open_editor(qtbot, ImageEditorWindow, [photo(tmp_path / "a.jpg", quality=95)])
    drag(window.canvas, (50, 40), (250, 190))
    box = window.state().crop
    assert box == pytest.approx((50, 40, 250, 190), abs=2)
    drag(window.canvas, (150, 100), (180, 120))  # inside: moves it
    moved = window.state().crop
    assert moved[0] == pytest.approx(box[0] + 30, abs=2)
    (out,) = saved_output(window).outputs
    assert out.name == "a-edited.jpg"
    with Image.open(out) as im:
        assert im.size == (moved[2] - moved[0], moved[3] - moved[1])


def test_crop_ratio_shift_keys_and_whole_picture(qtbot, tmp_path):
    window = open_editor(qtbot, ImageEditorWindow, [photo(tmp_path / "a.png")])
    part = window.crop_part
    part.ratio_combo.setCurrentIndex(part.ratio_combo.findData("1:1"))
    x0, y0, x1, y1 = window.state().crop
    assert (x1 - x0, y1 - y0) == (300, 300)
    part.ratio_combo.setCurrentIndex(part.ratio_combo.findData("free"))
    drag(window.canvas, (10, 10), (200, 60), modifiers=Qt.KeyboardModifier.ShiftModifier)
    x0, y0, x1, y1 = window.state().crop
    assert x1 - x0 == pytest.approx(y1 - y0, abs=2)  # Shift: a square
    window.canvas.setFocus()
    QTest.keyClick(window.canvas, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert window.state().crop[0] == x0 + 10
    window.undo()
    assert window.state().crop[0] == x0
    part.reset_button.click()
    assert window.state().crop is None


def test_crop_click_without_drag_keeps_the_box(qtbot, tmp_path):
    window = open_editor(qtbot, ImageEditorWindow, [photo(tmp_path / "a.png")])
    window.crop_part.set_box((100.0, 100.0, 200.0, 200.0))
    window.crop_part.commit()
    mouse(window.canvas, "press", (20, 20))
    mouse(window.canvas, "release", (20, 20))
    assert window.state().crop == (100, 100, 200, 200)


def test_crop_box_is_shown_on_the_turned_picture(qtbot, tmp_path):
    window = open_editor(qtbot, ImageEditorWindow, [photo(tmp_path / "a.png")])
    window.crop_part.set_box((0.0, 0.0, 100.0, 50.0))
    window.crop_part.commit()
    window.adjust_part.rotate(1)
    settle()
    assert window.canvas.logical_size.width() == 300  # the turned, uncropped picture
    assert window.crop_part.box == pytest.approx((250, 0, 300, 100))
    assert window.state().crop == (0, 0, 100, 50)  # kept in pixels of the original


# --- Adjust -------------------------------------------------------------------------------------


def test_adjust_turn_resize_colors_and_save(qtbot, tmp_path):
    exif = Image.Exif()
    exif[MAKE] = "Cam"
    src = photo(tmp_path / "a.jpg", color=(100, 100, 100), exif=exif.tobytes())
    window = open_editor(qtbot, ImageEditorWindow, [src])
    window.set_mode("adjust")
    part = window.adjust_part
    QTest.keyClick(window, Qt.Key.Key_BracketRight)  # ] turns right
    assert window.state().adjust.rotate == 1
    assert (part.width_spin.value(), part.height_spin.value()) == (300, 400)
    part.width_spin.setValue(150)  # proportions kept
    assert window.state().adjust.size == (150, 200)
    part.sliders["brightness"].slider.setValue(50)
    assert window.state().adjust.brightness == 50
    settle()
    assert "150 × 200" in window.info.text()
    (out,) = saved_output(window).outputs
    assert out.name == "a-edited.jpg"
    with Image.open(out) as im:
        assert im.size == (150, 200)
        assert im.getpixel((75, 100))[0] > 130  # brighter
        assert im.getexif().get(MAKE) == "Cam"


def test_a_chosen_size_follows_a_new_crop(qtbot, tmp_path):
    window = open_editor(qtbot, ImageEditorWindow, [photo(tmp_path / "a.png")])
    window.adjust_part.width_spin.setValue(200)  # half size: 200 x 150
    window.crop_part.set_box((0.0, 0.0, 200.0, 300.0))
    window.crop_part.commit()
    assert window.state().adjust.size == (100, 150)
    window.adjust_part.reset()
    assert window.state().adjust == imageedit.Adjust()


# --- Redact -------------------------------------------------------------------------------------


def test_redact_areas_styles_and_no_exif(qtbot, tmp_path):
    exif = Image.Exif()
    exif[MAKE] = "Cam"
    src = photo(tmp_path / "a.jpg", color=(250, 250, 250), exif=exif.tobytes(), quality=95)
    window = open_editor(qtbot, ImageEditorWindow, [src])
    window.set_mode("redact")
    part = window.redact_part
    drag(window.canvas, (20, 20), (120, 80))
    assert [i.style for i in window.state().redactions] == ["solid"]
    QTest.keyClick(window, Qt.Key.Key_2)  # Blur, for the picked area
    assert [i.style for i in window.state().redactions] == ["blur"]
    part.styles.buttons()[0].click()
    drag(window.canvas, (200, 150), (300, 250))
    assert len(window.state().redactions) == 2
    QTest.keyClick(window.canvas, Qt.Key.Key_Delete)  # the new one is picked
    assert len(window.state().redactions) == 1
    part.styles.buttons()[0].click()  # back to a black box
    snap = saved_output(window)
    (out,) = snap.outputs
    with Image.open(out) as im:
        assert max(im.getpixel((60, 50))) < 20
        assert min(im.getpixel((300, 200))) > 230
        assert MAKE not in im.getexif()
    assert any("EXIF" in note for note in snap.notes)


def test_redacted_area_stays_on_the_content_when_turned(qtbot, tmp_path):
    window = open_editor(qtbot, ImageEditorWindow, [photo(tmp_path / "a.png")])
    window.set_mode("redact")
    drag(window.canvas, (0, 0), (40, 30))
    window.adjust_part.rotate(1)  # 300 x 400 now; the top-left corner is at the top right
    (item,) = window.redact_part.items
    assert item.box == pytest.approx((270, 0, 300, 40), abs=1)
    (out,) = saved_output(window).outputs
    with Image.open(out) as im:
        assert im.size == (300, 400)
        assert max(im.getpixel((290, 10))[:3]) < 20 and im.getpixel((10, 10))[2] > 150


# --- Annotate -----------------------------------------------------------------------------------


def test_annotate_draw_select_delete_and_save(qtbot, tmp_path):
    src = photo(tmp_path / "a.png", color=(255, 255, 255))
    window = open_editor(qtbot, ImageEditorWindow, [src])
    window.set_mode("annotate")
    part = window.annotate_part
    part.set_tool("rect")
    drag(window.canvas, (50, 50), (150, 120))
    part.set_tool("arrow")
    drag(window.canvas, (200, 200), (350, 250))
    assert [s.kind for s in window.state().shapes] == ["rect", "arrow"]
    mouse(window.canvas, "press", (60, 60))  # a click is not a shape
    mouse(window.canvas, "release", (60, 60))
    assert len(window.state().shapes) == 2
    part.set_tool("select")
    mouse(window.canvas, "press", (50, 85))  # on the box's left edge
    mouse(window.canvas, "release", (50, 85))
    assert part.selected == 0
    QTest.keyClick(window.canvas, Qt.Key.Key_Delete)
    assert [s.kind for s in window.state().shapes] == ["arrow"]
    window.undo()
    assert len(window.state().shapes) == 2
    (out,) = saved_output(window).outputs
    with Image.open(out) as im:
        assert im.getpixel((50, 85))[1] < 200  # the red box


def test_annotate_text_entry(qtbot, tmp_path):
    window = open_editor(qtbot, ImageEditorWindow, [photo(tmp_path / "a.png")])
    window.set_mode("annotate")
    part = window.annotate_part
    part.set_tool("text")
    mouse(window.canvas, "press", (40, 40))
    assert part.entry is not None
    QTest.keyClicks(part.entry, "Hello")
    requests = []
    window.saved.connect(requests.append)
    QTest.keyClick(part.entry, Qt.Key.Key_Return)  # finishes the text, does not save
    assert not requests and part.entry is None
    (shape,) = window.state().shapes
    assert shape.kind == "text" and shape.text == "Hello"
    assert shape.points[0] == pytest.approx((40.0, 40.0))
    part.edit_text(0)
    part.entry.setText("Changed")
    QTest.keyClick(part.entry, Qt.Key.Key_Escape)  # drops the change
    assert window.state().shapes[0].text == "Hello"
    part.set_tool("text")
    mouse(window.canvas, "press", (200, 200))
    QTest.keyClick(part.entry, Qt.Key.Key_Escape)  # an empty text is no shape
    assert len(window.state().shapes) == 1
    mouse(window.canvas, "press", (100, 200))
    QTest.keyClicks(part.entry, "Kept")
    window.set_mode("frame")  # leaving the mode keeps the text
    assert [s.text for s in window.state().shapes] == ["Hello", "Kept"]


def test_annotate_color_applies_to_the_picked_shape(qtbot, tmp_path):
    window = open_editor(qtbot, ImageEditorWindow, [photo(tmp_path / "a.png")])
    window.set_mode("annotate")
    part = window.annotate_part
    part.set_tool("line")
    drag(window.canvas, (20, 20), (200, 20))
    part.set_tool("select")
    mouse(window.canvas, "press", (100, 20))
    mouse(window.canvas, "release", (100, 20))
    part.swatches.buttons[(30, 136, 229, 255)].click()
    assert window.state().shapes[0].color == (30, 136, 229)
    window.undo()
    assert window.state().shapes[0].color == (229, 57, 53)


# --- Frame --------------------------------------------------------------------------------------


def test_frame_is_off_until_added_and_settings_are_remembered(qtbot, tmp_path):
    window = open_editor(qtbot, ImageEditorWindow, [photo(tmp_path / "a.png")])
    window.set_mode("frame")
    part = window.frame_part
    assert window.state().frame is None
    part.padding.slider.setValue(10)  # changing a setting adds the frame
    part.background.buttons[(0, 0, 0, 255)].click()
    assert window.state().frame.padding == pytest.approx(0.1)
    (out,) = saved_output(window).outputs
    with Image.open(out) as im:
        assert im.size == (460, 360)
        assert im.convert("RGB").getpixel((2, 2)) == (0, 0, 0)
    again = open_editor(qtbot, ImageEditorWindow, [photo(tmp_path / "b.png")])
    again.frame_part.enabled_box.setChecked(True)
    assert again.state().frame.padding == pytest.approx(0.1)
    assert again.state().frame.background == (0, 0, 0, 255)


def test_transparent_result_defaults_to_png_and_jpg_gets_white(qtbot, tmp_path):
    window = open_editor(qtbot, ImageEditorWindow, [photo(tmp_path / "a.jpg")])
    window.frame_part.enabled_box.setChecked(True)
    window.frame_part.background.buttons[(0, 0, 0, 0)].click()
    seen = []
    window.run_save_dialog = lambda d: seen.append(d) and None
    window.save()
    dialog = seen[0]
    assert dialog.format_id == "png"
    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData("jpg"))
    assert "white" in dialog.note_label.text()
    snap = saved_output(window, format_id="jpg", stem="framed")
    (out,) = snap.outputs
    assert out.name == "framed.jpg" and any("white" in n for n in snap.notes)


# --- everything at once -------------------------------------------------------------------------


def test_every_mode_in_one_file_with_a_chosen_name_format_and_folder(qtbot, tmp_path):
    src = photo(tmp_path / "a.png", color=(255, 255, 255))
    folder = tmp_path / "out"
    folder.mkdir()
    window = open_editor(qtbot, ImageEditorWindow, [src])
    window.crop_part.set_box((0.0, 0.0, 200.0, 300.0))
    window.crop_part.commit()
    window.set_mode("adjust")
    window.adjust_part.rotate(1)  # 300 x 200
    window.set_mode("redact")
    drag(window.canvas, (0, 0), (50, 50))
    window.set_mode("annotate")
    window.annotate_part.set_tool("rect")
    drag(window.canvas, (150, 100), (250, 180))
    window.set_mode("frame")
    window.frame_part.enabled_box.setChecked(True)
    snap = saved_output(window, format_id="webp", stem="all", folder=folder)
    (out,) = snap.outputs
    assert out == folder / "all.webp"
    with Image.open(out) as im:
        pad = round(0.06 * 200)
        assert im.size == (300 + 2 * pad, 200 + 2 * pad)
        assert max(im.convert("RGB").getpixel((pad + 20, pad + 20))) < 30  # redacted


# --- Collage ------------------------------------------------------------------------------------


def test_collage_layout_order_and_save(qtbot, tmp_path):
    paths = [
        photo(tmp_path / "p2.jpg", color=(255, 0, 0)),
        photo(tmp_path / "p10.jpg", size=(300, 400), color=(0, 0, 255)),
        photo(tmp_path / "p1.jpg", color=(0, 255, 0)),
    ]
    window = open_editor(qtbot, CollageEditor, paths)
    names = [window.list.item(r).text() for r in range(window.list.count())]
    assert names == ["p1.jpg", "p2.jpg", "p10.jpg"]  # natural order
    assert window.save_button.isEnabled()
    window.layout_combo.setCurrentIndex(window.layout_combo.findData("row"))
    window.gap.slider.setValue(0)
    window.list.setCurrentRow(0)
    window.move_current(1)
    assert [window.list.item(r).text() for r in range(3)] == ["p2.jpg", "p1.jpg", "p10.jpg"]
    window.undo()
    assert [window.list.item(r).text() for r in range(3)] == ["p1.jpg", "p2.jpg", "p10.jpg"]
    width = window.width_spin.value()
    (out,) = saved_output(window).outputs
    assert out.name == "p1-collage.jpg"
    with Image.open(out) as im:
        assert im.width == width
        assert im.getpixel((5, im.height // 2))[1] > 200  # p1 (green) comes first


def test_collage_needs_two_images(tmp_path):
    with pytest.raises(JobError):
        CollageEditor.load(Selection.from_paths([photo(tmp_path / "a.png")]))


def test_widget_methods_are_not_shadowed(qtbot, tmp_path):
    """Editors must not hide QWidget.size() / style() / width() behind attributes."""
    window = open_editor(qtbot, ImageEditorWindow, [photo(tmp_path / "a.png")])
    assert window.size().width() == 1000 and window.width() == 1000
    assert window.style() is not None
    assert isinstance(window.pos(), QPoint)
