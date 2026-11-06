"""Video snapshots: save frames of a video as pictures.

M marks a frame (again on a marker removes it); Save writes every marked frame, or the frame
under the playhead when nothing is marked. One picture is saved as ``name-snapshot.png``,
several go into a folder ``name/`` as ``name-01.png``, ``name-02.png``, ... in time order.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLabel

from quince.backends import mediaedit
from quince.core.jobs import JobContext
from quince.core.operations import JobRequest
from quince.ui.editors.media import marks as m
from quince.ui.editors.media._common import MediaEditor

FORMATS = (("png", "PNG (lossless)"), ("jpg", "JPG (smaller)"))


class SnapshotsEditor(MediaEditor):
    name = "video.snapshots"
    title = "Video Snapshots"
    save_label = "Save Picture"
    needs_video = True
    marks_are_state = True

    def __init__(self, selection, data, parent=None) -> None:
        super().__init__(selection, data, parent)
        self.add_tool(QLabel("Format"))
        self.format_combo = QComboBox()
        for key, label in FORMATS:
            self.format_combo.addItem(label, key)
        self.format_combo.currentIndexChanged.connect(lambda _i: self.commit())
        self.add_tool(self.format_combo)
        self.toolbar.addStretch(1)
        self.media_hint("M marks a frame · Save writes the marked frames (or the one shown)")
        self._refresh()
        self.start_history()

    @property
    def image_format(self) -> str:
        return self.format_combo.currentData()

    def state(self) -> tuple:
        return (self.marks, self.image_format)

    def set_state(self, state: tuple) -> None:
        marks, image_format = state
        self.format_combo.blockSignals(True)
        self.format_combo.setCurrentIndex(self.format_combo.findData(image_format))
        self.format_combo.blockSignals(False)
        self.set_marks(marks, record=False)

    def can_save(self) -> bool:
        return True  # the frame under the playhead

    def times(self) -> tuple[float, ...]:
        return self.marks.markers or (self.position,)

    def marks_changed(self) -> None:
        self._refresh()

    def on_position(self, t: float) -> None:
        if not self.marks.markers:
            self._refresh()

    def _refresh(self) -> None:
        count = len(self.marks.markers)
        if count:
            self.info.setText(f"{count} marked frame{'s' if count != 1 else ''}")
        else:
            self.info.setText(f"Frame at {m.clock(self.position)}")
        self.save_button.setText("Save Picture" if count <= 1 else f"Save {count} Pictures")

    def build_job(self) -> JobRequest:
        source, image_format, times = self.source, self.image_format, self.times()
        path = source.path

        def run(ctx: JobContext) -> None:
            if len(times) == 1:
                out = ctx.new_output(path, extension=image_format, suffix="snapshot")
                shots = [(times[0], out)]
            else:
                folder = ctx.new_output(path, extension="", directory=True)
                width = max(2, len(str(len(times))))
                shots = [
                    (t, folder / f"{path.stem}-{n:0{width}d}.{image_format}")
                    for n, t in enumerate(times, 1)
                ]
            mediaedit.snapshots(ctx, source, shots, image_format)

        return JobRequest(run)


EDITOR = SnapshotsEditor
