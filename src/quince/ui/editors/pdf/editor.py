"""The PDF Editor: the pages of one or more PDFs in a grid, organized and split in one window.

Modes (Ctrl+1, Ctrl+2): Organize (turn, duplicate, delete, blank pages, add the pages of
other PDFs or pictures) and Split (Save Parts…). Pages are moved in the grid in both modes
(drag, Alt+arrows) and the editing keys of the grid work in both. One undo history covers
every change. Opened with several PDFs, all their pages are in the grid, one after the
other, and Save makes one PDF of them.

Save asks for the name (``name-edited``) and the folder; "Save Selection…" writes the
selected pages as a PDF of their own (``name-pages``) and keeps the editor open. Pages are
written by ``quince.backends.pdfedit``: text stays selectable and links keep working where
their target page is kept.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import QFileDialog, QLabel, QMenu, QMessageBox, QSlider

from quince.backends import pdfedit
from quince.backends.pdfedit import BLANK, Page, Source
from quince.core import filetype, formats, forms
from quince.core.jobs import JobContext, JobError
from quince.core.operations import JobRequest
from quince.core.selection import Selection
from quince.ui.editors.base import EditorWindow, Mode, tool_button
from quince.ui.editors.pdf._grid import CELL_RANGE, DEFAULT_CELL, GridView, PageGrid
from quince.ui.editors.pdf._organize import OrganizePart
from quince.ui.editors.pdf._split import SplitPart
from quince.ui.editors.pdf._thumbs import Thumbnails
from quince.ui.editors.save_dialog import OutputChoice, SaveTarget
from quince.ui.host import run_off_gui_thread

PDF_CHOICES = (OutputChoice("pdf"),)
REMEMBER_KEY = "pdf-editor"


def _saved_zoom() -> int:
    try:
        value = int(forms.load_section(REMEMBER_KEY).get("zoom", DEFAULT_CELL))
    except (TypeError, ValueError):
        return DEFAULT_CELL
    return min(max(value, CELL_RANGE[0]), CELL_RANGE[1])


def _file_filter() -> str:
    patterns = sorted(f"*.{ext}" for fid in pdfedit.ADDABLE for ext in formats.get(fid).extensions)
    return f"PDFs and pictures ({' '.join(patterns)})"


def load_files(paths: Sequence[Path]) -> tuple[list[Source], list[str]]:
    """Read every PDF or picture of ``paths`` (off the GUI thread); the sources and the
    reasons for the files that cannot be added."""
    sources: list[Source] = []
    problems: list[str] = []
    for path in paths:
        if path.is_dir():
            problems.append(f"{path.name}: a folder; {pdfedit.NOT_ADDABLE}")
            continue
        found = filetype.detect(path)
        format_id = found.id if found is not None else None
        if format_id not in pdfedit.ADDABLE:
            problems.append(f"{path.name}: {pdfedit.NOT_ADDABLE}")
            continue
        try:
            sources.append(pdfedit.load_source(path, format_id))
        except JobError as exc:
            problems.append(exc.reason)
    return sources, problems


class PdfEditor(EditorWindow):
    name = "pdf.edit"
    title = "PDF Editor"
    compare_label = ""
    items_word = "PDFs"

    @classmethod
    def load(cls, selection: Selection) -> list[Source]:
        files = selection.files
        if not files or any(item.format_id != "pdf" for item in files):
            raise JobError("The PDF Editor opens PDFs only")
        return [pdfedit.load_source(item.path, "pdf") for item in files]

    def __init__(self, selection: Selection, data: Any, parent=None) -> None:
        super().__init__(selection, data, parent)
        self.compute: Callable = run_off_gui_thread
        self.choose_files: Callable[[], list[Path]] = self._choose_files
        self.warn: Callable[[str, list[str]], None] = self._warn
        self.pages: tuple[Page, ...] = pdfedit.first_pages(self.sources)
        self._uids = itertools.count(len(self.pages))

        grid = self.grid
        grid.command.connect(self.run_command)
        grid.move_requested.connect(self.move_pages)
        grid.files_dropped.connect(self.add_files)
        grid.selection_changed.connect(self.sync)
        grid.menu_requested.connect(self.show_menu)

        self.organize_part = OrganizePart(self)
        self.split_part = SplitPart(self)
        for part in (self.organize_part, self.split_part):
            self.add_mode(part.mode)
        # The tools of the whole editor, right of the mode buttons.
        self.selection_button = self.add_tool(
            tool_button("Save Selection…", icon="document-save-as",
                        tip="Save the selected pages as a PDF of their own")
        )  # fmt: skip
        self.selection_button.clicked.connect(self.save_selection)
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(*CELL_RANGE)
        self.zoom.setFixedWidth(120)
        self.zoom.setToolTip("Page size  Ctrl+wheel")
        self.zoom.valueChanged.connect(grid.set_cell)
        grid.zoomed.connect(self._zoomed)
        self.add_tool(QLabel("Zoom"))
        self.add_tool(self.zoom)
        self.zoom.setValue(_saved_zoom())
        grid.set_cell(self.zoom.value())
        self.parts = (self.organize_part, self.split_part)
        QShortcut(
            QKeySequence.StandardKey.Open, self, self.add_pages_dialog,
            context=Qt.ShortcutContext.WindowShortcut,
        )  # fmt: skip
        grid.set_pages(self.pages, select=[self.pages[0].uid] if self.pages else [])
        self.set_mode("organize")
        self.start_history()
        self.sync()

    def create_canvas(self) -> GridView:
        self.sources: list[Source] = list(self.data)  # pages of added files are appended
        self.thumbnails = Thumbnails(self.sources)
        self.grid = PageGrid(self.sources, self.thumbnails)
        return GridView(self.grid)

    # --- the edit -------------------------------------------------------------------------

    def state(self) -> tuple[Page, ...]:
        return self.pages

    def set_state(self, state: tuple[Page, ...]) -> None:
        self.pages = state
        self.grid.set_pages(state)
        self.sync()

    def change(
        self, pages: Sequence[Page], *, select: Sequence[int] | None = None,
        current: int | None = None,
    ) -> None:  # fmt: skip
        self.pages = tuple(pages)
        self.grid.set_pages(self.pages, select, current)
        self.commit()

    def commit(self, merge: str | None = None) -> None:
        super().commit(merge)
        self.sync()

    def can_save(self) -> bool:
        return bool(self.pages)  # unchanged, several PDFs still become one; or another place

    def sync(self) -> None:
        if not hasattr(self, "parts"):
            return
        for part in self.parts:
            part.sync()
        count = len(self.pages)
        chosen = len(self.grid.selected)
        text = f"{count} page{'s' if count != 1 else ''}"
        if chosen:
            text += f" · {chosen} selected"
        self.info.setText(text)
        self.selection_button.setEnabled(bool(chosen))
        self.update_buttons()

    def _zoomed(self, cell: int) -> None:
        self.zoom.blockSignals(True)
        self.zoom.setValue(cell)
        self.zoom.blockSignals(False)

    # --- changes --------------------------------------------------------------------------

    def run_command(self, name: str) -> None:
        commands = {
            "delete": self.delete,
            "rotate_right": lambda: self.rotate(1),
            "rotate_left": lambda: self.rotate(-1),
            "duplicate": self.duplicate,
            "blank": self.add_blank,
            "earlier": lambda: self.shift(-1),
            "later": lambda: self.shift(1),
            "first": lambda: self.move_pages(self.grid.selected_uids(), 0),
            "last": lambda: self.move_pages(self.grid.selected_uids(), len(self.pages)),
        }
        commands[name]()

    def delete(self) -> None:
        uids = self.grid.selected_uids()
        if not uids:
            return
        first = self.grid.index_of(uids[0]) or 0
        pages = pdfedit.without(self.pages, uids)
        after = [pages[min(first, len(pages) - 1)].uid] if pages else []
        self.change(pages, select=after)

    def rotate(self, turns: int) -> None:
        uids = self.grid.selected_uids()
        if uids:
            self.change(pdfedit.rotated(self.pages, uids, turns))

    def duplicate(self) -> None:
        uids = self.grid.selected_uids()
        if uids:
            pages, copies = pdfedit.duplicated(self.pages, uids, self._uids)
            self.change(pages, select=copies)

    def _insertion_point(self) -> int:
        """After the current page, else after the last selected one, else at the end."""
        grid = self.grid
        if grid.current is not None and grid.current in grid.selected:
            return (grid.index_of(grid.current) or 0) + 1
        chosen = grid.selected_indices()
        return chosen[-1] + 1 if chosen else len(self.pages)

    def add_blank(self) -> None:
        index = self._insertion_point()
        size = pdfedit.blank_size_near(self.pages, self.sources, index)
        page = Page(next(self._uids), BLANK, blank_size=size)
        self.change(pdfedit.inserted(self.pages, index, [page]), select=[page.uid])

    def shift(self, step: int) -> None:
        uids = self.grid.selected_uids()
        if uids:
            self.change(pdfedit.shifted(self.pages, uids, step), current=self.grid.current)
            self._show_current()

    def move_pages(self, uids: Sequence[int], index: int) -> None:
        if uids:
            self.change(pdfedit.moved(self.pages, uids, index), current=self.grid.current)
            self._show_current()

    def _show_current(self) -> None:
        if self.grid.current is not None:
            index = self.grid.index_of(self.grid.current)
            if index is not None:
                self.grid.ensure_visible(index)

    def add_files(self, paths: Sequence[Path], index: int | None = None) -> None:
        if index is None:
            index = self._insertion_point()
        paths = [Path(p) for p in paths]
        loaded, problems = self.compute(lambda: load_files(paths))
        new: list[Page] = []
        for source in loaded:
            self.sources.append(source)
            new += pdfedit.source_pages(len(self.sources) - 1, source, self._uids)
        if new:
            self.change(
                pdfedit.inserted(self.pages, index, new), select=[p.uid for p in new],
                current=new[0].uid,
            )  # fmt: skip
        if problems:
            self.warn("Some files could not be added", problems)

    def add_pages_dialog(self) -> None:
        paths = self.choose_files()
        if paths:
            self.add_files(paths)

    def _choose_files(self) -> list[Path]:
        start = self.selection.files[0].path.parent
        names, _ = QFileDialog.getOpenFileNames(self, "Add Pages", str(start), _file_filter())
        return [Path(n) for n in names]

    def _warn(self, text: str, details: list[str]) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.title)
        box.setText(text)
        box.setInformativeText("\n".join(details))
        box.exec()

    def show_menu(self, where: QPoint) -> None:
        self.context_menu().exec(where)

    def context_menu(self) -> QMenu:
        menu = QMenu(self)
        some = bool(self.grid.selected)
        for text, keys, slot, needs in (
            ("Turn Right", "R", lambda: self.rotate(1), True),
            ("Turn Left", "Shift+R", lambda: self.rotate(-1), True),
            ("Duplicate", "Ctrl+D", self.duplicate, True),
            ("Blank Page After", "B", self.add_blank, False),
            ("Delete", "Delete", self.delete, True),
            (None, None, None, False),
            ("Save Selection…", None, self.save_selection, True),
        ):
            if text is None:
                menu.addSeparator()
                continue
            action = menu.addAction(text)
            if keys:
                action.setShortcut(QKeySequence(keys))
                action.setShortcutVisibleInContextMenu(True)
            action.triggered.connect(slot)
            action.setEnabled(some or not needs)
        return menu

    # --- modes ----------------------------------------------------------------------------

    def mode_changed(self, old: Mode | None, new: Mode) -> None:
        if new is self.split_part.mode:
            self.split_part.sync()
        else:
            self.grid.set_overlay(None)
        self.grid.setFocus()

    # --- saving ---------------------------------------------------------------------------

    def output_choices(self) -> list[OutputChoice]:
        return list(PDF_CHOICES)

    def _first(self) -> Path:
        return self.selection.files[0].path

    def build_job(self, target: SaveTarget | None = None) -> JobRequest:
        assert target is not None
        sources, pages, first = tuple(self.sources), self.pages, self._first()

        def run(ctx: JobContext) -> None:
            out = ctx.new_output(first, extension="pdf", name=target.stem,
                                 target_dir=target.folder)  # fmt: skip
            pdfedit.write(ctx, sources, pages, out)

        return JobRequest(run)

    def save_selection(self) -> None:
        indices = self.grid.selected_indices()
        if not indices:
            return
        target = self.ask_target(
            PDF_CHOICES, "pdf", f"{self._first().stem}-pages", title="Save Selection",
        )  # fmt: skip
        if target is None:
            return
        sources, first = tuple(self.sources), self._first()
        pages = tuple(self.pages[i] for i in indices)

        def run(ctx: JobContext) -> None:
            out = ctx.new_output(first, extension="pdf", name=target.stem,
                                 target_dir=target.folder)  # fmt: skip
            pdfedit.write(ctx, sources, pages, out)

        self.export(JobRequest(run))

    def save_parts(self) -> None:
        split = self.split_part
        try:
            parts = split.parts()
        except ValueError:
            return
        if not split.usable(parts):
            return
        target = self.ask_target(
            PDF_CHOICES, "pdf", f"{self._first().stem}-parts", title="Save Parts",
            folder_output=True, accept_label="Save Parts",
        )  # fmt: skip
        if target is None:
            return
        sources, pages, first = tuple(self.sources), self.pages, self._first()

        def run(ctx: JobContext) -> None:
            folder = ctx.new_output(first, extension="", directory=True, name=target.stem,
                                    target_dir=target.folder)  # fmt: skip
            width = max(2, len(str(len(parts))))
            outs = [folder / f"{target.stem}-{n:0{width}d}.pdf" for n in range(1, len(parts) + 1)]
            pdfedit.write_parts(ctx, sources, pages, parts, outs)

        self.export(JobRequest(run))

    def remember(self) -> None:
        forms.save_section(REMEMBER_KEY, {"zoom": self.grid.cell})

    def closeEvent(self, event: QCloseEvent) -> None:
        super().closeEvent(event)
        if event.isAccepted():
            self.remember()
            self.thumbnails.close()


EDITOR = PdfEditor
