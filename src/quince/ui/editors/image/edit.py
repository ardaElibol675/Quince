"""Edit image: turn, mirror, resize and change brightness, contrast and saturation.

Keys: ``[`` / ``]`` turn left / right, ``H`` / ``V`` mirror horizontally / vertically.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QCheckBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox

from quince.backends import imageedit
from quince.backends.imageedit import Adjust
from quince.core.operations import JobRequest
from quince.ui.editors.base import Slider, debounced, heading, tool_button
from quince.ui.editors.image._common import ImageEditor, size_text

MAX_SIDE = 30000


def _signed(value: int) -> str:
    return f"{value:+d}" if value else "0"


class EditEditor(ImageEditor):
    name = "image.edit"
    title = "Edit Image"
    side_panel = True

    def __init__(self, selection, data, parent=None) -> None:
        super().__init__(selection, data, parent)
        self.adjust = Adjust()
        self._updating = False
        self._refresh = debounced(self, self.refresh)

        self.side.addWidget(heading("Rotate and Flip"))
        turns = QGridLayout()
        for n, (text, icon, tip, slot) in enumerate(
            (
                ("Left", "object-rotate-left", "Turn left  [", lambda: self.rotate(-1)),
                ("Right", "object-rotate-right", "Turn right  ]", lambda: self.rotate(1)),
                ("Mirror", "object-flip-horizontal", "Mirror left to right  H", self.flip_h),
                ("Flip", "object-flip-vertical", "Flip upside down  V", self.flip_v),
            )
        ):
            button = tool_button(text, icon=icon, tip=tip, raised=True)
            button.clicked.connect(slot)
            turns.addWidget(button, n // 2, n % 2)
        self.side.addLayout(turns)

        self.side.addSpacing(8)
        self.side.addWidget(heading("Size"))
        size_row = QHBoxLayout()
        self.width_spin = self._spin()
        self.height_spin = self._spin()
        size_row.addWidget(self.width_spin)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.height_spin)
        size_row.addWidget(QLabel("px"))
        self.side.addLayout(size_row)
        self.keep_ratio = QCheckBox("Keep proportions")
        self.keep_ratio.setChecked(True)
        self.side.addWidget(self.keep_ratio)
        self.original_label = QLabel(f"Original: {size_text(self.image_size)}")
        self.original_label.setEnabled(False)
        self.side.addWidget(self.original_label)

        self.side.addSpacing(8)
        self.side.addWidget(heading("Colors"))
        self.sliders: dict[str, Slider] = {}
        for key, label in (
            ("brightness", "Brightness"),
            ("contrast", "Contrast"),
            ("saturation", "Saturation"),
        ):
            slider = Slider(label, -100, 100, 0, show=_signed)
            slider.changed.connect(lambda v, k=key: self._on_color(k, v))
            self.sliders[key] = slider
            self.side.addWidget(slider)
        self.side.addStretch(1)
        reset = QPushButton("Reset All")
        reset.clicked.connect(self.reset)
        self.side.addWidget(reset)

        for keys, slot in (
            ("[", lambda: self.rotate(-1)),
            ("]", lambda: self.rotate(1)),
            ("H", self.flip_h),
            ("V", self.flip_v),
        ):
            QShortcut(QKeySequence(keys), self, slot, context=Qt.ShortcutContext.WindowShortcut)

        self.set_hint("[ ] turn · H / V mirror")
        self._sync_controls()
        self.refresh()
        self.start_history()

    def _spin(self) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, MAX_SIDE)
        spin.setKeyboardTracking(False)
        spin.valueChanged.connect(self._on_size)
        return spin

    # --- state ------------------------------------------------------------------------

    def state(self) -> Adjust:
        return self.adjust

    def set_state(self, state: Adjust) -> None:
        self.adjust = state
        self._sync_controls()
        self.refresh()

    def final_size(self) -> tuple[int, int]:
        return self.adjust.size or self.adjust.turned_size(self.image_size)

    def _change(self, adjust: Adjust, merge: str | None = None) -> None:
        if adjust.size is not None and adjust.size == adjust.turned_size(self.image_size):
            adjust = replace(adjust, size=None)
        self.adjust = adjust
        self._sync_controls()
        self._refresh.start()
        self.commit(merge)

    def _sync_controls(self) -> None:
        self._updating = True
        w, h = self.final_size()
        self.width_spin.setValue(w)
        self.height_spin.setValue(h)
        for key, slider in self.sliders.items():
            slider.set_value(getattr(self.adjust, key))
        self._updating = False
        self.info.setText(f"{size_text(self.image_size)} → {size_text((w, h))}")

    # --- actions ------------------------------------------------------------------------

    def rotate(self, quarter_turns: int) -> None:
        self._change(self.adjust.rotated(quarter_turns))

    def flip_h(self) -> None:
        self._change(replace(self.adjust, flip_h=not self.adjust.flip_h))

    def flip_v(self) -> None:
        self._change(replace(self.adjust, flip_v=not self.adjust.flip_v))

    def reset(self) -> None:
        self._change(Adjust())

    def _on_color(self, key: str, value: int) -> None:
        if not self._updating:
            self._change(replace(self.adjust, **{key: value}), merge=key)

    def _on_size(self, _value: int) -> None:
        if self._updating:
            return
        w, h = self.width_spin.value(), self.height_spin.value()
        if self.keep_ratio.isChecked():
            tw, th = self.adjust.turned_size(self.image_size)
            if self.sender() is self.width_spin:
                h = max(1, round(w * th / tw))
            else:
                w = max(1, round(h * tw / th))
        self._change(replace(self.adjust, size=(w, h)), merge="size")

    # --- result -------------------------------------------------------------------------

    def refresh(self) -> None:
        preview = imageedit.adjust(self.loaded.preview, self.adjust, resize=False)
        self.show_preview(preview, self.final_size())

    def build_job(self) -> JobRequest:
        adjust = self.adjust
        return self.job(lambda im: imageedit.adjust(im, adjust), "edited")


EDITOR = EditEditor
