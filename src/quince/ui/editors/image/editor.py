"""The Image Editor: crop, adjust (turn, mirror, size, colors), redact, annotate and frame one
picture in one window, saved as one new file.

Modes (Ctrl+1 … Ctrl+5): Crop, Adjust, Redact, Annotate, Frame. Each mode has its own
toolbar or side panel and keys; one undo history covers them all. The preview shows the
result as far as the mode needs it: Crop shows the whole (turned) picture with the box,
Redact and Annotate the cropped picture without the frame, Adjust and Frame the full result.

Save asks for the name (``name-edited``), the format (the source's; PNG when the result has
transparency the source format cannot keep) and the folder. Every edit is applied in the
order of ``quince.backends.imagepipeline``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from PySide6.QtCore import QSizeF
from PySide6.QtGui import QImage

from quince.backends import imageedit, imaging, painting
from quince.backends import imagepipeline as pipeline
from quince.backends.imagepipeline import ImageEdit
from quince.core import deps
from quince.core.jobs import JobContext
from quince.core.operations import JobRequest
from quince.core.selection import Selection
from quince.ui.editors.base import EditorWindow, Mode, debounced
from quince.ui.editors.image._adjust import AdjustPart
from quince.ui.editors.image._annotate import AnnotatePart
from quince.ui.editors.image._common import ANIMATION_HINT, single_file, size_text
from quince.ui.editors.image._crop import CropPart, rounded_crop
from quince.ui.editors.image._frame import FramePart
from quince.ui.editors.image._redact import RedactPart
from quince.ui.editors.save_dialog import OutputChoice, SaveTarget

# The formats an edited picture can be saved in, in this order.
SAVE_FORMATS = ("jpg", "png", "webp", "heic", "avif", "tiff", "bmp", "gif")
FORMAT_DEPS = {"heic": "pillow-heif"}


def _available(format_id: str) -> bool:
    dep_id = FORMAT_DEPS.get(format_id)
    if dep_id is None:
        return True
    dep = deps.find(dep_id)
    return dep is not None and deps.check(dep, versions=False).available


class ImageEditorWindow(EditorWindow):
    name = "image.edit"
    title = "Image Editor"

    @classmethod
    def load(cls, selection: Selection) -> imageedit.Loaded:
        item = single_file(selection)
        return imageedit.load(item.path, item.format_id)

    def __init__(self, selection: Selection, data: Any, parent=None) -> None:
        super().__init__(selection, data, parent)
        self.loaded: imageedit.Loaded = data
        self.item = selection.files[0]
        self.original = painting.to_qimage(self.loaded.preview)
        self.edit = ImageEdit()
        self._refresh = debounced(self, self.refresh, 15)
        if self.loaded.animated:
            self.info.setToolTip(f"Animated: {ANIMATION_HINT}.")

        self.crop_part = CropPart(self)
        self.adjust_part = AdjustPart(self)
        self.redact_part = RedactPart(self)
        self.annotate_part = AnnotatePart(self)
        self.frame_part = FramePart(self)
        self.parts = (
            self.crop_part,
            self.adjust_part,
            self.redact_part,
            self.annotate_part,
            self.frame_part,
        )
        for part in self.parts:
            self.add_mode(part.mode)
        self.set_mode("crop")
        self.start_history()

    @property
    def image_size(self) -> tuple[int, int]:
        return self.loaded.size

    # --- the edit -------------------------------------------------------------------------

    def change(self, edit: ImageEdit, *, merge: str | None = None, record: bool = True) -> None:
        """Show ``edit``; ``record`` makes it an undo step (False while dragging)."""
        self.edit = edit
        self._refresh.start()
        if record:
            self.commit(merge)

    def commit(self, merge: str | None = None) -> None:
        self.edit = self._settled(self.edit)
        super().commit(merge)
        for part in self.parts:
            if part is not self.annotate_part:  # keeps a text being typed
                part.sync()
        self._refresh.start()

    def _settled(self, edit: ImageEdit) -> ImageEdit:
        """Whole-pixel crop; a chosen size that follows a new crop; no size equal to the
        unchanged one."""
        size = self.image_size
        edit = replace(edit, crop=rounded_crop(edit.crop, size))
        before = self.history.current if self.history is not None else None
        adjust = edit.adjust
        if adjust.size is not None and isinstance(before, ImageEdit) and before.crop != edit.crop:
            old = replace(before, adjust=adjust).geometry(size).out_size
            new = edit.geometry(size).out_size
            adjust = replace(adjust, size=pipeline.rescaled_size(adjust.size, old, new))
        w, h = edit.geometry(size).out_size
        if adjust.size is not None and tuple(adjust.size) == (round(w), round(h)):
            adjust = replace(adjust, size=None)
        return replace(edit, adjust=adjust)

    def state(self) -> ImageEdit:
        return self.edit

    def set_state(self, state: ImageEdit) -> None:
        self.edit = state
        for part in self.parts:
            part.sync()
        self.refresh()

    def can_save(self) -> bool:
        return True  # unchanged, it can still be saved in another format or place

    # --- modes ----------------------------------------------------------------------------

    def leave_mode(self, mode: Mode) -> None:
        if mode is self.annotate_part.mode:
            self.annotate_part.finish_text()
        if mode is self.redact_part.mode:
            self.redact_part.select(None)
        if mode is self.annotate_part.mode:
            self.annotate_part.select(None)

    def mode_changed(self, old: Mode | None, new: Mode) -> None:
        for part in self.parts:
            part.sync()
        self.refresh()

    # --- preview --------------------------------------------------------------------------

    def _steps(self) -> dict[str, bool]:
        """What the preview of the current mode leaves out."""
        key = self.mode.key if self.mode is not None else "crop"
        if key == "crop":
            return {"crop": False, "frame": False}
        if key == "redact":
            return {"frame": False}
        if key == "annotate":
            return {"annotate": False, "frame": False}
        return {}

    def _logical(self) -> tuple[float, float]:
        key = self.mode.key if self.mode is not None else "crop"
        size = self.image_size
        if key == "crop":
            return self.edit.geometry(size, crop=False).out_size
        if key in ("redact", "annotate"):
            return self.edit.geometry(size).out_size
        return pipeline.framed_size(self.edit, size)

    def preview(self, **steps: bool):
        return pipeline.render(
            self.loaded.preview, self.edit, self.image_size, resize=False, **steps
        )

    def refresh(self) -> None:
        image = self.preview(**self._steps())
        self.canvas.set_image(painting.to_qimage(image), QSizeF(*self._logical()))
        final = pipeline.framed_size(self.edit, self.image_size)
        words = pipeline.describe(self.edit)
        text = f"{size_text(self.image_size)} → {size_text(final)}"
        if words:
            text += " · " + ", ".join(words)
        self.info.setText(text)

    def compare_image(self) -> tuple[QImage, QSizeF]:
        return self.original, QSizeF(*self.image_size)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "annotate_part"):
            self.annotate_part.on_resize()

    # --- saving ---------------------------------------------------------------------------

    def transparent(self) -> bool:
        return pipeline.has_transparency(self.preview())

    def output_choices(self) -> list[OutputChoice]:
        transparent = self.transparent()
        choices = []
        for format_id in SAVE_FORMATS:
            if not _available(format_id):
                continue
            notes = []
            if transparent and not imaging.TARGETS[format_id].alpha:
                notes.append("No transparency: transparent areas become white.")
            if self.loaded.animated and format_id not in imaging.ANIMATION_TARGETS:
                notes.append("Only the first frame of the animation is kept.")
            if format_id == "gif":
                notes.append("At most 256 colors.")
            choices.append(OutputChoice(format_id, note=" ".join(notes)))
        return choices

    def default_format(self) -> str:
        return imageedit.output_format(self.item.format_id, alpha=self.transparent())

    def before_save(self) -> None:
        self.annotate_part.finish_text()

    def remember(self) -> None:
        self.annotate_part.remember()
        self.frame_part.remember()

    def build_job(self, target: SaveTarget | None = None) -> JobRequest:
        assert target is not None
        item, edit, size = self.item, self.edit, self.image_size

        def run(ctx: JobContext) -> None:
            out = ctx.new_output(
                item.path,
                extension=target.extension,
                name=target.stem,
                target_dir=target.folder,
            )
            imageedit.save_edited(
                ctx,
                item.path,
                item.format_id,
                out,
                lambda im: pipeline.render(im, edit, size),
                target_format=target.format_id,
                keep_exif=not edit.redactions,
            )
            if edit.redactions:
                ctx.note(pipeline.REDACT_NOTE)

        return JobRequest(run)


EDITOR = ImageEditorWindow
