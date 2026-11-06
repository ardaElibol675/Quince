"""Organize, in the PDF Editor: turn, duplicate, delete pages, add blank pages and pages of
other PDFs or pictures. Moving pages is done in the grid itself (drag, Alt+arrows)."""

from __future__ import annotations

from quince.ui.editors.base import Mode, separator, tool_button, toolbar_row

HINT = "Drag to move · R turns · Delete removes · drop PDFs or pictures to add pages"


class OrganizePart:
    def __init__(self, editor) -> None:
        self.editor = editor
        widget, row = toolbar_row()

        def button(text, icon, tip, slot):
            b = tool_button(text, icon=icon, tip=tip)
            b.clicked.connect(slot)
            row.addWidget(b)
            return b

        self.left_button = button(
            "Turn Left", "object-rotate-left", "Turn the selected pages left  Shift+R",
            lambda: editor.rotate(-1),
        )  # fmt: skip
        self.right_button = button(
            "Turn Right", "object-rotate-right", "Turn the selected pages right  R",
            lambda: editor.rotate(1),
        )  # fmt: skip
        row.addWidget(separator())
        self.duplicate_button = button(
            "Duplicate", "edit-copy", "A copy of each selected page after it  Ctrl+D",
            editor.duplicate,
        )  # fmt: skip
        self.blank_button = button(
            "Blank Page", "document-new", "A blank page after the current one  B",
            editor.add_blank,
        )  # fmt: skip
        self.delete_button = button(
            "Delete", "edit-delete", "Remove the selected pages  Delete", editor.delete
        )
        row.addWidget(separator())
        self.add_button = button(
            "Add Pages…", "list-add", "Add the pages of PDFs or pictures  Ctrl+O",
            editor.add_pages_dialog,
        )  # fmt: skip
        row.addStretch(1)
        self.mode = Mode(
            "organize", "Organize", icon="quince-organize", tip="Move, turn, delete and add pages",
            toolbar=widget, hint=HINT,
        )  # fmt: skip

    def sync(self) -> None:
        some = bool(self.editor.grid.selected)
        for b in (self.left_button, self.right_button, self.duplicate_button, self.delete_button):
            b.setEnabled(some)
