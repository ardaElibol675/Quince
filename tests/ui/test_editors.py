"""The editor framework and the image editors, driven like a user would (mouse, keys,
controls), saving through the real job queue."""

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
from quince.ui.editors.image.annotate import AnnotateEditor
from quince.ui.editors.image.collage import CollageEditor
from quince.ui.editors.image.crop import CropEditor
from quince.ui.editors.image.edit import EditEditor
from quince.ui.editors.image.frame import FrameEditor
from quince.ui.editors.image.redact import RedactEditor
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


def saved_output(window):
    """Press Save and run the job the editor emits; the snapshot."""
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
    assert set(editors) >= {
        "image.crop", "image.edit", "image.frame", "image.annotate", "image.redact",
        "image.collage",
    }  # fmt: skip
    named = {op.editor for op in discover_operations().operations(Kind.TOOL) if op.editor}
    assert named <= set(editors)


def _editor_op(editor="image.crop"):
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
    assert isinstance(window, CropEditor) and window.isVisible() and mgr.windows == [window]
    window.set_box((0.0, 0.0, 100.0, 100.0))
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


# --- shared window behavior ---------------------------------------------------------------------


def test_enter_saves_escape_asks_before_discarding(qtbot, tmp_path):
    window = open_editor(qtbot, CropEditor, [photo(tmp_path / "a.png")])
    assert not window.save_button.isEnabled()  # nothing changed yet
    window.set_box((10.0, 10.0, 110.0, 60.0))
    window.commit()
    asked = []
    window.confirm_discard = lambda: asked.append(True) or False
    QTest.keyClick(window.canvas, Qt.Key.Key_Escape)
    assert asked and window.isVisible()  # "Cancel" in the question keeps it open
    requests = []
    window.saved.connect(requests.append)
    QTest.keyClick(window.canvas, Qt.Key.Key_Return)
    assert len(requests) == 1 and not window.isVisible()


def test_enter_in_a_number_field_does_not_save(qtbot, tmp_path):
    window = open_editor(qtbot, CropEditor, [photo(tmp_path / "a.png")])
    requests = []
    window.saved.connect(requests.append)
    window.width_spin.setFocus()
    window.width_spin.lineEdit().selectAll()
    QTest.keyClicks(window.width_spin.lineEdit(), "200")
    QTest.keyClick(window.width_spin.lineEdit(), Qt.Key.Key_Return)
    assert not requests and window.isVisible()
    assert window.state()[0] == (100, 0, 300, 300)  # 200 wide, around the middle


def test_undo_redo_shortcuts(qtbot, tmp_path):
    window = open_editor(qtbot, EditEditor, [photo(tmp_path / "a.png")])
    window.rotate(1)
    window.flip_h()
    QTest.keySequence(window, "Ctrl+Z")
    assert window.state().rotate == 1 and not window.state().flip_h
    QTest.keySequence(window, "Ctrl+Shift+Z")
    assert window.state().flip_h


def test_compare_shows_original(qtbot, tmp_path):
    window = open_editor(qtbot, EditEditor, [photo(tmp_path / "a.png")])
    window.rotate(1)
    settle()
    assert window.canvas.logical_size.width() == 300
    window.compare_button.toggle()
    assert window.canvas.comparing and window.canvas.logical_size.width() == 400
    window.compare_button.toggle()
    assert not window.canvas.comparing


# --- Crop ---------------------------------------------------------------------------------------


def test_crop_by_dragging_and_saving(qtbot, tmp_path):
    window = open_editor(qtbot, CropEditor, [photo(tmp_path / "a.jpg", quality=95)])
    drag(window.canvas, (50, 40), (250, 190))
    box = window.state()[0]
    assert box == pytest.approx((50, 40, 250, 190), abs=2)
    assert window.save_button.isEnabled()
    drag(window.canvas, (150, 100), (180, 120))  # inside: moves it
    moved = window.state()[0]
    assert moved[0] == pytest.approx(box[0] + 30, abs=2)
    snap = saved_output(window)
    (out,) = snap.outputs
    assert out.name == "a-cropped.jpg"
    with Image.open(out) as im:
        assert im.size == (moved[2] - moved[0], moved[3] - moved[1])


