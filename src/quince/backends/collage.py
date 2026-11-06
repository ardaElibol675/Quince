"""Collage: several pictures on one canvas (the Create collage editor).

Layouts (``layout`` works on aspect ratios only, so the editor's small preview and the job's
full-size result are placed alike):

- ``grid``: equal cells, ``columns`` per row; the cell shape is the typical (median) picture
  shape. Pictures fill their cell (cropped at the center) or, with ``fill=False``, fit in it.
  A shorter last row is centered.
- ``mosaic``: rows of ``columns`` pictures that keep their shape, each row as high as needed to
  span the width. A shorter last row keeps the height of the others and is centered.
- ``row``: every picture side by side, equally high. ``column``: stacked, equally wide.

``gap`` (between pictures and around them) is a fraction of the width. Pictures with a color
profile are converted to sRGB, and the collage is saved without EXIF.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from quince.backends import imageedit, imaging
from quince.core.jobs import JobContext

LAYOUTS = (("grid", "Grid"), ("mosaic", "Mosaic"), ("row", "Row"), ("column", "Column"))
MAX_SIDE = 16000
MAX_PICTURES = 60
MIN_WIDTH = 200
RGBA = tuple[int, int, int, int]


@dataclass(frozen=True)
class CollageStyle:
    layout: str = "grid"
    columns: int = 0  # 0: about a square arrangement
    gap: float = 0.015
    background: RGBA = (255, 255, 255, 255)
    fill: bool = True

    def __post_init__(self) -> None:
        if self.layout not in dict(LAYOUTS):
            raise ValueError(f"unknown collage layout {self.layout!r}")

    @property
    def transparent(self) -> bool:
        return self.background[3] < 255


@dataclass(frozen=True)
class Cell:
    x: float
    y: float
    w: float
    h: float

    def pixels(self) -> tuple[int, int, int, int]:
        """The cell as a whole-pixel box (neighbors share their rounded edges)."""
        return round(self.x), round(self.y), round(self.x + self.w), round(self.y + self.h)


def auto_columns(count: int) -> int:
    return max(1, math.ceil(math.sqrt(count)))


def columns_for(style: CollageStyle, count: int) -> int:
    return min(count, style.columns) if style.columns > 0 else auto_columns(count)


def _rows(count: int, per_row: int) -> list[range]:
    return [range(i, min(i + per_row, count)) for i in range(0, count, per_row)]


def layout(
    aspects: Sequence[float], style: CollageStyle, width: float
) -> tuple[tuple[float, float], list[Cell]]:
    """The canvas size and one cell per picture (``aspects`` are width / height)."""
    count = len(aspects)
    if count == 0:
        return (width, width), []
    gap = style.gap * width
    if style.layout == "column":
        w = width - 2 * gap
        cells, y = [], gap
        for a in aspects:
            h = w / a
            cells.append(Cell(gap, y, w, h))
            y += h + gap
        return (width, y), cells
    per_row = count if style.layout == "row" else columns_for(style, count)
    if style.layout == "grid":
        return _grid(aspects, per_row, gap, width)
    return _mosaic(aspects, per_row, gap, width)


def _grid(aspects, per_row, gap, width):
    cell_aspect = statistics.median(aspects)
    w = (width - gap * (per_row + 1)) / per_row
    h = w / cell_aspect
    cells, y = [], gap
    for row in _rows(len(aspects), per_row):
        x = gap + (per_row - len(row)) * (w + gap) / 2
        for _i in row:
            cells.append(Cell(x, y, w, h))
            x += w + gap
        y += h + gap
    return (width, y), cells


def _mosaic(aspects, per_row, gap, width):
    rows = _rows(len(aspects), per_row)
    heights = []
    for row in rows:
        free = width - gap * (len(row) + 1)
        heights.append(free / sum(aspects[i] for i in row))
    if len(rows) > 1 and len(rows[-1]) < per_row:
        # A short last row would be huge at full width: give it the others' height.
        heights[-1] = min(heights[-1], statistics.mean(heights[:-1]))
    cells, y = [], gap
    for row, h in zip(rows, heights, strict=True):
        used = sum(aspects[i] * h for i in row) + gap * (len(row) - 1)
        x = (width - used) / 2
        for i in row:
            w = aspects[i] * h
            cells.append(Cell(x, y, w, h))
            x += w + gap
        y += h + gap
    return (width, y), cells


def fitted_width(sizes: Sequence[tuple[int, int]], style: CollageStyle, width: int) -> int:
    """``width``, made smaller when the canvas would get taller than ``MAX_SIDE``."""
    aspects = [w / h for w, h in sizes]
    (_w, height), _cells = layout(aspects, style, width)
    if height > MAX_SIDE:
        width = int(width * MAX_SIDE / height)
    return max(MIN_WIDTH, min(MAX_SIDE, width))


def default_width(sizes: Sequence[tuple[int, int]], style: CollageStyle) -> int:
    """A width at which the pictures are about their own size: none is blown up much."""
    if not sizes:
        return 2000
    per_row = (
        len(sizes)
        if style.layout == "row"
        else 1
        if style.layout == "column"
        else columns_for(style, len(sizes))
    )
    typical = statistics.median(w for w, _h in sizes)
    width = int(min(MAX_SIDE, typical * per_row) // 10 * 10)
    return fitted_width(sizes, style, max(MIN_WIDTH, width))


def _placed(picture: Image.Image, box: tuple[int, int], fill: bool) -> Image.Image:
    if fill:
        return ImageOps.fit(picture, box, Image.Resampling.LANCZOS)
    return ImageOps.contain(picture, box, Image.Resampling.LANCZOS)


def render(pictures: Sequence[Image.Image], style: CollageStyle, width: int) -> Image.Image:
    """The collage of ``pictures`` (RGB or RGBA) at ``width`` pixels."""
    aspects = [p.width / p.height for p in pictures]
    (w, h), cells = layout(aspects, style, width)
    canvas = Image.new("RGBA", (round(w), round(h)), style.background)
    for picture, cell in zip(pictures, cells, strict=True):
        x0, y0, x1, y1 = cell.pixels()
        box = (max(1, x1 - x0), max(1, y1 - y0))
        placed = _placed(picture, box, style.fill).convert("RGBA")
        dx = x0 + (box[0] - placed.width) // 2
        dy = y0 + (box[1] - placed.height) // 2
        canvas.alpha_composite(placed, (dx, dy))
    if canvas.getchannel("A").getextrema() == (255, 255):
        return canvas.convert("RGB")
    return canvas


def output_format(source_formats: Sequence[str], style: CollageStyle) -> str:
    """JPG when every picture is a JPG and nothing is transparent; PNG otherwise."""
    if not style.transparent and all(f == "jpg" for f in source_formats):
        return "jpg"
    return "png"


def save_collage(
    ctx: JobContext,
    sources: Sequence[tuple[Path, str]],
    style: CollageStyle,
    width: int,
    out: Path,
) -> None:
    pictures = []
    for n, (path, format_id) in enumerate(sources):
        ctx.check()
        ctx.progress(n / (len(sources) + 1), f"Reading {path.name}")
        with imageedit.opened(path, format_id, ctx) as im:
            im.seek(0)
            picture, icc = imageedit.working(ImageOps.exif_transpose(im))
        if icc:  # pictures with different color profiles meet on one sRGB canvas
            picture = imaging._to_srgb(picture, icc, picture.mode)
        pictures.append(picture)
    ctx.progress(len(sources) / (len(sources) + 1), "Placing pictures")
    width = fitted_width([p.size for p in pictures], style, width)
    result = render(pictures, style, width)
    ctx.check()
    target_id = output_format([f for _p, f in sources], style)
    target = imaging.TARGETS[target_id]
    prepared = imaging.prepare(result, target)
    imaging._save(prepared.image, out, target_id, **target.options)
