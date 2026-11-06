"""The Qt side of ``quince.core.operations.Host``: what ``Operation.start`` steps may use.

Every method runs on the GUI thread. Dialogs and menus are modal (they run a nested event
loop) and are only shown after the drag is over, when no other program grabs the pointer.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

from PySide6.QtCore import QEventLoop, QObject, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMenu, QSplitter, QWidget

from quince.core import forms
from quince.core.forms import Form, Values
from quince.core.operations import Choice, Report
from quince.integrations.files import show_in_files
from quince.ui.dialogs.options import FormDialog
from quince.ui.dialogs.order import OrderDialog
from quince.ui.dialogs.report import ReportDialog
from quince.ui.feedback.toast import Toast
from quince.ui.theme.icons import themed

log = logging.getLogger(__name__)

ERROR_TOAST_MS = 7000
DIALOG_SIZE = (860, 540)
SIDEBAR_WIDTH = 210
_ICON_SUFFIXES = (".png", ".svg", ".xpm")
COMPUTE_MESSAGE_MS = 600  # show the "working" message only when it takes longer
T = TypeVar("T")


def icon_for(name: str) -> QIcon:
    """A theme icon by name, or an image file for absolute paths (as in desktop files)."""
    if not name:
        return QIcon()
    if os.path.isabs(name):
        return QIcon(name)
    if name.endswith(_ICON_SUFFIXES):
        name = name.rsplit(".", 1)[0]
    return themed(name)


def _standard_places() -> list[Path]:
    places = [Path.home()]
    for location in (
        QStandardPaths.StandardLocation.DesktopLocation,
        QStandardPaths.StandardLocation.DocumentsLocation,
        QStandardPaths.StandardLocation.DownloadLocation,
        QStandardPaths.StandardLocation.PicturesLocation,
        QStandardPaths.StandardLocation.MusicLocation,
        QStandardPaths.StandardLocation.MoviesLocation,
    ):
        path = Path(QStandardPaths.writableLocation(location))
        if path.is_dir() and path not in places:
            places.append(path)
    return places


def folder_dialog(
    title: str, *, accept_label: str, start: Path | None, recent: Sequence[Path]
) -> QFileDialog:
    """The destination chooser: Qt's own dialog, so the sidebar can list the recent
    destinations (GTK's native dialog cannot be given extra places)."""
    dialog = QFileDialog(None, title, str(start or Path.home()))
    dialog.setFileMode(QFileDialog.FileMode.Directory)
    dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setLabelText(QFileDialog.DialogLabel.Accept, accept_label)
    places = [Path.home(), *(p for p in recent if p != Path.home())]
    places += [p for p in _standard_places() if p not in places]
    dialog.setSidebarUrls([QUrl.fromLocalFile(str(p)) for p in places])
    dialog.setViewMode(QFileDialog.ViewMode.List)
    dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    dialog.resize(*DIALOG_SIZE)
    # Widgets of Qt's dialog, by their object names: no file type filter for folders, and
    # a sidebar wide enough for folder names.
    for name in ("fileTypeCombo", "fileTypeLabel"):
        if (widget := dialog.findChild(QWidget, name)) is not None:
            widget.hide()
    if (splitter := dialog.findChild(QSplitter, "splitter")) is not None:
        splitter.setSizes([SIDEBAR_WIDTH, DIALOG_SIZE[0] - SIDEBAR_WIDTH])
    return dialog


def choice_menu(
    title: str, choices: Sequence[Choice], more: Sequence[Choice], more_label: str
) -> QMenu:
    menu = QMenu()
    header = menu.addAction(title)
    header.setEnabled(False)
    menu.addSeparator()
    if not choices:
        menu.addAction("No suggested applications").setEnabled(False)
    for choice in choices:
        menu.addAction(icon_for(choice.icon), choice.label).setData(choice.key)
    if more:
        menu.addSeparator()
        sub = menu.addMenu(more_label or "More")
        for choice in more:
            sub.addAction(icon_for(choice.icon), choice.label).setData(choice.key)
    return menu


class QtHost:
    def __init__(
        self,
        toast: Toast,
        shelf: Callable[[], QWidget],
        *,
        show_files: Callable[[Sequence[Path]], None] = show_in_files,
        run_dialog: Callable[[QFileDialog], Path | None] | None = None,
        run_menu: Callable[[QMenu], str | None] | None = None,
        run_form: Callable[[FormDialog], Values | None] | None = None,
        run_order: Callable[[OrderDialog], list[Path] | None] | None = None,
        run_report: Callable[[ReportDialog], str | None] | None = None,
        options_file: Path | None = None,
    ) -> None:
        self._toast = toast
        self._shelf = shelf  # created on first use
        self._show_files = show_files
        self._run_dialog = run_dialog or _exec_dialog
        self._run_menu = run_menu or _exec_menu
        self._run_form = run_form or _exec_form
        self._run_order = run_order or _exec_order
        self._run_report = run_report or _exec_report
        self._options_file = options_file

    def choose_folder(
        self, title: str, *, accept_label: str, start: Path | None, recent: Sequence[Path]
    ) -> Path | None:
        dialog = folder_dialog(title, accept_label=accept_label, start=start, recent=recent)
        try:
            return self._run_dialog(dialog)
        finally:
            dialog.deleteLater()

    def choose(
        self,
        title: str,
        choices: Sequence[Choice],
        *,
        more: Sequence[Choice] = (),
        more_label: str = "",
    ) -> str | None:
        menu = choice_menu(title, choices, more, more_label)
        try:
            return self._run_menu(menu)
        finally:
            menu.deleteLater()

    def set_clipboard_text(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)

    def notify(self, text: str) -> None:
        self._toast.show_message(text)

    def notify_error(self, text: str) -> None:
        self._toast.show_message(text, ERROR_TOAST_MS)

    def show_in_files(self, paths: Sequence[Path]) -> None:
        self._show_files(list(paths))

    def float_items(self, paths: Sequence[Path]) -> None:
        self._shelf().add(list(paths))

    def ask_options(self, form: Form) -> Values | None:
        dialog = FormDialog(form, forms.initial_values(form, self._options_file))
        try:
            values = self._run_form(dialog)
        finally:
            dialog.deleteLater()
        if values is not None:
            forms.remember(form, values, self._options_file)
        return values

    def ask_order(
        self, title: str, paths: Sequence[Path], *, accept_label: str
    ) -> list[Path] | None:
        dialog = OrderDialog(title, list(paths), accept_label=accept_label)
        try:
            return self._run_order(dialog)
        finally:
            dialog.deleteLater()

    def compute(self, message: str, work: Callable[[], T]) -> T:
        return run_off_gui_thread(work, on_slow=lambda: self.notify(message))

    def show_report(self, report: Report) -> str | None:
        dialog = ReportDialog(report)
        try:
            return self._run_report(dialog)
        finally:
            dialog.deleteLater()


class _Done(QObject):
    finished = Signal()


def run_off_gui_thread(work: Callable[[], T], *, on_slow: Callable[[], None] | None = None) -> T:
    """Run ``work`` on a thread and wait in a local event loop (the UI keeps painting).

    Returns its result or raises its exception. ``on_slow`` runs if it takes a while.
    """
    result: list = []
    error: list[BaseException] = []
    done = _Done()
    loop = QEventLoop()
    done.finished.connect(loop.quit, Qt.ConnectionType.QueuedConnection)

    def target() -> None:
        try:
            result.append(work())
        except BaseException as exc:  # handed to the caller
            error.append(exc)
        finally:
            done.finished.emit()

    timer = QTimer()
    timer.setSingleShot(True)
    if on_slow is not None:
        timer.timeout.connect(on_slow)
    thread = threading.Thread(target=target, name="quince-compute", daemon=True)
    QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
    try:
        thread.start()
        timer.start(COMPUTE_MESSAGE_MS)
        if thread.is_alive():
            loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        thread.join()
    finally:
        timer.stop()
        QApplication.restoreOverrideCursor()
    if error:
        raise error[0]
    return result[0]


def _exec_dialog(dialog: QFileDialog) -> Path | None:
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selectedFiles():
        return None
    return Path(dialog.selectedFiles()[0])


def _present(dialog: QDialog) -> int:
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog.exec()


def _exec_form(dialog: FormDialog) -> Values | None:
    if _present(dialog) != QDialog.DialogCode.Accepted:
        return None
    return dialog.values()


def _exec_order(dialog: OrderDialog) -> list[Path] | None:
    if _present(dialog) != QDialog.DialogCode.Accepted:
        return None
    return dialog.paths()


def _exec_report(dialog: ReportDialog) -> str | None:
    _present(dialog)
    return dialog.chosen


def _exec_menu(menu: QMenu) -> str | None:
    action = menu.exec(QCursor.pos())
    if action is None or action.data() is None:
        return None
    return str(action.data())
