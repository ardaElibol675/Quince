"""Frame image: padding, a background color, rounded corners and a soft shadow.

The last settings are remembered. A transparent background saves a JPG (or BMP) as PNG.
"""

from __future__ import annotations

from dataclasses import asdict, replace

from PySide6.QtWidgets import QPushButton

from quince.backends import imageedit
from quince.backends.imageedit import FrameStyle
from quince.core import forms
from quince.core.operations import JobRequest
from quince.ui.editors.base import Slider, Swatches, debounced, heading
from quince.ui.editors.image._common import ImageEditor, size_text

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


class FrameEditor(ImageEditor):
    name = "image.frame"
    title = "Frame Image"
    side_panel = True

    def __init__(self, selection, data, parent=None) -> None:
        super().__init__(selection, data, parent)
        self.frame_style = saved_style()
        self._refresh = debounced(self, self.refresh)

        self.side.addWidget(heading("Padding"))
        self.padding = Slider(
            "Space around",
            0,
            30,
            round(self.frame_style.padding * 100),
            default=6,
            show=self._pad_text,
        )
        self.padding.changed.connect(lambda v: self._change(padding=v / 100, merge="padding"))
        self.side.addWidget(self.padding)

        self.side.addSpacing(6)
        self.side.addWidget(heading("Background"))
        self.background = Swatches(BACKGROUNDS, self.frame_style.background, alpha=True)
        self.background.picked.connect(lambda c: self._change(background=tuple(c)))
        self.side.addWidget(self.background)

        self.side.addSpacing(6)
        self.side.addWidget(heading("Corners and Shadow"))
        self.radius = Slider(
            "Rounded corners", 0, 25, round(self.frame_style.radius * 100), default=3,
            show=self._radius_text,
        )  # fmt: skip
        self.radius.changed.connect(lambda v: self._change(radius=v / 100, merge="radius"))
        self.side.addWidget(self.radius)
        self.shadow = Slider(
            "Shadow", 0, 100, round(self.frame_style.shadow * 100), default=0,
            show=lambda v: f"{v} %" if v else "None",
        )  # fmt: skip
        self.shadow.changed.connect(lambda v: self._change(shadow=v / 100, merge="shadow"))
        self.side.addWidget(self.shadow)
        self.side.addStretch(1)
        reset = QPushButton("Reset All")
        reset.clicked.connect(lambda: self._set(DEFAULT))
        self.side.addWidget(reset)

        self.refresh()
        self.start_history()

    def _pad_text(self, value: int) -> str:
        return f"{round(value / 100 * min(self.image_size))} px"

    def _radius_text(self, value: int) -> str:
        return f"{min(round(value / 100 * min(self.image_size)), min(self.image_size) // 2)} px"

    # --- state ------------------------------------------------------------------------

    def state(self) -> FrameStyle:
        return self.frame_style

    def set_state(self, state: FrameStyle) -> None:
        self.frame_style = state
        self.padding.set_value(round(state.padding * 100))
        self.radius.set_value(round(state.radius * 100))
        self.shadow.set_value(round(state.shadow * 100))
        self.background.set_color(state.background)
        self.refresh()

    def can_save(self) -> bool:
        return True  # the first look is already a frame

    def _change(self, merge: str | None = None, **changes) -> None:
        self.frame_style = replace(self.frame_style, **changes)
        self._refresh.start()
        self.commit(merge)

    def _set(self, style: FrameStyle) -> None:
        self.set_state(style)
        self.commit()

    def remember(self) -> None:
        forms.save_section(REMEMBER_KEY, asdict(self.frame_style))

    # --- result -------------------------------------------------------------------------

    def framed_size(self) -> tuple[int, int]:
        pad, _radius = self.frame_style.pixels(self.image_size)
        return self.image_size[0] + 2 * pad, self.image_size[1] + 2 * pad

    def target_format(self) -> str:
        return imageedit.output_format(self.item.format_id, alpha=self.frame_style.transparent)

    def refresh(self) -> None:
        self.show_preview(
            imageedit.frame(self.loaded.preview, self.frame_style), self.framed_size()
        )
        text = f"{size_text(self.image_size)} → {size_text(self.framed_size())}"
        if self.target_format() != imageedit.output_format(self.item.format_id):
            text += " · PNG (transparent)"
        self.info.setText(text)

    def build_job(self) -> JobRequest:
        style = self.frame_style
        return self.job(
            lambda im: imageedit.frame(im, style), "framed", target_format=self.target_format()
        )


EDITOR = FrameEditor
