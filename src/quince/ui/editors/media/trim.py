"""Trim video / Trim audio: keep the part between In and Out as ``name-trimmed``.

A video is cut exactly on the chosen frame (the picture is encoded again); with "Fast" every
stream is copied (no quality loss) and the part starts at the keyframe at or before In, which
the timeline shows as a dashed line. Sound is copied when lossy and encoded when lossless.
"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox

from quince.backends import mediaedit
from quince.core.formats import Category
from quince.core.operations import JobRequest
from quince.ui.editors.media import marks as m
from quince.ui.editors.media._common import MediaEditor

FAST_TIP = (
    "Copy the streams without encoding: fast and without quality loss, but the part starts "
    "at the keyframe at or before In"
)


class TrimVideoEditor(MediaEditor):
    name = "video.trim"
    title = "Trim Video"
    save_label = "Trim"
    needs_video = True
    wants_keyframes = True
    marks_are_state = True
    offers_fast = True

    def __init__(self, selection, data, parent=None) -> None:
        super().__init__(selection, data, parent)
        self.fast_box = QCheckBox("Fast (no re-encoding)", self)
        self.fast_box.setToolTip(FAST_TIP)
        self.fast_box.toggled.connect(lambda _on: self._changed())
        if self.offers_fast:
            self.add_tool(self.fast_box)
            self.toolbar.addStretch(1)
        else:
            self.fast_box.hide()
        self.media_hint("I / O mark the part to keep · Shift+Space plays it")
        self._refresh()
        self.start_history()

    @property
    def fast(self) -> bool:
        return self.fast_box.isChecked()

    def state(self) -> tuple:
        return (self.marks, self.fast)

    def set_state(self, state: tuple) -> None:
        marks, fast = state
        self.fast_box.blockSignals(True)
        self.fast_box.setChecked(fast)
        self.fast_box.blockSignals(False)
        self.set_marks(marks, record=False)
        self._refresh()

    def _changed(self) -> None:
        self._refresh()
        self.commit()

    def marks_changed(self) -> None:
        self._refresh()

    def part(self) -> tuple[float, float]:
        """Where the result really starts and ends."""
        start, end = self.marks.span(self.duration)
        if self.fast:
            start = mediaedit.snap_back(start, self.media.keyframes)
        return start, end

    def can_save(self) -> bool:
        start, end = self.marks.span(self.duration)
        return (
            super().can_save()
            and self.marks.has_range
            and end - start >= mediaedit.MIN_PART_S
            and (start > 1e-3 or end < self.duration - 1e-3)
        )

    def _refresh(self) -> None:
        wanted, end = self.marks.span(self.duration)
        start, _ = self.part()
        moved = self.fast and abs(start - wanted) > 1e-3
        self.timeline.set_cuts([start] if moved else [])
        if not self.marks.has_range:
            self.info.setText("Mark the part to keep with I and O")
        else:
            text = f"Keeps {m.clock(start)} – {m.clock(end)} ({m.length_text(end - start)})"
            if moved:
                text += " · starts at a keyframe"
            self.info.setText(text)
        self.update_buttons()

    def build_job(self) -> JobRequest:
        source, fast = self.source, self.fast
        start, end = self.part()

        def work(ctx, out) -> None:
            mediaedit.cut(ctx, source, out, start, end, fast=fast)
            if fast:
                ctx.note("Cut without re-encoding (no quality loss)")

        return self.job(work, "trimmed")


class TrimAudioEditor(TrimVideoEditor):
    name = "audio.trim"
    title = "Trim Audio"
    categories = frozenset({Category.AUDIO})
    needs_video = False
    needs_audio = True
    wants_keyframes = False
    offers_fast = False


EDITORS = (TrimVideoEditor, TrimAudioEditor)
