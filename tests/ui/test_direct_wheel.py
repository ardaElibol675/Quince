import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from quince.core.config import Config
from quince.core.deps import Availability
from quince.input import drag
from quince.ui.wheel.controller import DragWheelController
from quince.ui.wheel.direct import DirectWheel, first_wheel
from quince.ui.wheel.model import WheelContent, WheelItem
from quince.ui.wheel.window import WheelWindow

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


@pytest.fixture
def files(tmp_path):
    paths = []
    for name, data in (("a.png", PNG), ("b.png", PNG), ("c.pdf", b"%PDF-1.4\n")):
        (tmp_path / name).write_bytes(data)
        paths.append(str(tmp_path / name))
    return paths


def _registry():
    from quince.core.operations import Accepts, Kind, Operation
    from quince.core.registry import Registry
    from quince.core.routes import Route

    return Registry.from_parts(
        [Route("png", "jpg", "imaging"), Route("png", "webp", "imaging")],
        [Operation("action.copy_path", Kind.ACTION, "Copy Path", "x", Accepts.anything())],
    )


def _direct(qtbot, wheel=None, **kw):
    wheel = wheel or WheelWindow(animations=False, drop_settle_ms=0)
    qtbot.addWidget(wheel)
    notes = []
    direct = DirectWheel(
        wheel,
        kw.pop("registry", None) or _registry(),
        load_config=Config,
        probe=Availability.everything,
        submit=lambda fn: fn(),
        notify=notes.append,
        **kw,
    )
    direct.notes = notes
    direct.chosen = []
    direct.operation_chosen.connect(lambda op, sel: direct.chosen.append((op.id, sel)))
    return direct


def _labels(wheel):
    return [i.label for i in wheel.content.items]


def test_first_wheel_is_the_first_with_items():
    item = (WheelItem("x", "X", "X", ""),)
    contents = {"convert": WheelContent("convert"), "tools": WheelContent("tools", item),
                "actions": WheelContent("actions", item)}  # fmt: skip
    assert first_wheel(contents) == "tools"
    contents["tools"] = WheelContent("tools")
    assert first_wheel(contents) == "actions"


def test_opens_the_first_wheel_with_items_at_the_given_point(qtbot, files):
    direct = _direct(qtbot)
    assert direct.open(files[:2], pos=QPoint(500, 400))
    assert direct.is_open and direct.wheel.owner is direct
    assert direct.wheel.content.wheel == "convert" and _labels(direct.wheel) == ["JPG", "WebP"]
    direct.close()
    direct.open([files[2]])  # a PDF: no convert or tool route in this registry
    assert direct.wheel.content.wheel == "actions"


def test_requested_wheel_and_keyboard_choice(qtbot, files):
    direct = _direct(qtbot)
    direct.open(files[:2], wheel="actions")
    assert direct.wheel.content.wheel == "actions"
    direct.wheel.handle_key("Down")
    direct.wheel.handle_key("Return")
    assert not direct.wheel.is_open
    [(op_id, selection)] = direct.chosen
    assert op_id == "action.copy_path"
    assert [str(i.path) for i in selection.items] == files[:2]


def test_trigger_taps_switch_wheels(qtbot, files):
    direct = _direct(qtbot)
    direct.open(files[:2])
    QTest.keyPress(direct.wheel, Qt.Key.Key_Alt)
    QTest.keyRelease(direct.wheel, Qt.Key.Key_Alt)
    assert direct.wheel.content.wheel == "actions"
    QTest.keyPress(direct.wheel, Qt.Key.Key_Shift)
    QTest.keyRelease(direct.wheel, Qt.Key.Key_Shift)
    assert direct.wheel.content.wheel == "convert"
    QTest.keyClick(direct.wheel, Qt.Key.Key_Escape)
    assert not direct.wheel.is_open and direct.chosen == []


def test_click_outside_the_ring_closes_without_a_choice(qtbot, files):
    direct = _direct(qtbot)
    direct.open(files[:2], pos=QPoint(500, 400))
    QTest.mouseClick(direct.wheel, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    assert not direct.wheel.is_open and direct.chosen == []


def test_missing_files_are_reported(qtbot, tmp_path):
    direct = _direct(qtbot)
    assert not direct.open([str(tmp_path / "gone.png")])
    assert direct.notes and not direct.wheel.isVisible()


def test_no_wheel_while_a_drag_owns_it(qtbot, files):
    direct = _direct(qtbot, busy=lambda: True)
    direct.open(files[:2])
    assert not direct.wheel.isVisible()


def test_drag_controller_and_direct_session_share_one_wheel(qtbot, files):
    wheel = WheelWindow(animations=False, drop_settle_ms=0)
    registry = _registry()
    direct = _direct(qtbot, wheel, registry=registry)
    ctl = DragWheelController(wheel, registry, load_config=Config, probe=Availability.everything,
                              submit=lambda fn: fn(), nudge=lambda: None, grace_ms=0)  # fmt: skip
    from_drag = []
    ctl.operation_chosen.connect(lambda op, sel: from_drag.append(op.id))

    # A direct choice is not reported by the drag controller.
    direct.open(files[:2], wheel="actions")
    wheel.handle_key("Down")
    wheel.handle_key("Return")
    assert [c[0] for c in direct.chosen] == ["action.copy_path"] and from_drag == []

    # A drag starting closes a direct wheel; the drag's choice is not reported by it.
    direct.open(files[:2])
    ctl.handle(drag.DragStarted(100))
    assert not direct.is_open and not wheel.isVisible()
    ctl.handle(drag.DragFiles(100, tuple(files[:2])))
    ctl.handle(drag.TriggerTapped("actions", "Alt_L", 110))
    assert wheel.owner is ctl
    wheel.handle_key("Down")
    wheel.handle_key("Return")
    assert from_drag == ["action.copy_path"] and len(direct.chosen) == 1
    assert ctl.in_drag_session
    ctl.handle(drag.DragEnded(120))
    qtbot.waitUntil(lambda: not ctl.in_drag_session, timeout=1000)
    # let the controller's release timer run while the wheel still exists
    qtbot.waitUntil(lambda: not wheel.isVisible(), timeout=1000)
    qtbot.wait(20)
