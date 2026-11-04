"""Phase 6 UI: running start steps after the drag, the Qt host and the Float shelf."""

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QGuiApplication
from PySide6.QtWidgets import QFileDialog, QWidget

from quince.core.config import Config
from quince.core.deps import Availability
from quince.core.jobs import JobError
from quince.core.operations import Accepts, Choice, JobRequest, Kind, Operation
from quince.core.registry import Registry
from quince.core.routes import Route
from quince.core.selection import Item, Selection
from quince.input import drag
from quince.ui import host as host_module
from quince.ui.feedback.toast import Toast
from quince.ui.host import QtHost, choice_menu, folder_dialog
from quince.ui.runner import OperationRunner
from quince.ui.shelf.window import ShelfWindow, dropped_paths, mime_for
from quince.ui.wheel.controller import DragWheelController
from quince.ui.wheel.window import WheelWindow
from tests.ui.test_wheel_controller import PNG

# --- running start steps once the drag is over -----------------------------------------


@pytest.fixture
def png(tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(PNG)
    return str(path)


def _controller(qtbot, grace_ms):
    registry = Registry.from_parts(
        [Route("png", "jpg", "imaging")],
        [Operation("action.copy_path", Kind.ACTION, "Copy Path", "x", Accepts.anything())],
    )
    wheel = WheelWindow(animations=False, drop_settle_ms=0)
    qtbot.addWidget(wheel)
    return DragWheelController(
        wheel,
        registry,
        load_config=Config,
        probe=Availability.everything,
        submit=lambda fn: fn(),
        nudge=lambda: None,
        grace_ms=grace_ms,
    )


def test_after_drag_waits_for_the_wheel_to_be_released(qtbot, png):
    ctl = _controller(qtbot, grace_ms=0)
    ran = []
    ctl.handle(drag.DragStarted(100))
    ctl.handle(drag.DragFiles(100, (png,)))
    ctl.handle(drag.TriggerTapped("actions", "Alt_L", 110))
    ctl.handle(drag.DragKey("Return", 111))  # keyboard choice: the drag goes on
    ctl.after_drag(lambda: ran.append(1))
    qtbot.wait(50)
    assert ran == [] and ctl.busy
    ctl.handle(drag.DragEnded(120))
    qtbot.waitUntil(lambda: ran == [1], timeout=1000)
    assert not ctl.wheel.isVisible()


def test_after_drag_runs_soon_when_idle(qtbot):
    ctl = _controller(qtbot, grace_ms=0)
    ran = []
    ctl.after_drag(lambda: ran.append(1))
    assert ran == []  # never inside the caller
    qtbot.waitUntil(lambda: ran == [1], timeout=1000)


def test_a_drop_choice_releases_the_wheel_without_the_grace_period(qtbot, png):
    ctl = _controller(qtbot, grace_ms=10_000)
    ran = []
    ctl.operation_chosen.connect(lambda op, sel: ctl.after_drag(lambda: ran.append(op.id)))
    ctl.handle(drag.DragStarted(100))
    ctl.handle(drag.DragFiles(100, (png,)))
    ctl.handle(drag.TriggerTapped("actions", "Alt_L", 110))
    ctl.handle(drag.DragEnded(120))  # the raw release came before the settled drop
    ctl.wheel._finish_drop(0, [png])
    qtbot.waitUntil(lambda: ran == ["action.copy_path"], timeout=1000)
    assert not ctl.wheel.isVisible()


def test_a_drop_before_the_drag_end_also_skips_the_grace_period(qtbot, png):
    ctl = _controller(qtbot, grace_ms=10_000)
    ran = []
    ctl.handle(drag.DragStarted(100))
    ctl.handle(drag.DragFiles(100, (png,)))
    ctl.handle(drag.TriggerTapped("actions", "Alt_L", 110))
    ctl.wheel._finish_drop(0, [png])
    ctl.after_drag(lambda: ran.append(1))
    ctl.handle(drag.DragEnded(120))
    qtbot.waitUntil(lambda: ran == [1], timeout=1000)


# --- the runner ---------------------------------------------------------------------


class FakeJobs:
    def __init__(self):
        self.submitted = []

    def submit(self, run, selection, *, title, op_id, output):
        self.submitted.append((run, title, op_id))


class RecordingHost:
    def __init__(self):
        self.messages = []
        self.errors = []

    def notify(self, text):
        self.messages.append(text)

    def notify_error(self, text):
        self.errors.append(text)


def _op(op_id="action.thing", **kw):
    return Operation(op_id, Kind.ACTION, "Thing", "x", Accepts.anything(), title="Do thing", **kw)


@pytest.fixture
def runner():
    deferred = []
    r = OperationRunner(FakeJobs(), RecordingHost(), after_drag=deferred.append, load_config=Config)
    r.deferred = deferred
    return r


SEL = Selection((Item(Path("/x/a.txt")),))


def test_run_operations_are_queued_at_once(runner):
    def job(ctx):
        return None

    runner.run(Operation("tool.t", Kind.TOOL, "T", "x", Accepts(), run=job), SEL)
    assert runner.jobs.submitted == [(job, "T", "tool.t")]
    assert runner.deferred == []


def test_start_steps_wait_for_the_drag_and_may_queue_a_job(runner):
    def job(ctx):
        return None

    seen = []

    def start(selection, host):
        seen.append((selection, host))
        return JobRequest(job, "Copy to dest")

    runner.run(_op(start=start), SEL)
    assert seen == [] and len(runner.deferred) == 1
    runner.deferred[0]()
    assert seen == [(SEL, runner.host)]
    assert runner.jobs.submitted == [(job, "Copy to dest", "action.thing")]


def test_start_errors_are_shown(runner):
    def fails(selection, host):
        raise JobError("No terminal program found", details="tried x, y")

    def crashes(selection, host):
        raise RuntimeError("boom")

    runner.start(_op(start=fails), SEL)
    runner.start(_op(start=crashes), SEL)
    runner.start(_op(start=lambda s, h: None), SEL)
    assert runner.host.errors == [
        "Do thing: No terminal program found",
        "Do thing: unexpected error: boom",
    ]
    assert runner.jobs.submitted == []


def test_stub_operations_show_a_toast(runner):
    runner.run(_op(), SEL)
    assert runner.host.messages == ["Do thing · a.txt\nNot implemented yet"]


# --- the Qt host --------------------------------------------------------------------


def test_folder_dialog(qtbot, tmp_path):
    recent = [tmp_path / "r1", tmp_path / "r2"]
    for r in recent:
        r.mkdir()
    dialog = folder_dialog(
        "Copy 2 items to", accept_label="Copy Here", start=tmp_path, recent=recent
    )
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == "Copy 2 items to"
    assert dialog.labelText(QFileDialog.DialogLabel.Accept) == "Copy Here"
    assert dialog.fileMode() == QFileDialog.FileMode.Directory
    assert dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
    assert Path(dialog.directory().absolutePath()) == tmp_path
    assert dialog.findChild(QWidget, "fileTypeCombo").isHidden()  # no file types for folders
    sidebar = [Path(u.toLocalFile()) for u in dialog.sidebarUrls()]
    assert sidebar[:3] == [Path.home(), *recent]


def test_choice_menu(qtbot):
    menu = choice_menu(
        "Open a.png with",
        [Choice("viewer.desktop", "Viewer", "image-x-generic")],
        [Choice("gimp.desktop", "GIMP", "/no/such/icon.png")],
        "Other Applications",
    )
    actions = menu.actions()
    assert actions[0].text() == "Open a.png with" and not actions[0].isEnabled()
    assert [a.data() for a in actions if a.data()] == ["viewer.desktop"]
    sub = actions[-1].menu()
    assert actions[-1].text() == "Other Applications"
    assert [a.data() for a in sub.actions()] == ["gimp.desktop"]
    empty = choice_menu("Open x with", [], [], "")
    assert any(a.text() == "No suggested applications" for a in empty.actions())


def test_icon_for():
    assert host_module.icon_for("").isNull()
    assert host_module.icon_for("/no/such/icon.png").isNull()
    host_module.icon_for("org.gnome.gedit.png")  # an extension is tolerated


def test_qt_host(qtbot, tmp_path):
    shelf = ShelfWindow(open_file=lambda p: None, show_files=lambda p: None)
    qtbot.addWidget(shelf)
    shown = []
    toast = Toast()
    qtbot.addWidget(toast)
    host = QtHost(
        toast,
        lambda: shelf,
        show_files=shown.append,
        run_dialog=lambda d: Path(d.directory().absolutePath()),
        run_menu=lambda m: m.actions()[2].data(),
    )
    assert host.choose_folder("t", accept_label="Copy Here", start=tmp_path, recent=[]) == tmp_path
    assert host.choose("t", [Choice("a.desktop", "A")]) == "a.desktop"
    host.set_clipboard_text("/x/a.txt\n/x/b.txt")
    assert QGuiApplication.clipboard().text() == "/x/a.txt\n/x/b.txt"
    host.show_in_files([Path("/x/a.txt")])
    assert shown == [[Path("/x/a.txt")]]
    host.notify("Path copied")
    assert toast.text == "Path copied" and toast.isVisible()
    host.notify_error("Open in Terminal: No terminal program found")
    assert toast.text.startswith("Open in Terminal")
    host.float_items([tmp_path])
    assert shelf.paths == [tmp_path] and shelf.isVisible()


# --- the Float shelf -------------------------------------------------------------------


@pytest.fixture
def shelf(qtbot):
    drags, opened, shown = [], [], []

    def fake_drag(source, paths):
        drags.append(paths)
        return Qt.DropAction.CopyAction

    s = ShelfWindow(open_file=opened.append, show_files=shown.append, start_drag=fake_drag)
    qtbot.addWidget(s)
    s.drags, s.opened, s.shown = drags, opened, shown
    return s


@pytest.fixture
def shelf_files(tmp_path):
    paths = []
    for name in ("a.png", "b.txt", "c.pdf"):
        (tmp_path / name).write_text(name)
        paths.append(tmp_path / name)
    return paths


def _drop_events(paths, source_actions=Qt.DropAction.CopyAction | Qt.DropAction.MoveAction):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    rest = (mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    enter = QDragEnterEvent(QPoint(20, 20), source_actions, *rest)
    drop = QDropEvent(QPointF(20, 20), source_actions, *rest)
    for event in (enter, drop):
        event.setDropAction(Qt.DropAction.MoveAction)
    return mime, enter, drop


def test_add_shows_the_shelf_and_skips_duplicates(shelf, shelf_files):
    assert not shelf.isVisible()
    assert shelf.add(shelf_files[:2]) == shelf_files[:2]
    assert shelf.isVisible()
    assert shelf.add([shelf_files[0]]) == []
    assert shelf.list.count() == 2 and shelf.count.text() == "· 2 items"
    assert shelf.list.item(0).toolTip() == str(shelf_files[0])
    assert shelf.handle.isVisible() and shelf.handle.text() == "Drag all 2"
    # It opens at the right edge of the screen.
    area = QGuiApplication.primaryScreen().availableGeometry()
    assert shelf.geometry().right() <= area.right() and shelf.x() > area.center().x()


def test_drop_adds_files_as_copy(shelf, shelf_files):
    _mime, enter, drop = _drop_events(shelf_files)
    shelf.dragEnterEvent(enter)
    shelf.dropEvent(drop)
    for event in (enter, drop):
        assert event.isAccepted()
        assert event.dropAction() == Qt.DropAction.CopyAction  # the source never deletes
    assert shelf.paths == shelf_files
    assert all(p.exists() for p in shelf_files)


def test_drops_without_local_files_are_refused(shelf):
    mime = QMimeData()
    mime.setText("just text")
    event = QDragEnterEvent(
        QPoint(5, 5),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    shelf.dragEnterEvent(event)
    assert not event.isAccepted()


def test_drag_out_offers_the_selected_or_all_items(shelf, shelf_files):
    shelf.add(shelf_files)
    shelf.list.item(2).setSelected(True)
    shelf.list.item(0).setSelected(True)
    shelf.list.startDrag(Qt.DropAction.CopyAction)
    assert shelf.drags == [[shelf_files[0], shelf_files[2]]]  # in shelf order
    shelf.handle.drag_requested.emit()
    assert shelf.drags[-1] == shelf_files
    assert shelf.paths == shelf_files  # dragging out keeps the items


def test_drag_data_is_a_uri_list(shelf_files):
    data = mime_for(shelf_files)
    assert data.formats() == ["text/uri-list"]
    assert dropped_paths(data) == [str(p) for p in shelf_files]


def test_real_drag_offers_copy_only(shelf, shelf_files, monkeypatch):
    calls = []

    class FakeDrag:
        def __init__(self, source):
            pass

        def setMimeData(self, data):
            calls.append(("mime", data.formats()))

        def setPixmap(self, pixmap):
            pass

        def exec(self, actions, default):
            calls.append(("exec", actions, default))
            return Qt.DropAction.CopyAction

    from quince.ui.shelf import window

    monkeypatch.setattr(window, "QDrag", FakeDrag)
    plain = ShelfWindow()
    plain.add(shelf_files[:1])
    plain.drag_out(shelf_files[:1])
    assert calls == [
        ("mime", ["text/uri-list"]),
        ("exec", Qt.DropAction.CopyAction, Qt.DropAction.CopyAction),
    ]
    plain.close_shelf()


def test_remove_clear_close(shelf, shelf_files, qtbot):
    shelf.add(shelf_files)
    shelf.list.item(1).setSelected(True)
    qtbot.keyClick(shelf, Qt.Key.Key_Delete)
    assert shelf.paths == [shelf_files[0], shelf_files[2]]
    shelf.clear_button.click()
    assert shelf.paths == [] and shelf.isVisible()
    assert shelf.empty.isVisible() and not shelf.list.isVisible()
    shelf.add(shelf_files[:1])
    shelf.close_button.click()
    assert not shelf.isVisible() and shelf.paths == []


def test_missing_files_drop_off(shelf, shelf_files):
    shelf.add(shelf_files)
    shelf_files[1].unlink()
    shelf.prune()
    assert shelf.paths == [shelf_files[0], shelf_files[2]]
    shelf_files[0].unlink()
    shelf.drag_out([shelf_files[0], shelf_files[2]])
    assert shelf.drags == [[shelf_files[2]]]


def test_double_click_opens(shelf, shelf_files):
    shelf.add(shelf_files[:1])
    shelf.list.itemDoubleClicked.emit(shelf.list.item(0))
    assert shelf.opened == [shelf_files[0]]


def test_shelf_keeps_its_position_after_being_moved(shelf, shelf_files):
    shelf.add(shelf_files[:1])
    shelf.move(100, 120)
    shelf.close_shelf()
    shelf.add(shelf_files[:1])
    assert shelf.pos() == QPoint(100, 120)
