import os
import subprocess
import sys

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent
from PySide6.QtWidgets import QApplication

from quince import app
from quince.ui import runner
from quince.ui.feedback.toast import Toast
from quince.ui.wheel.demo import demo_content
from quince.ui.wheel.window import WheelWindow
from tests.ui.test_wheel_window import _drag_events


def _ask(qtbot, command: str, name: str) -> str:
    """Send a command from another process, as ``quince service --stop`` does."""
    code = f"from quince import app; print(app.send_command({command!r}, {name!r}))"
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    qtbot.waitUntil(lambda: proc.poll() is not None, timeout=10000)
    return proc.stdout.read().strip()


def test_instance_server_answers_commands(qtbot):
    name = f"quince-test-{os.getpid()}"
    calls = []
    handlers = {"ping": lambda: "pong", "quit": lambda: calls.append(1) or "bye"}
    server = app.InstanceServer(handlers, name)
    assert server.listen()
    try:
        answers = [_ask(qtbot, command, name) for command in ("ping", "quit", "nope")]
        assert answers == ["pong", "bye", "unknown command 'nope'"]
        assert calls == [1]
    finally:
        server.close()


def test_send_command_without_a_server():
    assert app.send_command("ping", "quince-test-nobody-listens") is None


def test_listen_replaces_a_stale_socket(qtbot):
    name = f"quince-test-stale-{id(qtbot)}"
    first = app.InstanceServer({}, name)
    assert first.listen()
    second = app.InstanceServer({}, name)
    assert second.listen()  # e.g. after a crash; only called when nobody answered
    second.close()
    first.close()


def test_stub_message():
    from pathlib import Path

    from quince.core.operations import Accepts, Kind, Operation
    from quince.core.selection import Item, Selection

    op = Operation("convert.image.jpg", Kind.CONVERT, "JPG", "x", Accepts(), title="JPEG image")
    one = Selection((Item(Path("/x/photo.png")),))
    two = Selection((Item(Path("/x/a.png")), Item(Path("/x/b.png"))))
    assert runner.stub_message(op, one) == "JPEG image · photo.png\nNot implemented yet"
    assert runner.stub_message(op, two).startswith("JPEG image · 2 items")


def test_toast_shows_and_hides(qtbot):
    toast = Toast()
    qtbot.addWidget(toast)
    toast.show_message("Hello\nworld", duration_ms=50)
    assert toast.isVisible() and toast.text == "Hello\nworld"
    qtbot.waitUntil(lambda: not toast.isVisible(), timeout=2000)


def test_wheel_rejects_drags_without_files(qtbot):
    wheel = WheelWindow(animations=False)
    qtbot.addWidget(wheel)
    wheel.open_at(QPoint(400, 300), demo_content("convert", 3))
    mime = QMimeData()
    mime.setText("just text")
    enter = QDragEnterEvent(
        QPoint(1, 1),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(wheel, enter)
    assert not enter.isAccepted()


def test_held_wheel_swallows_drops_and_release_hides(qtbot):
    wheel = WheelWindow(animations=False, drop_settle_ms=0)
    qtbot.addWidget(wheel)
    chosen = []
    wheel.chosen.connect(lambda *a: chosen.append(a))
    wheel.open_at(QPoint(400, 300), demo_content("convert", 3), hold=True)
    wheel.handle_key("Right")
    wheel.close_wheel("cancel click")
    assert wheel.holding and wheel.isVisible()
    wheel._finish_drop(0, ["/tmp/a.png"])
    assert chosen == []
    wheel.release()
    assert not wheel.isVisible() and not wheel.holding


def test_drop_settles_before_acting(qtbot):
    """A cancel click that arrives just after the drop still wins."""
    wheel = WheelWindow(animations=False, drop_settle_ms=50)
    qtbot.addWidget(wheel)
    chosen = []
    wheel.chosen.connect(lambda *a: chosen.append(a))
    wheel.open_at(QPoint(400, 300), demo_content("convert", 3), hold=True)
    wheel.handle_key("Right")
    _mime, _enter, _move, drop = _drag_events(QPointF(10, 10), ["/tmp/a.png"])
    wheel.dropEvent(drop)
    assert drop.dropAction() == Qt.DropAction.CopyAction
    wheel.close_wheel("cancel click")
    qtbot.wait(150)
    assert chosen == []


def test_set_content_replaces_without_switch(qtbot):
    wheel = WheelWindow(animations=False)
    qtbot.addWidget(wheel)
    wheel.open_at(QPoint(400, 300), demo_content("convert", 0))
    wheel.set_content(demo_content("convert", 9))
    assert len(wheel.content.items) == 9 and wheel._previous is None
