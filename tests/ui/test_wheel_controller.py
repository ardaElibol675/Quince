from pathlib import Path

import pytest
from PySide6.QtCore import QPointF

from quince.core.config import Config
from quince.core.deps import Availability
from quince.core.operations import Accepts, Kind, Operation
from quince.core.registry import Registry
from quince.core.routes import Route
from quince.input import drag
from quince.ui.wheel.controller import DragWheelController
from quince.ui.wheel.model import PENDING_MESSAGE
from quince.ui.wheel.window import WheelWindow

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


@pytest.fixture
def files(tmp_path):
    paths = []
    for name, data in (("a.png", PNG), ("b.png", PNG), ("c.pdf", b"%PDF-1.4\n")):
        (tmp_path / name).write_bytes(data)
        paths.append(str(tmp_path / name))
    return paths


@pytest.fixture
def ctl(qtbot):
    registry = Registry.from_parts(
        [Route("png", "jpg", "imaging"), Route("png", "webp", "imaging")],
        [Operation("action.copy_path", Kind.ACTION, "Copy Path", "x", Accepts.anything())],
    )
    wheel = WheelWindow(animations=False, drop_settle_ms=0)
    qtbot.addWidget(wheel)
    nudges = []
    c = DragWheelController(
        wheel,
        registry,
        load_config=Config,
        probe=Availability.everything,
        submit=lambda fn: fn(),  # run the worker job inline
        nudge=lambda: nudges.append(1),
        grace_ms=0,
    )
    c.nudges = nudges
    c.chosen = []
    c.operation_chosen.connect(lambda op, sel: c.chosen.append((op.id, sel)))
    return c


def _labels(wheel: WheelWindow) -> list[str]:
    return [i.label for i in wheel.content.items]


def _start(ctl, paths=None, time=100):
    ctl.handle(drag.DragStarted(time))
    if paths is not None:
        ctl.handle(drag.DragFiles(time, tuple(paths)))


def test_tap_opens_the_wheel_with_the_dragged_files(ctl, files):
    _start(ctl, files[:2])
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 110))
    assert ctl.wheel.is_open and _labels(ctl.wheel) == ["JPG", "WebP"]
    assert ctl.nudges == [1]
    ctl.handle(drag.TriggerTapped("actions", "Alt_L", 120))
    assert ctl.wheel.content.wheel == "actions" and _labels(ctl.wheel) == ["Copy Path"]
    assert ctl.nudges == [1]  # only when opening


def test_pending_until_the_files_are_known(ctl, files):
    _start(ctl)
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 110))
    assert ctl.wheel.content.empty_message == PENDING_MESSAGE
    ctl.handle(drag.DragFiles(100, tuple(files[:1])))
    assert _labels(ctl.wheel) == ["JPG", "WebP"]


def test_drag_enter_data_is_the_fallback(ctl, files):
    _start(ctl)
    ctl.handle(drag.DragFiles(100, None))  # the source did not answer
    ctl.handle(drag.TriggerTapped("tools", "Control_L", 110))
    ctl.wheel.drag_entered.emit(files[:2])
    assert ctl.wheel.content.wheel == "tools"
    assert ctl.wheel.content.empty_message == "Nothing available"


def test_mixed_selection_messages(ctl, files):
    _start(ctl, [files[0], files[2]])
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 110))
    assert _labels(ctl.wheel) == []
    assert ctl.wheel.content.empty_message == "Only file actions for folders and mixed files"


def test_no_files_means_no_wheel(ctl):
    _start(ctl, [])
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 110))
    assert not ctl.wheel.isVisible()


def test_text_drag_closes_a_pending_wheel(ctl):
    _start(ctl)
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 110))
    ctl.wheel.drag_entered.emit([])
    assert not ctl.wheel.is_open and not ctl.wheel.isVisible()


def test_stale_files_of_an_older_drag_are_ignored(ctl, files):
    _start(ctl, time=200)
    ctl.handle(drag.DragFiles(100, tuple(files[:1])))
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 210))
    assert ctl.wheel.content.empty_message == PENDING_MESSAGE


def test_keyboard_choice_emits_operation_and_holds(ctl, files):
    _start(ctl, files[:2])
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 110))
    ctl.handle(drag.DragKey("Right", 111))
    ctl.handle(drag.DragKey("Return", 112))
    assert [c[0] for c in ctl.chosen] == ["convert.image.jpg"]
    assert [str(i.path) for i in ctl.chosen[0][1].items] == files[:2]
    assert ctl.wheel.holding  # invisible but mapped, to swallow GTK's Enter drop
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 113))
    assert not ctl.wheel.is_open  # one wheel per drag


