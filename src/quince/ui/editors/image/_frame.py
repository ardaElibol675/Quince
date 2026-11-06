"""Frame, in the Image Editor: padding, a background color, rounded corners and a soft shadow
around the picture. Off until "Add a frame" is ticked (or a setting is changed); the last
settings are remembered."""

from __future__ import annotations

from dataclasses import asdict, replace

from PySide6.QtWidgets import QCheckBox, QPushButton

from quince.backends.imageedit import FrameStyle
from quince.core import forms
from quince.ui.editors.base import Mode, Slider, Swatches, heading, side_panel

REMEMBER_KEY = "editor.frame"
BACKGROUNDS = [
    ("White", (255, 255, 255, 255)),
    ("Light gray", (236, 236, 236, 255)),
    ("Dark gray", (40, 40, 44, 255)),
    ("Black", (0, 0, 0, 255)),
    ("Transparent", (0, 0, 0, 0)),
]
DEFAULT = FrameStyle(padding=0.06, radius=0.03, shadow=0.0)


def saved_style() -> FrameStyle:
    data = forms.load_section(REMEMBER_KEY)
    try:
        background = tuple(int(v) for v in data.get("background", DEFAULT.background))
        if len(background) != 4 or not all(0 <= v <= 255 for v in background):
            raise ValueError
        return FrameStyle(
            padding=min(0.3, max(0.0, float(data.get("padding", DEFAULT.padding)))),
            background=background,  # type: ignore[arg-type]
            radius=min(0.25, max(0.0, float(data.get("radius", DEFAULT.radius)))),
            shadow=min(1.0, max(0.0, float(data.get("shadow", DEFAULT.shadow)))),
        )
    except (TypeError, ValueError):
        return DEFAULT


class FramePart:
    def __init__(self, editor) -> None:
        self.editor = editor
        self.style = saved_style()  # shown while the frame is off
        panel, side = side_panel()
        self.enabled_box = QCheckBox("Add a frame")
        self.enabled_box.toggled.connect(self._on_enabled)
        side.addWidget(self.enabled_box)

        side.addSpacing(6)
        side.addWidget(heading("Padding"))
        self.padding = Slider(
            "Space around", 0, 30, round(self.style.padding * 100), default=6, show=self._pad_text
        )
        self.padding.changed.connect(lambda v: self._change("padding", padding=v / 100))
        side.addWidget(self.padding)
        side.addSpacing(6)
        side.addWidget(heading("Background"))
        self.background = Swatches(BACKGROUNDS, self.style.background, alpha=True)
        self.background.picked.connect(lambda c: self._change(None, background=tuple(c)))
        side.addWidget(self.background)
        side.addSpacing(6)
        side.addWidget(heading("Corners and Shadow"))
        self.radius = Slider(
            "Rounded corners", 0, 25, round(self.style.radius * 100), default=3,
            show=self._radius_text,
        )  # fmt: skip
        self.radius.changed.connect(lambda v: self._change("radius", radius=v / 100))
        side.addWidget(self.radius)
        self.shadow = Slider(
            "Shadow", 0, 100, round(self.style.shadow * 100), default=0,
            show=lambda v: f"{v} %" if v else "None",
        )  # fmt: skip
        self.shadow.changed.connect(lambda v: self._change("shadow", shadow=v / 100))
        side.addWidget(self.shadow)
        side.addStretch(1)
        reset = QPushButton("Reset Frame")
        reset.clicked.connect(lambda: self._set(DEFAULT))
        side.addWidget(reset)
        self.mode = Mode(
            "frame", "Frame", icon="insert-image", tip="Padding, background, corners, shadow",
            panel=panel, hint="A frame around the picture",
        )  # fmt: skip
        self.sync()

    def _short_side(self) -> int:
        return min(self.editor.edit.final_size(self.editor.image_size))

    def _pad_text(self, value: int) -> str:
        return f"{round(value / 100 * self._short_side())} px"

    def _radius_text(self, value: int) -> str:
        short = self._short_side()
        return f"{min(round(value / 100 * short), short // 2)} px"

    # --- state ----------------------------------------------------------------------------

    def sync(self) -> None:
        frame = self.editor.edit.frame
        if frame is not None:
            self.style = frame
        style = self.style
        self.enabled_box.blockSignals(True)
        self.enabled_box.setChecked(frame is not None)
        self.enabled_box.blockSignals(False)
        self.padding.set_value(round(style.padding * 100))
        self.radius.set_value(round(style.radius * 100))
        self.shadow.set_value(round(style.shadow * 100))
        self.background.set_color(style.background)

    def _on_enabled(self, on: bool) -> None:
        self.editor.change(replace(self.editor.edit, frame=self.style if on else None))

    def _change(self, merge: str | None, **changes) -> None:
        self.style = replace(self.style, **changes)
        self.editor.change(replace(self.editor.edit, frame=self.style), merge=merge)
        self.sync()

    def _set(self, style: FrameStyle) -> None:
        self.style = style
        self.editor.change(replace(self.editor.edit, frame=style))
        self.sync()

    def remember(self) -> None:
        if self.editor.edit.frame is not None:
            forms.save_section(REMEMBER_KEY, asdict(self.editor.edit.frame))
