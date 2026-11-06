"""Crop video: drag a box on the picture; the part inside is kept for the whole video as
``name-cropped`` (the picture is encoded again; sound and subtitles are copied).

The box works like Crop image (Shift keeps its shape, the Ratio list fixes it); the arrow keys
move the playhead here, so any frame can be checked. The size is rounded to even pixels,
which video encoders need.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLabel

from quince.backends import mediaedit
from quince.core.operations import JobRequest
from quince.ui.editors import rects
from quince.ui.editors.image.crop import RATIOS, CropTool
from quince.ui.editors.media._common import MediaEditor
from quince.ui.editors.rects import Box


class CropVideoEditor(MediaEditor):
    name = "video.crop"
    title = "Crop Video"
    save_label = "Crop"
    needs_video = True

    def __init__(self, selection, data, parent=None) -> None:
        super().__init__(selection, data, parent)
        w, h = self.source.size
        self.bounds: Box = (0.0, 0.0, float(w), float(h))
        self.box: Box = self.bounds
        self._updating = False
        self.add_tool(QLabel("Ratio"))
        self.ratio_combo = QComboBox()
        for key, label, _value in RATIOS:
            self.ratio_combo.addItem(label, key)
        self.ratio_combo.currentIndexChanged.connect(self._on_ratio)
        self.add_tool(self.ratio_combo)
        self.toolbar.addStretch(1)
        self.canvas.tool = CropTool(self)
        self.media_hint("Drag to choose the area · Shift keeps the shape")
        self._sync()
        self.start_history()

    # --- what CropTool needs ------------------------------------------------------------

    def ratio(self) -> float | None:
        key = self.ratio_combo.currentData()
        if key == "original":
            return rects.aspect(self.source.size)
        return dict((k, v) for k, _label, v in RATIOS)[key]

    def set_box(self, box: Box) -> None:
        self.box = box
        self._sync()
        self.canvas.update()
        self.update_buttons()

    # --- state --------------------------------------------------------------------------

    def kept(self) -> tuple[int, int, int, int]:
        return mediaedit.even_box(self.box, self.source.size)

    def state(self) -> tuple:
        return (self.kept(), self.ratio_combo.currentData())

    def set_state(self, state: tuple) -> None:
        (x, y, w, h), ratio = state
        self._updating = True
        self.ratio_combo.setCurrentIndex(self.ratio_combo.findData(ratio))
        self._updating = False
        self.set_box((float(x), float(y), float(x + w), float(y + h)))

    def can_save(self) -> bool:
        w, h = self.source.size
        _x, _y, cw, ch = self.kept()
        return (cw, ch) != (w // 2 * 2, h // 2 * 2)

    def _on_ratio(self, _index: int) -> None:
        if self._updating:
            return
        ratio = self.ratio()
        if ratio:
            self.set_box(rects.fit_ratio(self.box, ratio, self.bounds))
        self.commit()

    def _sync(self) -> None:
        w, h = self.source.size
        _x, _y, cw, ch = self.kept()
        self.info.setText(f"{w} × {h} → {cw} × {ch}")

    def build_job(self) -> JobRequest:
        source, box = self.source, self.box
        return self.job(lambda ctx, out: mediaedit.crop(ctx, source, out, box), "cropped")


EDITOR = CropVideoEditor
