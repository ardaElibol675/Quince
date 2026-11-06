"""Trim, in the Video and Sound Editors: keep only the part between In and Out.

Mark the part with I and O, then "Keep In-Out" (T in this mode); the rest is shown dark on
the timeline. A video is cut exactly on the chosen frame (the picture is encoded again);
with "Fast" and no other change, every stream is copied (no quality loss) and the part starts
at the keyframe at or before In, which the timeline shows as a dashed line.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import QCheckBox, QLabel

from quince.backends import mediaedit
from quince.ui.editors.base import Mode, tool_button, toolbar_row
from quince.ui.editors.media import marks as m

FAST_TIP = (
    "Copy the streams without encoding: fast and without quality loss, but the part starts "
    "at the keyframe at or before In. Only when nothing else is changed and the format stays."
)


class TrimPart:
    def __init__(self, editor, *, fast: bool) -> None:
        self.editor = editor
        widget, row = toolbar_row()
        self.keep_button = tool_button(
            "Keep In–Out", icon="edit-cut", tip="Keep the part between In and Out  T"
        )
        self.keep_button.clicked.connect(self.keep_marked)
        row.addWidget(self.keep_button)
        self.all_button = tool_button("Keep All", tip="Keep the whole file")
        self.all_button.clicked.connect(self.keep_all)
        row.addWidget(self.all_button)
        self.fast_box = QCheckBox("Fast (no re-encoding)")
        self.fast_box.setToolTip(FAST_TIP)
        self.fast_box.toggled.connect(self._on_fast)
        self.fast_box.setVisible(fast)
        row.addWidget(self.fast_box)
        row.addSpacing(10)
        self.label = QLabel()
        self.label.setEnabled(False)
        row.addWidget(self.label)
        row.addStretch(1)
        self.mode = Mode(
            "trim", "Trim", icon="edit-cut", tip="Keep a part", toolbar=widget,
            hint="I / O mark the part · T keeps it · Shift+Space plays it",
        )  # fmt: skip
        editor.mode_key(self.mode, "T", self.keep_marked)
        self.sync()

    def keep_marked(self) -> None:
        editor = self.editor
        if not editor.marks.has_range:
            editor.info.setText("Mark the part to keep with I and O first")
            return
        start, end = editor.marks.span(editor.duration)
        if end - start < mediaedit.MIN_PART_S:
            editor.info.setText("The marked part is too short")
            return
        editor.change(replace(editor.edit, trim=(start, end)))
        editor.set_marks(m.clear_range(editor.marks))

    def keep_all(self) -> None:
        self.editor.change(replace(self.editor.edit, trim=None))

    def _on_fast(self, on: bool) -> None:
        self.editor.change(replace(self.editor.edit, fast=on))

    def kept_start(self) -> float:
        """Where the result really starts (a fast cut starts at a keyframe)."""
        editor = self.editor
        start, _end = editor.edit.span(editor.duration)
        if editor.edit.fast and editor.fast_allowed():
            return mediaedit.snap_back(start, editor.media.keyframes)
        return start

    def sync(self) -> None:
        editor = self.editor
        edit = editor.edit
        self.fast_box.blockSignals(True)
        self.fast_box.setChecked(edit.fast)
        self.fast_box.blockSignals(False)
        self.fast_box.setEnabled(editor.fast_allowed() or edit.fast)
        self.all_button.setEnabled(edit.trim is not None)
        if edit.trim is None:
            self.label.setText("Whole file")
            return
        start, end = edit.trim
        real = self.kept_start()
        text = f"Keeps {m.clock(real)} – {m.clock(end)} ({m.length_text(end - real)})"
        if abs(real - start) > 1e-3:
            text += " · starts at a keyframe"
        self.label.setText(text)
