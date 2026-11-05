from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, Qt, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

from quince.core.history import History
from quince.core.jobs import JobError, JobSnapshot, JobState
from quince.ui.home import paste as pasting
from quince.ui.home.window import MainWindow
from quince.ui.library.model import LibraryModel


@pytest.fixture
def library(tmp_path):
    return LibraryModel(History(tmp_path / "history.sqlite3"))


@pytest.fixture
def some_files(tmp_path):
    paths = []
    for name in ("a.png", "b.png", "c.pdf"):
        (tmp_path / name).write_bytes(b"x")
        paths.append(tmp_path / name)
    return paths


@pytest.fixture
def window(qtbot, library, tmp_path):
    opened = []
    w = MainWindow(library, open_file=opened.append, choose_files=lambda _p, _d: [])
    w.opened = opened
    w.requests = []
    w.wheel_requested.connect(lambda paths, wheel, at: w.requests.append((paths, wheel)))
    qtbot.addWidget(w)
    return w


def _done(job_id, op_id, outputs, title="Convert to JPG", state=JobState.SUCCEEDED):
    return JobSnapshot(job_id, title, op_id, (Path("/src/a.png"),), state=state,
                       outputs=tuple(outputs))  # fmt: skip


# --- Library model ---------------------------------------------------------------


def test_library_records_convert_and_tool_outputs_only(library, qtbot, tmp_path):
    changes = []
    library.changed.connect(lambda: changes.append(1))
    library.on_job_event(_done(1, "convert.image.jpg", [tmp_path / "a.jpg"]))
    library.on_job_event(_done(2, "tool.compress", [tmp_path / "a-small.jpg"], "Compress"))
    library.on_job_event(_done(3, "action.copy", [tmp_path / "copy/a.png"], "Copy"))
    library.on_job_event(_done(4, "convert.image.jpg", [tmp_path / "x.jpg"], state=JobState.FAILED))
    library.on_job_event(_done(5, "convert.image.jpg", []))
    assert [e.name for e in library.entries()] == ["a-small.jpg", "a.jpg"]
    assert library.entries()[1].sources == (Path("/src/a.png"),)
    assert len(changes) == 2


# --- Home -------------------------------------------------------------------------


def test_drop_open_and_enter_ask_for_a_wheel(window, some_files):
    home = window.home
    home.add_files([str(p) for p in some_files[:2]])
    assert home.files.paths == some_files[:2]
    assert home.files.selected_paths() == some_files[:2]
    home.add_files([str(some_files[0]), str(some_files[2]), "/does/not/exist"])
    assert home.files.paths == some_files  # once each, missing ones skipped
    assert home.files.selected_paths() == [some_files[0], some_files[2]]
    QTest.keyClick(home.files, Qt.Key.Key_Return)
    assert window.requests == [([some_files[0], some_files[2]], None)]


def test_trigger_tap_on_the_list_opens_that_wheel(window, some_files):
    home = window.home
    home.add_files(some_files[:1])
    QTest.keyPress(home.files, Qt.Key.Key_Control)
    QTest.keyRelease(home.files, Qt.Key.Key_Control)
    assert window.requests == [([some_files[0]], "tools")]
    # Shift with another key (Shift+Down selects) is not a tap.
    QTest.keyPress(home.files, Qt.Key.Key_Shift)
    QTest.keyClick(home.files, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyRelease(home.files, Qt.Key.Key_Shift)
    assert len(window.requests) == 1


def test_delete_and_clear_only_empty_the_list(window, some_files):
    home = window.home
    home.add_files(some_files)
    home.files.clearSelection()
    home.files.item(1).setSelected(True)
    QTest.keyClick(home.files, Qt.Key.Key_Delete)
    assert home.files.paths == [some_files[0], some_files[2]]
    home.clear_files()
    assert home.files.paths == [] and all(p.exists() for p in some_files)


def test_open_files_dialog_adds_its_choice(qtbot, library, some_files):
    w = MainWindow(library, choose_files=lambda _parent, _start: [str(some_files[1])])
    qtbot.addWidget(w)
    w.home.open_files()
    assert w.home.files.paths == [some_files[1]]


def test_drop_zone_accepts_files_as_copy(window, some_files):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QDragEnterEvent, QDropEvent

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in some_files[:2]])
    zone = window.home.drop
    both = Qt.DropAction.MoveAction | Qt.DropAction.CopyAction
    shift = Qt.KeyboardModifier.ShiftModifier
    enter = QDragEnterEvent(zone.rect().center(), both, mime, Qt.MouseButton.LeftButton, shift)
    zone.dragEnterEvent(enter)
    assert enter.isAccepted() and enter.dropAction() == Qt.DropAction.CopyAction
    drop = QDropEvent(QPointF(zone.rect().center()), both, mime, Qt.MouseButton.LeftButton, shift)
    zone.dropEvent(drop)
    assert drop.dropAction() == Qt.DropAction.CopyAction
    assert window.home.files.paths == some_files[:2]