def test_drop_on_slice_uses_the_dropped_paths(ctl, files):
    _start(ctl, files[:1])
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 110))
    ctl.wheel.handle_key("End")
    ctl.wheel._finish_drop(ctl.wheel.state.highlighted, files[:2])
    op_id, selection = ctl.chosen[0]
    assert op_id == "convert.image.webp" and len(selection) == 2


def test_cancel_click_closes_and_holds_until_the_drag_ends(ctl, qtbot, files):
    _start(ctl, files[:1])
    ctl.handle(drag.TriggerTapped("tools", "Control_L", 110))
    ctl.handle(drag.DragCancel(3, 111))
    assert not ctl.wheel.is_open and ctl.wheel.holding
    ctl.handle(drag.DragEnded(120))
    qtbot.waitUntil(lambda: not ctl.wheel.isVisible(), timeout=1000)
    assert ctl.chosen == []


def test_drag_end_without_a_drop_closes_the_wheel(ctl, qtbot, files):
    _start(ctl, files[:1])
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 110))
    ctl.handle(drag.DragEnded(120))
    qtbot.waitUntil(lambda: not ctl.wheel.isVisible(), timeout=1000)
    assert not ctl.wheel.is_open


def test_taps_outside_a_drag_are_ignored(ctl):
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 1))
    assert not ctl.wheel.isVisible()


def test_new_drag_releases_a_held_wheel(ctl, files):
    _start(ctl, files[:1])
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 110))
    ctl.handle(drag.DragCancel(2, 111))
    assert ctl.wheel.holding
    _start(ctl, files[:1], time=300)
    assert not ctl.wheel.isVisible()
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 310))
    assert ctl.wheel.is_open


def test_keys_go_to_the_wheel_only_when_open(ctl, files):
    ctl.handle(drag.DragKey("Right", 1))  # no wheel: nothing happens
    _start(ctl, files[:1])
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 110))
    ctl.handle(drag.DragKey("Escape", 111))
    assert not ctl.wheel.is_open


def test_wheel_center_is_on_screen(ctl, files):
    _start(ctl, files[:1])
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 110))
    assert isinstance(ctl.wheel.center, QPointF)
    area = ctl.wheel.screen().availableGeometry()
    assert area.contains(ctl.wheel.center.toPoint())


def test_unknown_selection_path(ctl, tmp_path):
    blob = tmp_path / "blob"
    blob.write_bytes(b"\x00\x01\x02")
    _start(ctl, [str(blob)])
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 110))
    assert ctl.wheel.content.empty_message == "Unknown file type"
    ctl.handle(drag.TriggerTapped("actions", "Alt_L", 111))
    assert _labels(ctl.wheel) == ["Copy Path"]


def test_selection_paths_are_kept_as_strings(ctl, files):
    _start(ctl, files[:1])
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 110))
    ctl.wheel.handle_key("Home")
    ctl.wheel.handle_key("Return")
    assert ctl.chosen[0][1].items[0].path == Path(files[0])


def test_folder_gets_only_actions(ctl, tmp_path):
    folder = tmp_path / "photos"
    folder.mkdir()
    _start(ctl, [str(folder)])
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 110))
    assert _labels(ctl.wheel) == []
    assert ctl.wheel.content.empty_message == "Only file actions for folders and mixed files"
    ctl.handle(drag.TriggerTapped("actions", "Alt_L", 111))
    ctl.handle(drag.DragKey("Down", 112))
    ctl.handle(drag.DragKey("Return", 113))
    op_id, selection = ctl.chosen[0]
    assert op_id == "action.copy_path" and selection.has_folders


def test_text_drag_enter_on_a_pending_wheel_closes_it(ctl):
    """A source that refused every request: the drag-enter event tells it is not files."""
    from PySide6.QtCore import QMimeData, QPoint, Qt
    from PySide6.QtGui import QDragEnterEvent

    _start(ctl)
    ctl.handle(drag.DragFiles(100, None))
    ctl.handle(drag.TriggerTapped("convert", "Shift_L", 110))
    assert ctl.wheel.is_open
    mime = QMimeData()
    mime.setText("selected text")
    enter = QDragEnterEvent(
        QPoint(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )  # fmt: skip
    ctl.wheel.dragEnterEvent(enter)
    assert not enter.isAccepted()
    assert not ctl.wheel.is_open and not ctl.wheel.isVisible()
