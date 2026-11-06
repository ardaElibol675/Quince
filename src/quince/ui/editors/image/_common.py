"""What the single-image editors share: loading the picture, showing it and the save job."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PIL import Image
from PySide6.QtCore import QSizeF
from PySide6.QtGui import QImage

from quince.backends import imageedit, painting
from quince.core.jobs import JobContext, JobError
from quince.core.operations import JobRequest
from quince.core.selection import Selection
from quince.ui.editors.base import EditorWindow

ANIMATION_HINT = "every frame of the animation is edited"


def single_file(selection: Selection):
    files = selection.files
    if len(files) != 1 or files[0].format_id not in imageedit.EDITABLE:
        raise JobError("This editor works on one image")
    return files[0]


class ImageEditor(EditorWindow):
    """An editor for one picture. ``self.loaded`` holds the preview and the full size."""

    @classmethod
    def load(cls, selection: Selection) -> imageedit.Loaded:
        item = single_file(selection)
        return imageedit.load(item.path, item.format_id)

    def __init__(self, selection: Selection, data: Any, parent=None) -> None:
        super().__init__(selection, data, parent)
        self.loaded: imageedit.Loaded = data
        self.item = selection.files[0]
        self.original = painting.to_qimage(self.loaded.preview)
        if self.loaded.animated:
            self.info.setToolTip(f"Animated: {ANIMATION_HINT}.")

    @property
    def image_size(self) -> tuple[int, int]:
        return self.loaded.size

    def full_size(self) -> QSizeF:
        return QSizeF(*self.image_size)

    def compare_image(self) -> tuple[QImage, QSizeF]:
        return self.original, self.full_size()

    def show_preview(self, image: Image.Image, logical: tuple[float, float]) -> None:
        self.canvas.set_image(painting.to_qimage(image), QSizeF(*logical))

    def job(
        self,
        transform: Callable[[Image.Image], Image.Image],
        suffix: str,
        *,
        target_format: str | None = None,
        keep_exif: bool = True,
        note: str = "",
    ) -> JobRequest:
        item = self.item
        target = target_format or imageedit.output_format(item.format_id)

        def run(ctx: JobContext) -> None:
            out = ctx.new_output(item.path, extension=target, suffix=suffix)
            imageedit.save_edited(
                ctx,
                item.path,
                item.format_id,
                out,
                transform,
                target_format=target,
                keep_exif=keep_exif,
            )
            if note:
                ctx.note(note)

        return JobRequest(run)


def size_text(size: tuple[float, float]) -> str:
    return f"{round(size[0])} × {round(size[1])}"
