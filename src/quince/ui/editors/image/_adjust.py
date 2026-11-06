"""Adjust, in the Image Editor: turn, mirror, resize and change brightness, contrast and
saturation.

Keys (in this mode): ``[`` / ``]`` turn left / right, ``H`` / ``V`` mirror horizontally /
vertically. The size is the final size of the (cropped) picture; it follows a later crop at
the same scale.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import QCheckBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox

from quince.backends.imageedit import Adjust
from quince.ui.editors.base import Mode, Slider, heading, side_panel, tool_button

MAX_SIDE = 30000
COLOR_KEYS = (("brightness", "Brightness"), ("contrast", "Contrast"), ("saturation", "Saturation"))


def _signed(value: int) -> str:
    return f"{value:+d}" if value else "0"


class AdjustPart:
    def __init__(self, editor) -> None:
        self.editor = editor
        self._updating = False
        panel, side = side_panel()
        side.addWidget(heading("Rotate and Flip"))
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
        side.addLayout(turns)

        side.addSpacing(8)
        side.addWidget(heading("Size"))
        size_row = QHBoxLayout()
        self.width_spin = self._spin("width")
        self.height_spin = self._spin("height")
        size_row.addWidget(self.width_spin)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.height_spin)
        size_row.addWidget(QLabel("px"))
        side.addLayout(size_row)
        self.keep_ratio = QCheckBox("Keep proportions")
        self.keep_ratio.setChecked(True)
        side.addWidget(self.keep_ratio)
        self.base_label = QLabel()
        self.base_label.setEnabled(False)
        side.addWidget(self.base_label)

        side.addSpacing(8)
        side.addWidget(heading("Colors"))
        self.sliders: dict[str, Slider] = {}
        for key, label in COLOR_KEYS:
            slider = Slider(label, -100, 100, 0, show=_signed)
            slider.changed.connect(lambda v, k=key: self._on_color(k, v))
            self.sliders[key] = slider
            side.addWidget(slider)
        side.addStretch(1)
        reset = QPushButton("Reset Adjustments")
        reset.clicked.connect(self.reset)
        side.addWidget(reset)

        self.mode = Mode(
            "adjust",
            "Adjust",
            icon="object-rotate-right",
            tip="Turn, mirror, resize and colors",
            panel=panel,
            hint="[ ] turn · H / V mirror",
        )
        for keys, slot in (
            ("[", lambda: self.rotate(-1)),
            ("]", lambda: self.rotate(1)),
            ("H", self.flip_h),
            ("V", self.flip_v),
        ):
            editor.mode_key(self.mode, keys, slot)
        self.sync()

    def _spin(self, side: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, MAX_SIDE)
        spin.setKeyboardTracking(False)
        spin.valueChanged.connect(lambda _v: self._on_size(side))
        return spin

    @property
    def adjust(self) -> Adjust:
        return self.editor.edit.adjust

    def base_size(self) -> tuple[int, int]:
        """The size before resizing: cropped and turned."""
        w, h = self.editor.edit.geometry(self.editor.image_size).out_size
        return max(1, round(w)), max(1, round(h))

    def final_size(self) -> tuple[int, int]:
        return self.adjust.size or self.base_size()

    def _change(self, adjust: Adjust, merge: str | None = None) -> None:
        self.editor.change(replace(self.editor.edit, adjust=adjust), merge=merge)

    def sync(self) -> None:
        self._updating = True
        w, h = self.final_size()
        self.width_spin.setValue(w)
        self.height_spin.setValue(h)
        for key, slider in self.sliders.items():
            slider.set_value(getattr(self.adjust, key))
        self._updating = False
        bw, bh = self.base_size()
        self.base_label.setText(f"Before resizing: {bw} × {bh}")

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

    def _on_size(self, side: str) -> None:
        if self._updating:
            return
        w, h = self.width_spin.value(), self.height_spin.value()
        if self.keep_ratio.isChecked():
            bw, bh = self.base_size()
            if side == "width":
                h = max(1, round(w * bh / bw))
            else:
                w = max(1, round(h * bw / bh))
        self._change(replace(self.adjust, size=(w, h)), merge="size")