def test_freshly_made_lists_existing_outputs(window, library, tmp_path, qtbot):
    out = tmp_path / "made.jpg"
    out.write_bytes(b"x")
    library.on_job_event(_done(1, "convert.image.jpg", [tmp_path / "gone.jpg"]))
    library.on_job_event(_done(2, "convert.image.jpg", [out]))
    window.show_home()
    qtbot.waitUntil(lambda: window.home.fresh.paths == [out], timeout=1000)
    assert window.home.fresh.isVisible()
    window.home.fresh.item(0).setSelected(True)
    window.home.fresh.setCurrentRow(0)
    QTest.keyClick(window.home.fresh, Qt.Key.Key_Enter)
    assert window.requests == [([out], None)]


def test_shortcuts_switch_pages_and_escape_hides(window, qtbot):
    window.show_home()
    qtbot.waitExposed(window)
    window.show_library()
    assert window.tabs.currentWidget() is window.library
    window.show_home()
    assert window.tabs.currentWidget() is window.home
    window._escape()
    assert not window.isVisible()


# --- paste ------------------------------------------------------------------------


def test_paste_files_from_a_file_manager(some_files, tmp_path):
    mime = QMimeData()
    mime.setData(pasting.GNOME_COPIED,
                 ("copy\n" + "\n".join(QUrl.fromLocalFile(str(p)).toString()
                                       for p in some_files[:2])).encode())  # fmt: skip
    assert pasting.paste(mime, tmp_path / "pasted") == some_files[:2]
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(some_files[2]))])
    assert pasting.paste(mime, tmp_path / "pasted") == [some_files[2]]
    mime = QMimeData()
    mime.setText(f"{some_files[0]}\nfile://{some_files[1]}\n")
    assert pasting.paste(mime, tmp_path / "pasted") == some_files[:2]
    assert not (tmp_path / "pasted").exists()


def test_paste_image_and_text_are_saved_as_new_files(tmp_path, qtbot):
    folder = tmp_path / "pasted"
    image = QImage(4, 3, QImage.Format.Format_RGB32)
    image.fill(QColor("orange"))
    mime = QMimeData()
    mime.setImageData(image)
    [png] = pasting.paste(mime, folder)
    assert png.parent == folder and png.suffix == ".png"
    assert QImage(str(png)).size() == image.size()

    mime = QMimeData()
    mime.setText("Merhaba dünya\n/not/a/path")
    [txt] = pasting.paste(mime, folder)
    assert txt.read_text(encoding="utf-8") == "Merhaba dünya\n/not/a/path"
    assert not list(folder.glob(".quince-paste-*"))


def test_pasted_names_never_overwrite(tmp_path):
    now = datetime(2026, 11, 5, 14, 32, 10)
    first = pasting.save_text("a", tmp_path, now)
    second = pasting.save_text("b", tmp_path, now)
    assert first.name == "pasted-2026-11-05-143210.txt"
    assert second.name == "pasted-2026-11-05-143210 (1).txt"
    assert first.read_text() == "a"


def test_paste_nothing_usable(tmp_path):
    with pytest.raises(JobError):
        pasting.paste(QMimeData(), tmp_path)
    with pytest.raises(JobError):
        pasting.paste(None, tmp_path)


def test_home_paste_shows_where_it_was_saved(qtbot, library, tmp_path, monkeypatch):
    monkeypatch.setattr(pasting, "pasted_dir", lambda: tmp_path / "pasted")
    mime = QMimeData()
    mime.setText("hello")
    w = MainWindow(library, clipboard_mime=lambda: mime)
    qtbot.addWidget(w)
    w._paste()
    [added] = w.home.files.paths
    assert added.parent == tmp_path / "pasted"
    assert "Saved as" in w.home.status.text()
    w.home._clipboard_mime = lambda: QMimeData()
    w._paste()
    assert "no files" in w.home.status.text()
