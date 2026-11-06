"""Crop, in the Video Editor: drag a box on the picture; the part inside is kept for the
whole video. The box works like the Image Editor's (Shift keeps its shape, the Ratio list fixes
it); the arrow keys move the playhead here, so any frame can be checked. The size is rounded
to even pixels, which video encoders need."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import QLabel

from quince.backends import mediaedit
from quince.ui.editors import rects
from quince.ui.editors.base import Mode, tool_button, toolbar_row
from quince.ui.editors.canvas import ImageCanvas
from quince.ui.editors.image._crop import CropTool, ratio_combo, ratio_value
from quince.ui.editors.rects import Box


class VideoCropPart:
    def __init__(self, editor) -> None:
        self.editor = editor
        w, h = editor.source.size
        self.bounds: Box = (0.0, 0.0, float(w), float(h))
        widget, row = toolbar_row()
        row.addWidget(QLabel("Ratio"))
        self.ratio_combo = ratio_combo()
        self.ratio_combo.currentIndexChanged.connect(self._on_ratio)
        row.addWidget(self.ratio_combo)
        row.addSpacing(10)
        self.reset_button = tool_button("Whole Picture", tip="Keep the whole picture")
        self.reset_button.clicked.connect(self.reset)
        row.addWidget(self.reset_button)
        row.addSpacing(10)
        self.label = QLabel()
        self.label.setEnabled(False)
        row.addWidget(self.label)
        row.addStretch(1)
        self.tool = CropTool(self)
        self.mode = Mode(
            "crop", "Crop", icon="quince-crop", tip="Keep a part of the picture",
            toolbar=widget, tool=self.tool, hint="Drag to choose the area · Shift keeps the shape",
        )  # fmt: skip
        self.sync()

    # --- what CropTool needs ------------------------------------------------------------

    @property
    def canvas(self) -> ImageCanvas:
        return self.editor.canvas

    @property
    def box(self) -> Box:
        return self.editor.edit.crop or self.bounds

    def ratio(self) -> float | None:
        return ratio_value(self.ratio_combo.currentData(), self.editor.source.size)

    def set_box(self, box: Box) -> None:
        self.editor.change(replace(self.editor.edit, crop=box), record=False)
        self.sync()

    def commit(self, merge: str | None = None) -> None:
        self.editor.change(
            replace(self.editor.edit, crop=self.settled(self.editor.edit.crop)), merge=merge
        )

    def settled(self, crop: Box | None) -> Box | None:
        """Even whole pixels; None when it keeps the whole picture."""
        if crop is None:
            return None
        size = self.editor.source.size
        x, y, w, h = mediaedit.even_box(crop, size)
        if (w, h) == (size[0] // 2 * 2, size[1] // 2 * 2):
            return None
        return (float(x), float(y), float(x + w), float(y + h))

    # --- controls -----------------------------------------------------------------------

    def reset(self) -> None:
        self.editor.change(replace(self.editor.edit, crop=None))

    def _on_ratio(self, _index: int) -> None:
        ratio = self.ratio()
        if ratio:
            self.set_box(rects.fit_ratio(self.box, ratio, self.bounds))
            self.commit()

    def sync(self) -> None:
        w, h = self.editor.source.size
        crop = self.editor.edit.crop
        self.reset_button.setEnabled(crop is not None)
        if crop is None:
            self.label.setText(f"{w} × {h}")
        else:
            _x, _y, cw, ch = mediaedit.even_box(crop, (w, h))
            self.label.setText(f"{w} × {h} → {cw} × {ch}")
        self.canvas.update()
