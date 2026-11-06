"""The Quince window: Home and Library.

Home
- A drop area: drop files (always answered as Copy), click it or press Ctrl+O to open files,
  Ctrl+V to paste files, an image or text (see ``paste.py``).
- "Files": what was added in this session. Select files and press Enter for a wheel, or tap
  a trigger key (Shift / Ctrl / Alt by default) for that wheel; taps switch the open wheel.
- "Freshly made": the newest outputs from the Library that still exist.

Library: see ``quince.ui.library.page``. Ctrl+F searches it. Esc or Ctrl+W hides the window;
the service keeps running.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCursor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QGuiApplication,
    QKeySequence,
    QMouseEvent,
    QPalette,
    QShortcut,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from quince.core import config
from quince.core.jobs import JobError
from quince.input.taps import TapDetector
from quince.ui.files import FileList
from quince.ui.home import paste as pasting
from quince.ui.keys import keysym_name
from quince.ui.library.model import LibraryModel
from quince.ui.library.page import LibraryPage, format_date
from quince.ui.settings.model import trigger_hint
from quince.ui.shelf.window import default_open, dropped_paths

log = logging.getLogger(__name__)

FRESH_COUNT = 8
STATUS_MS = 6000
WINDOW_SIZE = (720, 620)
HOME_TAB, LIBRARY_TAB = 0, 1
DROP_ACCENT = (226, 128, 38)  # the wheel's orange


class DropZone(QFrame):
    """Drop files here, or click to open some."""

    files_dropped = Signal(list)
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(120)
        self.title = QLabel("Drop files here")
        font = self.title.font()
        font.setPointSizeF(font.pointSizeF() * 1.3)
        font.setBold(True)
        self.title.setFont(font)
        self.subtitle = QLabel("or click to open files (Ctrl+O) · paste with Ctrl+V")
        for label in (self.title, self.subtitle):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)
        layout.addStretch(1)
        self._set_hover(False)

    def _set_hover(self, hover: bool) -> None:
        # Colors derived from the text color, so the border shows in light and dark themes.
        red, green, blue = DROP_ACCENT
        if hover:
            border = f"2px solid rgb({red}, {green}, {blue})"
            background = f"background: rgba({red}, {green}, {blue}, 28);"
        else:
            text = self.palette().color(QPalette.ColorRole.WindowText)
            border = f"2px dashed rgba({text.red()}, {text.green()}, {text.blue()}, 90)"
            background = ""
        self.setStyleSheet(f"#dropzone {{ border: {border}; border-radius: 12px; {background} }}")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._set_hover(False)  # light / dark changed: the border follows the text color

    def _accept(self, event: QDragEnterEvent | QDragMoveEvent | QDropEvent) -> bool:
        if not dropped_paths(event.mimeData()):
            event.ignore()
            return False
        # Copy, never Move: the drag source must not delete what it dropped here.
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        return True

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        self._set_hover(self._accept(event))

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        self._accept(event)

    def dragLeaveEvent(self, event) -> None:
        self._set_hover(False)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_hover(False)
        if self._accept(event):
            self.files_dropped.emit(dropped_paths(event.mimeData()))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()


class _TapFilter(QObject):
    """Trigger taps on a file list open that wheel for the selected files."""

    def __init__(self, lists: Sequence[FileList], on_tap: Callable[[FileList, str], None]):
        super().__init__()
        self._on_tap = on_tap
        self.taps = TapDetector()
        for widget in lists:
            widget.installEventFilter(self)  # keys
            widget.viewport().installEventFilter(self)  # clicks

    def configure(self, cfg: config.Config) -> None:
        self.taps = TapDetector(cfg.triggers.keys, max_tap_ms=cfg.triggers.max_tap_ms)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind == QEvent.Type.KeyPress and not event.isAutoRepeat():
            self.taps.press(keysym_name(event), event.timestamp())
        elif kind == QEvent.Type.KeyRelease and not event.isAutoRepeat():
            wheel = self.taps.release(keysym_name(event), event.timestamp())
            if wheel is not None:
                self._on_tap(obj, wheel)
        elif kind == QEvent.Type.MouseButtonPress:
            self.taps.button()
        elif kind == QEvent.Type.FocusOut:
            self.taps.reset()
        return False


def _section(title: str, button: QPushButton | None = None) -> tuple[QHBoxLayout, QLabel]:
    label = QLabel(title)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    row = QHBoxLayout()
    row.addWidget(label, 1)
    if button is not None:
        row.addWidget(button)
    return row, label


class HomePage(QWidget):
    wheel_requested = Signal(list, object, QPoint)  # paths, wheel name or None, global point
    library_requested = Signal()

    def __init__(
        self,
        library: LibraryModel,
        *,
        open_file: Callable[[Path], None] = default_open,
        choose_files: Callable[[QWidget, Path], list[str]] | None = None,
        clipboard_mime: Callable[[], object] | None = None,
    ) -> None:
        super().__init__()
        self.library = library
        self._choose_files = choose_files or _choose_files
        self._clipboard_mime = clipboard_mime or (lambda: QGuiApplication.clipboard().mimeData())
        self._last_dir = Path.home()
        self._fresh_dirty = True

        self.drop = DropZone()
        self.drop.files_dropped.connect(self.add_files)
        self.drop.clicked.connect(self.open_files)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self.status.hide)

        self.clear_button = QPushButton("Clear")
        self.clear_button.setToolTip("Empty the list (the files stay)")
        self.clear_button.clicked.connect(self.clear_files)
        files_row, self.files_label = _section("Files", self.clear_button)
        self.files = FileList(open_file=open_file)
        self.files.wheel_requested.connect(lambda p, at: self.wheel_requested.emit(p, None, at))
        self.files.delete_pressed.connect(self.remove_selected)
        self.hint = QLabel(trigger_hint(config.DEFAULT_TRIGGERS))
        self.hint.setEnabled(False)  # dimmed

        self.library_button = QPushButton("Library")
        self.library_button.setToolTip("Everything Quince made (Ctrl+F searches it)")
        self.library_button.clicked.connect(self.library_requested)
        fresh_row, _label = _section("Freshly made", self.library_button)
        self.fresh = FileList(open_file=open_file)
        self.fresh.wheel_requested.connect(lambda p, at: self.wheel_requested.emit(p, None, at))
        self.fresh_empty = QLabel("Files you convert or make with a tool show up here.")
        self.fresh_empty.setEnabled(False)

        self.taps = _TapFilter([self.files, self.fresh], self._on_tap)

        layout = QVBoxLayout(self)
        layout.addWidget(self.drop)
        layout.addWidget(self.status)
        layout.addLayout(files_row)
        layout.addWidget(self.files, 3)
        layout.addWidget(self.hint)
        layout.addSpacing(6)
        layout.addLayout(fresh_row)
        layout.addWidget(self.fresh, 2)
        layout.addWidget(self.fresh_empty)
        layout.addStretch(0)

        library.changed.connect(self._on_library_changed)
        self._update_files()
        self.refresh_fresh()

    # --- adding files ---------------------------------------------------------------

    def add_files(self, paths: Sequence[str | Path]) -> list[Path]:
        """Add files to the list (once each) and select them."""
        new = [Path(p) for p in paths]
        new = [p for p in dict.fromkeys(new) if p.exists()]
        if not new:
            return []
        rows = [*self.files.paths, *(p for p in new if p not in self.files.paths)]
        self.files.set_paths([(p, str(p)) for p in rows], select=new)
        self.files.setFocus()
        self._last_dir = new[-1].parent
        log.info("home: added %s", ", ".join(str(p) for p in new))
        self._update_files()
        return new

    def open_files(self) -> None:
        chosen = self._choose_files(self.window(), self._last_dir)
        if chosen:
            self.add_files(chosen)

    def paste(self) -> None:
        try:
            added = self.add_files(pasting.paste(self._clipboard_mime()))
        except JobError as exc:
            self.show_status(exc.reason)
            return
        if added and added[0].parent == pasting.pasted_dir():
            self.show_status(f"Saved as {added[0].name} in {added[0].parent}")

    def show_status(self, text: str) -> None:
        self.status.setText(text)
        self.status.show()
        self._status_timer.start(STATUS_MS)

    def clear_files(self) -> None:
        self.files.set_paths([])
        self._update_files()

    def remove_selected(self) -> None:
        gone = set(self.files.selected_paths())
        self.files.set_paths([(p, str(p)) for p in self.files.paths if p not in gone])
        self._update_files()

    # --- freshly made ---------------------------------------------------------------

    def refresh_fresh(self) -> None:
        self._fresh_dirty = False
        entries = self.library.recent(FRESH_COUNT)
        rows = [(e.path, f"{e.path}\n{e.title} · {format_date(e.created)}") for e in entries]
        self.fresh.set_paths(rows)
        self.fresh.setVisible(bool(entries))
        self.fresh_empty.setVisible(not entries)

    def _on_library_changed(self) -> None:
        if self.isVisible():
            self.refresh_fresh()
        else:
            self._fresh_dirty = True

    def showEvent(self, event) -> None:
        if self._fresh_dirty:
            self.refresh_fresh()
        else:  # files may have been deleted meanwhile
            QTimer.singleShot(0, self, self.refresh_fresh)
        super().showEvent(event)

    # --- internals -------------------------------------------------------------------

    def _update_files(self) -> None:
        count = self.files.count()
        self.files_label.setText(f"Files · {count}" if count else "Files")
        self.clear_button.setEnabled(count > 0)
        self.hint.setVisible(count > 0)

    def _on_tap(self, widget: FileList, wheel: str) -> None:
        paths = widget.selected_paths()
        if paths:
            self.wheel_requested.emit(paths, wheel, widget.wheel_point())


class MainWindow(QWidget):
    wheel_requested = Signal(list, object, QPoint)  # paths, wheel name or None, global point

    def __init__(self, library: LibraryModel, **home_options) -> None:
        super().__init__()
        self.setWindowTitle("Quince")
        self.resize(*WINDOW_SIZE)
        self.home = HomePage(library, **home_options)
        self.library = LibraryPage(library)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.home, "Home")
        self.tabs.addTab(self.library, "Library")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)

        self.home.wheel_requested.connect(self.wheel_requested)
        self.home.library_requested.connect(self.show_library)
        self.library.wheel_requested.connect(lambda p, at: self.wheel_requested.emit(p, None, at))

        for keys, slot in (
            (QKeySequence.StandardKey.Open, self._open_files),
            (QKeySequence.StandardKey.Find, self.show_library),
            (QKeySequence.StandardKey.Close, self.hide),
            (QKeySequence("Ctrl+1"), lambda: self.tabs.setCurrentIndex(HOME_TAB)),
            (QKeySequence("Ctrl+2"), lambda: self.tabs.setCurrentIndex(LIBRARY_TAB)),
        ):
            QShortcut(keys, self, slot)
        # Ctrl+V pastes into a focused text field (the Library search) as usual.
        self.paste_shortcut = QShortcut(QKeySequence.StandardKey.Paste, self, self._paste)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._escape)

    def configure(self, cfg: config.Config) -> None:
        """Apply the config (trigger keys); called before every show and after Settings."""
        self.home.taps.configure(cfg)
        self.home.hint.setText(trigger_hint(cfg.triggers.keys))

    def show_home(self) -> None:
        self.tabs.setCurrentIndex(HOME_TAB)
        self._present()
        self.home.files.setFocus()

    def show_library(self) -> None:
        self.tabs.setCurrentIndex(LIBRARY_TAB)
        self._present()
        self.library.focus_search()

    def _present(self) -> None:
        if not self.isVisible():
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            frame = self.frameGeometry()
            frame.moveCenter(screen.availableGeometry().center())
            self.move(frame.topLeft())
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def _open_files(self) -> None:
        self.tabs.setCurrentIndex(HOME_TAB)
        self.home.open_files()

    def _paste(self) -> None:
        self.tabs.setCurrentIndex(HOME_TAB)
        self.home.paste()

    def _escape(self) -> None:
        if self.tabs.currentIndex() == LIBRARY_TAB and self.library.search.text():
            self.library.search.clear()
        else:
            self.hide()


def _choose_files(parent: QWidget, start: Path) -> list[str]:
    paths, _filter = QFileDialog.getOpenFileNames(parent, "Open Files", str(start))
    return paths
