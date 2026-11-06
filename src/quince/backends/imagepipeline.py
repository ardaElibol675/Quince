"""The Image Editor's result as data (``ImageEdit``) and the one function that makes it.

Every edit is applied in a fixed order:

    crop → turn / mirror → colors → redact → annotate → resize → frame

Redacted areas and drawn shapes are stored in pixels of the upright original, like the crop
box, and carried through the crop, the turns and the mirrors (``Geometry``) when the picture
is made. So they stay on the same part of the picture when the crop or the turns change
later, texts stay upright, and the parts outside the crop are simply cut off.

The editor applies ``render`` to its small preview and the job to the full picture, so the
saved file looks like the preview.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from PIL import Image

from quince.backends import imageedit, imaging, painting
from quince.backends.imageedit import Adjust, Box, FrameStyle, Redaction
from quince.backends.painting import Shape

Point = tuple[float, float]
MIN_AREA_PX = 1.0  # a redacted area cut smaller than this by the crop is left out


def _normalized(box: Box) -> Box:
    x0, y0, x1, y1 = box
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


@dataclass(frozen=True)
class Geometry:
    """The crop, the quarter turns (clockwise) and the mirrors of an edit, as a mapping from
    the upright original (``size``) to the picture after them. Mirrors apply after turns."""

    size: tuple[float, float]
    crop: Box | None = None
    rotate: int = 0
    flip_h: bool = False
    flip_v: bool = False

    @property
    def crop_box(self) -> Box:
        return self.crop if self.crop is not None else (0.0, 0.0, *self.size)

    @property
    def cropped_size(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.crop_box
        return x1 - x0, y1 - y0

    @property
    def out_size(self) -> tuple[float, float]:
        """The size of the picture after the crop and the turns."""
        w, h = self.cropped_size
        return (h, w) if self.rotate % 2 else (w, h)

    @property
    def bounds(self) -> Box:
        return (0.0, 0.0, *self.out_size)

    def forward(self, point: Point) -> Point:
        x0, y0, _x1, _y1 = self.crop_box
        x, y = point[0] - x0, point[1] - y0
        w, h = self.cropped_size
        for _ in range(self.rotate % 4):
            x, y = h - y, x
            w, h = h, w
        if self.flip_h:
            x = w - x
        if self.flip_v:
            y = h - y
        return x, y

    def inverse(self, point: Point) -> Point:
        x, y = point
        w, h = self.out_size
        if self.flip_v:
            y = h - y
        if self.flip_h:
            x = w - x
        for _ in range(self.rotate % 4):
            x, y = y, w - x
            w, h = h, w
        x0, y0, _x1, _y1 = self.crop_box
        return x + x0, y + y0

    def forward_box(self, box: Box) -> Box:
        a, b = self.forward((box[0], box[1])), self.forward((box[2], box[3]))
        return _normalized((*a, *b))

    def inverse_box(self, box: Box) -> Box:
        a, b = self.inverse((box[0], box[1])), self.inverse((box[2], box[3]))
        return _normalized((*a, *b))

    def forward_shape(self, shape: Shape) -> Shape:
        return _map_shape(shape, self.forward)

    def inverse_shape(self, shape: Shape) -> Shape:
        return _map_shape(shape, self.inverse)


def _map_shape(shape: Shape, mapping) -> Shape:
    if shape.kind != "text":
        return replace(shape, points=tuple(mapping(p) for p in shape.points))
    # A text stays upright: its middle follows the picture, not its corner.
    rect = painting.text_rect(shape)
    cx, cy = mapping((rect.center().x(), rect.center().y()))
    return replace(shape, points=((cx - rect.width() / 2, cy - rect.height() / 2),))


def _scaled_shape(shape: Shape, factor: float) -> Shape:
    if factor == 1.0:
        return shape
    points = tuple((x * factor, y * factor) for x, y in shape.points)
    return replace(shape, points=points, width=shape.width * factor)


def _clip(box: Box, bounds: Box) -> Box | None:
    x0, y0 = max(box[0], bounds[0]), max(box[1], bounds[1])
    x1, y1 = min(box[2], bounds[2]), min(box[3], bounds[3])
    if x1 - x0 < MIN_AREA_PX or y1 - y0 < MIN_AREA_PX:
        return None
    return x0, y0, x1, y1


@dataclass(frozen=True)
class ImageEdit:
    """Everything the Image Editor changes. Boxes and shapes are in pixels of the upright
    original; ``adjust.size`` is the final size (before the frame)."""

    crop: Box | None = None
    adjust: Adjust = field(default_factory=Adjust)
    redactions: tuple[Redaction, ...] = ()
    shapes: tuple[Shape, ...] = ()
    frame: FrameStyle | None = None

    def geometry(self, size: tuple[float, float], *, crop: bool = True) -> Geometry:
        a = self.adjust
        return Geometry(size, self.crop if crop else None, a.rotate % 4, a.flip_h, a.flip_v)

    def final_size(self, size: tuple[int, int]) -> tuple[int, int]:
        """The size after the crop, the turns and the resize (without the frame)."""
        if self.adjust.size is not None:
            return tuple(int(v) for v in self.adjust.size)  # type: ignore[return-value]
        w, h = self.geometry(size).out_size
        return max(1, round(w)), max(1, round(h))

    @property
    def is_empty(self) -> bool:
        return self == ImageEdit()


def redactions_in(edit: ImageEdit, geometry: Geometry) -> list[Redaction]:
    """The redacted areas in the picture after ``geometry``, cut to it."""
    found = []
    for item in edit.redactions:
        box = _clip(geometry.forward_box(item.box), geometry.bounds)
        if box is not None:
            found.append(replace(item, box=box))
    return found


def shapes_in(edit: ImageEdit, geometry: Geometry) -> list[Shape]:
    return [geometry.forward_shape(s) for s in edit.shapes if not s.is_empty]


def render(
    im: Image.Image,
    edit: ImageEdit,
    size: tuple[int, int],
    *,
    crop: bool = True,
    redact: bool = True,
    annotate: bool = True,
    resize: bool = True,
    frame: bool = True,
) -> Image.Image:
    """``edit`` applied to ``im``, the upright original of ``size`` or a smaller preview of
    it (then every box, shape and stroke is scaled down to match). The keyword switches
    leave steps out, for the editor's modes; a preview leaves out ``resize`` too."""
    factor = im.width / size[0] if size[0] else 1.0
    geometry = edit.geometry(size, crop=crop)
    if crop and edit.crop is not None:
        box = tuple(v * factor for v in edit.crop)
        im = imageedit.crop(im, box)  # type: ignore[arg-type]
    im = imageedit.adjust(im, replace(edit.adjust, size=None))
    if redact and edit.redactions:
        items = [i.scaled(factor) for i in redactions_in(edit, geometry)]
        im = imageedit.redact(im, items)
    if annotate and edit.shapes:
        im = painting.annotate(im, [_scaled_shape(s, factor) for s in shapes_in(edit, geometry)])
    if resize and edit.adjust.size is not None and tuple(edit.adjust.size) != im.size:
        w, h = (max(1, int(v)) for v in edit.adjust.size)
        im = im.resize((w, h), Image.Resampling.LANCZOS)
    if frame and edit.frame is not None:
        im = imageedit.frame(im, edit.frame)
    return im


