"""The file actions (Phase 6), driven through their ``start`` step with a fake host."""

from pathlib import Path

import pytest

from quince.backends import desktop
from quince.core import recent
from quince.core.jobs import JobError, JobQueue, JobState
from quince.core.operations import Kind
from quince.core.registry import discover
from quince.core.selection import Selection
from quince.operations.actions import (
    copy,
    copy_path,
    move,
    open_in_terminal,
    open_with,
    show_in_files,
)
from quince.operations.actions import (
    float as float_action,
)
from tests.unit.test_desktop import _app
from tests.unit.test_jobs import wait_finished


class FakeHost:
    def __init__(self, folder=None, choice=None):
        self.folder = folder
        self.choice = choice
        self.calls = []

    def choose_folder(self, title, *, accept_label, start, recent):
        self.calls.append(("choose_folder", title, accept_label, start, list(recent)))
        return self.folder

    def choose(self, title, choices, *, more=(), more_label=""):
        self.calls.append(("choose", title, list(choices), list(more), more_label))
        return self.choice

    def set_clipboard_text(self, text):
        self.calls.append(("clipboard", text))

    def notify(self, text):
        self.calls.append(("notify", text))

    def show_in_files(self, paths):
        self.calls.append(("show", list(paths)))

    def float_items(self, paths):
        self.calls.append(("float", list(paths)))


