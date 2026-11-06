"""Redact audio: cover parts of a sound (or of a video's sound) with a bleep or silence,
saved as ``name-redacted``.

Mark a part with I and O and press A (Add) to hide it; parts are red bands on the timeline.
Click a band to pick it, Delete removes it. Every sound track of a video is changed; the
picture is copied. While playing, the hidden parts are muted.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QButtonGroup

from quince.backends import mediaedit
from quince.core.formats import Category
from quince.core.operations import JobRequest
from quince.ui.editors.base import separator, tool_button
from quince.ui.editors.media import marks as m
from quince.ui.editors.media._common import MediaEditor

STYLES = (
    ("bleep", "Bleep", "1", "A 1 kHz tone over the part"),
    ("silence", "Silence", "2", "The part becomes silent"),
)


class RedactAudioEditor(MediaEditor):
    name = "audio.redact"
    title = "Redact Audio"
    save_label = "Save Redacted"
    categories = frozenset({Category.AUDIO, Category.VIDEO})
    needs_audio = True
    wants_wave = True

    def __init__(self, selection, data, parent=None) -> None:
        super().__init__(selection, data, parent)
        self.spans: tuple[tuple[float, float], ...] = ()
        self.selected: int | None = None
        self.styles = QButtonGroup(self)
        self.styles.setExclusive(True)
        window = Qt.ShortcutContext.WindowShortcut
        for key, label, shortcut, tip in STYLES:
            button = tool_button(label, tip=f"{tip}  {shortcut}", checkable=True)
            button.setProperty("quince_style", key)
            button.setChecked(key == "bleep")
            self.styles.addButton(button)
            self.add_tool(button)
            QShortcut(QKeySequence(shortcut), self, lambda b=button: b.click(), context=window)
        self.styles.buttonClicked.connect(lambda _b: self._changed())
        self.add_tool(separator())
        self.add_button = tool_button("Add", icon="list-add", tip="Hide the part from In to Out  A")
        self.add_button.clicked.connect(self.add_span)
        self.add_tool(self.add_button)
        self.delete_button = tool_button("Delete", icon="edit-delete", tip="Delete  Del")
        self.delete_button.clicked.connect(self.delete_selected)
        self.add_tool(self.delete_button)
        self.toolbar.addStretch(1)
        QShortcut(QKeySequence("A"), self, self.add_span, context=window)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, self.delete_selected, context=window)
        self.media_hint("I / O mark a part, A hides it")
        self._refresh()
        self.start_history()

    @property
    def style(self) -> str:
        button = self.styles.checkedButton()
        return button.property("quince_style") if button is not None else "bleep"

    def state(self) -> tuple:
        return (self.spans, self.style)

    def set_state(self, state: tuple) -> None:
        self.spans, style = state
        for button in self.styles.buttons():
            button.setChecked(button.property("quince_style") == style)
        if self.selected is not None and self.selected >= len(self.spans):
            self.selected = None
        self._refresh()

    def can_save(self) -> bool:
        return bool(self.spans) and super().can_save()

    def add_span(self) -> None:
        if not self.marks.has_range:
            self.info.setText("Mark the part with I and O first")
            return
        start, end = self.marks.span(self.duration)
        self.spans = mediaedit.merge_spans((*self.spans, (start, end)))
        self.selected = None
        self.set_marks(m.clear_range(self.marks))
        self._changed()

    def delete_selected(self) -> None:
        if self.selected is None:
            return
        spans = list(self.spans)
        del spans[self.selected]
        self.spans = tuple(spans)
        self.selected = None
        self._changed()

    def timeline_pressed(self, t: float) -> None:
        hits = [n for n, (a, b) in enumerate(self.spans) if a <= t <= b]
        self.selected = hits[-1] if hits else None
        self._refresh()

    def on_position(self, t: float) -> None:
        self.player.set_muted(any(a <= t <= b for a, b in self.spans))

    def _changed(self) -> None:
        self._refresh()
        self.commit()

    def _refresh(self) -> None:
        self.timeline.set_spans(self.spans, self.selected)
        self.delete_button.setEnabled(self.selected is not None)
        total = sum(b - a for a, b in self.spans)
        count = len(self.spans)
        self.info.setText(
            f"{count} part{'s' if count != 1 else ''} · {m.length_text(total)}" if count else ""
        )
        self.update_buttons()

    def build_job(self) -> JobRequest:
        source, spans, style = self.source, self.spans, self.style
        return self.job(
            lambda ctx, out: mediaedit.redact_audio(ctx, source, out, spans, style), "redacted"
        )


EDITOR = RedactAudioEditor