def framed_size(edit: ImageEdit, size: tuple[int, int]) -> tuple[int, int]:
    """The size of the saved picture."""
    w, h = edit.final_size(size)
    if edit.frame is None:
        return w, h
    pad, _radius = edit.frame.pixels((w, h))
    return w + 2 * pad, h + 2 * pad


def has_transparency(im: Image.Image) -> bool:
    return im.mode == "RGBA" and im.getchannel("A").getextrema() != (255, 255)


def rescaled_size(
    size: tuple[int, int] | None, old: tuple[float, float], new: tuple[float, float]
) -> tuple[int, int] | None:
    """A chosen final ``size`` carried over when the picture before resizing changes from
    ``old`` to ``new`` (another crop): the same scale per side."""
    if size is None or not old[0] or not old[1]:
        return size
    return (
        max(1, round(new[0] * size[0] / old[0])),
        max(1, round(new[1] * size[1] / old[1])),
    )


def output_notes(target: str, transparent: bool, edit: ImageEdit) -> list[str]:
    """What the result card says about the chosen format."""
    notes = []
    if transparent and not imaging.TARGETS[target].alpha:
        notes.append(imaging.FLATTENED_NOTE)
    if edit.redactions:
        notes.append(REDACT_NOTE)
    return notes


REDACT_NOTE = "EXIF metadata was removed from the redacted copy"


def describe(edit: ImageEdit) -> Sequence[str]:
    """Short words for what is changed (the info line)."""
    words = []
    if edit.crop is not None:
        words.append("cropped")
    a = edit.adjust
    if a.rotate % 4 or a.flip_h or a.flip_v:
        words.append("turned")
    if a.colors_changed:
        words.append("colors")
    if a.size is not None:
        words.append("resized")
    if edit.redactions:
        words.append("redacted")
    if edit.shapes:
        words.append("annotated")
    if edit.frame is not None:
        words.append("framed")
    return words