@pytest.fixture
def items(tmp_path):
    src = tmp_path / "src"
    (src / "folder").mkdir(parents=True)
    (src / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
    (src / "notes.txt").write_text("notes")
    return [src / "photo.png", src / "notes.txt", src / "folder"]


@pytest.fixture
def dest(tmp_path):
    path = tmp_path / "dest"
    path.mkdir()
    return path


def _sel(paths):
    return Selection.from_paths(paths)


def run_request(request, selection):
    queue = JobQueue(workers=1)
    try:
        job_id = queue.submit(request.run, selection, title=request.title)
        return wait_finished(queue, job_id)
    finally:
        queue.shutdown()


def test_actions_wheel_contents_and_order(items):
    registry = discover()
    for selection in (_sel(items[:1]), _sel(items), _sel(items[2:])):
        ops = registry.query(selection, Kind.ACTION)
        assert [op.id for op in ops] == [
            "action.copy",
            "action.move",
            "action.copy_path",
            "action.show_in_files",
            "action.create_archive",
            "action.open_with",
            "action.open_in_terminal",
            "action.float",
        ]
        assert all(op.start is not None and not op.requires for op in ops)


def test_copy_asks_for_a_folder_and_copies(items, dest):
    host = FakeHost(folder=dest)
    selection = _sel(items)
    request = copy.OPERATION.start(selection, host)
    _, title, accept, start, recents = host.calls[0]
    assert (title, accept) == ("Copy 3 items to", "Copy Here")
    assert start == items[0].parent and recents == []  # nothing recent yet
    assert request.title == "Copy to dest"
    snap = run_request(request, selection)
    assert snap.state is JobState.SUCCEEDED
    assert sorted(p.name for p in dest.iterdir()) == ["folder", "notes.txt", "photo.png"]
    assert all(p.exists() for p in items)  # originals stay

    # The destination is remembered and offered first next time.
    assert recent.destinations() == [dest]
    host = FakeHost(folder=None)
    assert copy.OPERATION.start(_sel(items[:1]), host) is None  # cancelled
    _, title, _, start, recents = host.calls[0]
    assert title == "Copy photo.png to" and start == dest and recents == [dest]


def test_move_asks_for_a_folder_and_moves(items, dest):
    host = FakeHost(folder=dest)
    selection = _sel(items[:2])
    request = move.OPERATION.start(selection, host)
    assert host.calls[0][1:3] == ("Move 2 items to", "Move Here")
    assert request.title == "Move to dest"
    snap = run_request(request, selection)
    assert snap.outputs == (dest / "photo.png", dest / "notes.txt")
    assert not items[0].exists() and not items[1].exists()


def test_copy_path(items):
    host = FakeHost()
    assert copy_path.OPERATION.start(_sel(items[:1]), host) is None
    assert host.calls == [("clipboard", str(items[0])), ("notify", "Path copied")]
    host = FakeHost()
    copy_path.OPERATION.start(_sel(items), host)
    assert host.calls[0] == ("clipboard", "\n".join(map(str, items)))
    assert host.calls[1] == ("notify", "3 paths copied")


def test_show_in_files_and_float(items):
    host = FakeHost()
    show_in_files.OPERATION.start(_sel(items), host)
    float_action.OPERATION.start(_sel(items[:2]), host)
    assert host.calls == [("show", items), ("float", items[:2])]


def test_open_in_terminal(items, tmp_path, monkeypatch):
    opened = []

    class FakeTerminal:
        def open_in(self, folder):
            opened.append(folder)

    monkeypatch.setattr(desktop, "find_terminal", lambda: FakeTerminal())
    host = FakeHost()
    open_in_terminal.OPERATION.start(_sel(items), host)
    assert opened == [items[0].parent, items[2]]  # a file's folder once, the folder itself
    assert host.calls == []

    many = []
    for i in range(open_in_terminal.MAX_WINDOWS + 2):
        folder = tmp_path / f"d{i}"
        folder.mkdir()
        many.append(folder)
    opened.clear()
    open_in_terminal.OPERATION.start(_sel(many), host)
    assert len(opened) == open_in_terminal.MAX_WINDOWS
    assert host.calls == [("notify", "Opened the first 5 of 7 folders")]

    monkeypatch.setattr(desktop, "find_terminal", lambda: None)
    with pytest.raises(JobError, match="No terminal program found"):
        open_in_terminal.OPERATION.start(_sel(items), host)


class FakeApps:
    def __init__(self, apps, by_type, defaults):
        self.apps = {a.id: a for a in apps}
        self.by_type = by_type
        self.defaults = defaults
        self.mime = desktop.MimeDatabase()

    def for_types(self, mimes):
        lists = [self.by_type.get(m, []) for m in mimes]
        common = set.intersection(*(set(x) for x in lists))
        return [self.apps[i] for i in lists[0] if i in common]

    def default_for(self, mime):
        app_id = self.defaults.get(mime)
        return self.apps.get(app_id) if app_id else None

    def shown(self):
        return sorted(self.apps.values(), key=lambda a: a.name)

    def get(self, app_id):
        return self.apps.get(app_id)


@pytest.fixture
def apps(monkeypatch):
    viewer = _app("viewer.desktop", name="Viewer", exec="viewer %F", icon="viewer")
    gimp = _app("gimp.desktop", name="GIMP", exec="gimp %U")
    editor = _app("editor.desktop", name="Editor", exec="editor %F")
    vim = _app("vim.desktop", name="Vim", exec="vim %F", terminal=True)
    db = FakeApps(
        [viewer, gimp, editor, vim],
        {
            "image/png": ["gimp.desktop", "viewer.desktop"],
            "text/plain": ["editor.desktop", "vim.desktop"],
        },
        {"image/png": "viewer.desktop"},
    )
    monkeypatch.setattr(desktop.AppDatabase, "load", classmethod(lambda cls: db))
    launched = []
    monkeypatch.setattr(
        desktop,
        "launch",
        lambda app, files, terminal=None: launched.append((app.id, files, terminal)),
    )
    monkeypatch.setattr(desktop, "find_terminal", lambda: "TERM")
    return launched


def test_open_with_lists_matching_apps_default_first(items, apps):
    host = FakeHost(choice="viewer.desktop")
    open_with.OPERATION.start(_sel(items[:1]), host)
    _, title, choices, more, more_label = host.calls[0]
    assert title == "Open photo.png with"
    assert [c.label for c in choices] == ["Viewer", "GIMP"]  # the default first
    assert choices[0].icon == "viewer"
    assert [c.label for c in more] == ["Editor", "Vim"]
    assert more_label == "Other Applications"
    assert apps == [("viewer.desktop", [items[0]], None)]


def test_open_with_terminal_app_and_cancel(items, apps):
    open_with.OPERATION.start(_sel(items[1:2]), FakeHost(choice="vim.desktop"))
    assert apps == [("vim.desktop", [items[1]], "TERM")]
    apps.clear()
    open_with.OPERATION.start(_sel(items[1:2]), FakeHost(choice=None))
    assert apps == []
    with pytest.raises(JobError, match="no longer installed"):
        open_with.OPERATION.start(_sel(items[1:2]), FakeHost(choice="gone.desktop"))


def test_open_with_mixed_types_has_no_common_app(items, apps):
    host = FakeHost()
    open_with.OPERATION.start(_sel(items[:2]), host)
    _, title, choices, more, _ = host.calls[0]
    assert title == "Open 2 items with" and choices == []
    assert len(more) == 4


def test_mime_types_of_a_selection(items):
    db = desktop.AppDatabase({}, desktop.Associations(), desktop.MimeDatabase())
    assert open_with.mime_types(_sel(items), db) == ["image/png", "text/plain", "inode/directory"]


def test_open_in_terminal_folders():
    sel = Selection.from_paths([Path("/nonexistent/a.txt"), Path("/nonexistent/b.txt")])
    assert open_in_terminal.folders(sel) == [Path("/nonexistent")]