def test_crop_ratio_shift_and_keys(qtbot, tmp_path):
    window = open_editor(qtbot, CropEditor, [photo(tmp_path / "a.png")])
    window.ratio_combo.setCurrentIndex(window.ratio_combo.findData("1:1"))
    x0, y0, x1, y1 = window.state()[0]
    assert (x1 - x0, y1 - y0) == (300, 300)
    window.ratio_combo.setCurrentIndex(window.ratio_combo.findData("free"))
    drag(window.canvas, (10, 10), (200, 60), modifiers=Qt.KeyboardModifier.ShiftModifier)
    x0, y0, x1, y1 = window.state()[0]
    assert x1 - x0 == pytest.approx(y1 - y0, abs=2)  # Shift: a square
    window.canvas.setFocus()
    QTest.keyClick(window.canvas, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert window.state()[0][0] == x0 + 10
    window.undo()
    assert window.state()[0][0] == x0


def test_crop_click_without_drag_keeps_the_box(qtbot, tmp_path):
    window = open_editor(qtbot, CropEditor, [photo(tmp_path / "a.png")])
    window.set_box((100.0, 100.0, 200.0, 200.0))
    mouse(window.canvas, "press", (20, 20))
    mouse(window.canvas, "release", (20, 20))
    assert window.state()[0] == (100, 100, 200, 200)


# --- Edit ---------------------------------------------------------------------------------------


def test_edit_turn_resize_colors_and_save(qtbot, tmp_path):
    exif = Image.Exif()
    exif[MAKE] = "Cam"
    src = photo(tmp_path / "a.jpg", color=(100, 100, 100), exif=exif.tobytes())
    window = open_editor(qtbot, EditEditor, [src])
    QTest.keyClick(window, Qt.Key.Key_BracketRight)  # ] turns right
    assert window.state().rotate == 1
    assert (window.width_spin.value(), window.height_spin.value()) == (300, 400)
    window.width_spin.setValue(150)  # proportions kept
    assert window.state().size == (150, 200)
    window.sliders["brightness"].slider.setValue(50)
    assert window.state().brightness == 50
    assert "300 × 400" not in window.info.text() and "150 × 200" in window.info.text()
    (out,) = saved_output(window).outputs
    assert out.name == "a-edited.jpg"
    with Image.open(out) as im:
        assert im.size == (150, 200)
        assert im.getpixel((75, 100))[0] > 130  # brighter
        assert im.getexif().get(MAKE) == "Cam"


def test_edit_reset_returns_to_the_original(qtbot, tmp_path):
    window = open_editor(qtbot, EditEditor, [photo(tmp_path / "a.png")])
    window.flip_v()
    window.reset()
    assert window.state() == imageedit.Adjust() and not window.save_button.isEnabled()


# --- Frame --------------------------------------------------------------------------------------


def test_frame_defaults_can_be_saved_and_settings_are_remembered(qtbot, tmp_path):
    window = open_editor(qtbot, FrameEditor, [photo(tmp_path / "a.png")])
    assert window.save_button.isEnabled()  # the first look is already a frame
    window.padding.slider.setValue(10)
    window.background.buttons[(0, 0, 0, 255)].click()
    (out,) = saved_output(window).outputs
    assert out.name == "a-framed.png"
    with Image.open(out) as im:
        assert im.size == (460, 360)
        assert im.convert("RGB").getpixel((2, 2)) == (0, 0, 0)
    again = open_editor(qtbot, FrameEditor, [photo(tmp_path / "b.png")])
    assert again.state().padding == pytest.approx(0.1)
    assert again.state().background == (0, 0, 0, 255)


def test_frame_transparent_jpg_becomes_png(qtbot, tmp_path):
    window = open_editor(qtbot, FrameEditor, [photo(tmp_path / "a.jpg")])
    window.background.buttons[(0, 0, 0, 0)].click()
    settle()
    assert "PNG" in window.info.text()
    snap = saved_output(window)
    (out,) = snap.outputs
    assert out.name == "a-framed.png" and imageedit.SAVED_AS_PNG_NOTE in snap.notes


# --- Annotate -----------------------------------------------------------------------------------


def test_annotate_draw_select_delete_and_save(qtbot, tmp_path):
    window = open_editor(qtbot, AnnotateEditor, [photo(tmp_path / "a.png", color=(255, 255, 255))])
    window.set_tool("rect")
    drag(window.canvas, (50, 50), (150, 120))
    window.set_tool("arrow")
    drag(window.canvas, (200, 200), (350, 250))
    assert [s.kind for s in window.state()] == ["rect", "arrow"]
    mouse(window.canvas, "press", (60, 60))  # a click is not a shape
    mouse(window.canvas, "release", (60, 60))
    assert len(window.state()) == 2
    window.set_tool("select")
    mouse(window.canvas, "press", (50, 85))  # on the box's left edge
    mouse(window.canvas, "release", (50, 85))
    assert window.selected == 0
    QTest.keyClick(window.canvas, Qt.Key.Key_Delete)
    assert [s.kind for s in window.state()] == ["arrow"]
    window.undo()
    assert len(window.state()) == 2
    (out,) = saved_output(window).outputs
    assert out.name == "a-annotated.png"
    with Image.open(out) as im:
        assert im.getpixel((50, 85))[1] < 200  # the red box


def test_annotate_text_entry(qtbot, tmp_path):
    window = open_editor(qtbot, AnnotateEditor, [photo(tmp_path / "a.png")])
    window.set_tool("text")
    mouse(window.canvas, "press", (40, 40))
    assert window.entry is not None
    QTest.keyClicks(window.entry, "Hello")
    requests = []
    window.saved.connect(requests.append)
    QTest.keyClick(window.entry, Qt.Key.Key_Return)  # finishes the text, does not save
    assert not requests and window.entry is None
    (shape,) = window.state()
    assert shape.kind == "text" and shape.text == "Hello" and shape.points == ((40.0, 40.0),)
    window.edit_text(0)
    window.entry.setText("Changed")
    QTest.keyClick(window.entry, Qt.Key.Key_Escape)  # drops the change
    assert window.state()[0].text == "Hello"
    window.set_tool("text")
    mouse(window.canvas, "press", (200, 200))
    QTest.keyClick(window.entry, Qt.Key.Key_Escape)  # an empty text is no shape
    assert len(window.state()) == 1


def test_annotate_color_applies_to_the_picked_shape(qtbot, tmp_path):
    window = open_editor(qtbot, AnnotateEditor, [photo(tmp_path / "a.png")])
    window.set_tool("line")
    drag(window.canvas, (20, 20), (200, 20))
    window.set_tool("select")
    mouse(window.canvas, "press", (100, 20))
    mouse(window.canvas, "release", (100, 20))
    window.swatches.buttons[(30, 136, 229, 255)].click()
    assert window.state()[0].color == (30, 136, 229)
    window.undo()
    assert window.state()[0].color == (229, 57, 53)


# --- Redact -------------------------------------------------------------------------------------


def test_redact_areas_styles_and_no_exif(qtbot, tmp_path):
    exif = Image.Exif()
    exif[MAKE] = "Cam"
    src = photo(tmp_path / "a.jpg", color=(250, 250, 250), exif=exif.tobytes(), quality=95)
    window = open_editor(qtbot, RedactEditor, [src])
    assert not window.save_button.isEnabled()
    drag(window.canvas, (20, 20), (120, 80))
    assert [i.style for i in window.state()] == ["solid"]
    window.styles.buttons()[1].click()  # Blur, for the picked area
    assert [i.style for i in window.state()] == ["blur"]
    window.styles.buttons()[0].click()
    drag(window.canvas, (200, 150), (300, 250))
    assert len(window.state()) == 2
    QTest.keyClick(window.canvas, Qt.Key.Key_Delete)  # the new one is picked
    assert len(window.state()) == 1
    window.styles.buttons()[0].click()  # back to a black box
    snap = saved_output(window)
    (out,) = snap.outputs
    assert out.name == "a-redacted.jpg"
    with Image.open(out) as im:
        assert max(im.getpixel((60, 50))) < 20
        assert min(im.getpixel((300, 200))) > 230
        assert MAKE not in im.getexif()
    assert any("EXIF" in note for note in snap.notes)


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
    window = open_editor(qtbot, FrameEditor, [photo(tmp_path / "a.png")])
    assert window.size().width() == 1000 and window.width() == 1000
    assert window.style() is not None
    assert isinstance(window.pos(), QPoint)
