"""Rectangle geometry for editors that draw, move and resize boxes (Crop, Redact).

Pure functions on ``Box`` tuples ``(x0, y0, x1, y1)`` with x0 <= x1 and y0 <= y1, so they are
unit tested without Qt. ``bounds`` is the box everything must stay inside (the image).
``ratio`` (width / height) keeps the proportions while resizing.
"""

from __future__ import annotations

import math

Box = tuple[float, float, float, float]
Point = tuple[float, float]

# Compass names: the corners and edges a box can be resized by.
HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
CORNERS = frozenset({"nw", "ne", "se", "sw"})


def normalized(box: Box) -> Box:
    x0, y0, x1, y1 = box
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def width(box: Box) -> float:
    return box[2] - box[0]


def height(box: Box) -> float:
    return box[3] - box[1]


def center(box: Box) -> Point:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def contains(box: Box, point: Point) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def handle_points(box: Box) -> dict[str, Point]:
    x0, y0, x1, y1 = box
    cx, cy = center(box)
    return {
        "nw": (x0, y0),
        "n": (cx, y0),
        "ne": (x1, y0),
        "e": (x1, cy),
        "se": (x1, y1),
        "s": (cx, y1),
        "sw": (x0, y1),
        "w": (x0, cy),
    }


def handle_at(box: Box, point: Point, tolerance: float) -> str | None:
    """The handle within ``tolerance`` of ``point`` (corners first), or None.

    Edges count along their whole length, not only at their middle.
    """
    px, py = point
    points = handle_points(box)
    for name in ("nw", "ne", "se", "sw"):
        hx, hy = points[name]
        if abs(px - hx) <= tolerance and abs(py - hy) <= tolerance:
            return name
    x0, y0, x1, y1 = box
    inside_x = x0 - tolerance <= px <= x1 + tolerance
    inside_y = y0 - tolerance <= py <= y1 + tolerance
    if inside_x and abs(py - y0) <= tolerance:
        return "n"
    if inside_x and abs(py - y1) <= tolerance:
        return "s"
    if inside_y and abs(px - x0) <= tolerance:
        return "w"
    if inside_y and abs(px - x1) <= tolerance:
        return "e"
    return None


def clamp_point(point: Point, bounds: Box) -> Point:
    return (
        min(max(point[0], bounds[0]), bounds[2]),
        min(max(point[1], bounds[1]), bounds[3]),
    )


def moved(box: Box, dx: float, dy: float, bounds: Box) -> Box:
    """``box`` moved by (dx, dy), stopped at the edges of ``bounds``."""
    w, h = width(box), height(box)
    x0 = min(max(box[0] + dx, bounds[0]), bounds[2] - w)
    y0 = min(max(box[1] + dy, bounds[1]), bounds[3] - h)
    return x0, y0, x0 + w, y0 + h


def _corner(
    anchor: Point,
    target: Point,
    direction: tuple[int, int],
    bounds: Box,
    ratio: float | None,
    min_size: float,
) -> Box:
    """The box from the fixed corner ``anchor`` towards ``target``, growing in
    ``direction`` (+1 / -1 per axis)."""
    ax, ay = anchor
    dx, dy = direction
    w = max(min_size, (target[0] - ax) * dx)
    h = max(min_size, (target[1] - ay) * dy)
    max_w = (bounds[2] - ax) if dx > 0 else (ax - bounds[0])
    max_h = (bounds[3] - ay) if dy > 0 else (ay - bounds[1])
    if ratio:
        if w / h > ratio:
            h = w / ratio
        else:
            w = h * ratio
        scale = min(1.0, max_w / w, max_h / h)
        w, h = w * scale, h * scale
    else:
        w, h = min(w, max_w), min(h, max_h)
    return normalized((ax, ay, ax + w * dx, ay + h * dy))


def _edge(
    box: Box, handle: str, target: Point, bounds: Box, ratio: float | None, min_size: float
) -> Box:
    x0, y0, x1, y1 = box
    cx, cy = center(box)
    if handle in ("e", "w"):
        anchor = x0 if handle == "e" else x1
        direction = 1 if handle == "e" else -1
        max_w = (bounds[2] - anchor) if direction > 0 else (anchor - bounds[0])
        w = min(max(min_size, (target[0] - anchor) * direction), max_w)
        if not ratio:
            return normalized((anchor, y0, anchor + w * direction, y1))
        h = w / ratio
        max_h = 2 * min(cy - bounds[1], bounds[3] - cy)
        if h > max_h:
            h, w = max_h, max_h * ratio
        return normalized((anchor, cy - h / 2, anchor + w * direction, cy + h / 2))
    anchor = y0 if handle == "s" else y1
    direction = 1 if handle == "s" else -1
    max_h = (bounds[3] - anchor) if direction > 0 else (anchor - bounds[1])
    h = min(max(min_size, (target[1] - anchor) * direction), max_h)
    if not ratio:
        return normalized((x0, anchor, x1, anchor + h * direction))
    w = h * ratio
    max_w = 2 * min(cx - bounds[0], bounds[2] - cx)
    if w > max_w:
        w, h = max_w, max_w / ratio
    return normalized((cx - w / 2, anchor, cx + w / 2, anchor + h * direction))


_OPPOSITE = {"nw": "se", "ne": "sw", "se": "nw", "sw": "ne"}
_DIRECTION = {"nw": (-1, -1), "ne": (1, -1), "se": (1, 1), "sw": (-1, 1)}


def resized(
    box: Box,
    handle: str,
    target: Point,
    bounds: Box,
    *,
    ratio: float | None = None,
    min_size: float = 1.0,
) -> Box:
    """``box`` with ``handle`` dragged to ``target``; the opposite side stays in place.

    The box never flips over and never leaves ``bounds``; with ``ratio`` it keeps that shape
    (an edge handle then grows the other side evenly around the middle).
    """
    if handle in CORNERS:
        anchor = handle_points(box)[_OPPOSITE[handle]]
        return _corner(anchor, target, _DIRECTION[handle], bounds, ratio, min_size)
    if handle in ("n", "e", "s", "w"):
        return _edge(box, handle, target, bounds, ratio, min_size)
    raise ValueError(f"unknown handle {handle!r}")


def from_points(
    anchor: Point,
    target: Point,
    bounds: Box,
    *,
    ratio: float | None = None,
    min_size: float = 0.0,
) -> Box:
    """A new box dragged from ``anchor`` to ``target`` (both clamped to ``bounds``)."""
    anchor = clamp_point(anchor, bounds)
    direction = (1 if target[0] >= anchor[0] else -1, 1 if target[1] >= anchor[1] else -1)
    return _corner(anchor, target, direction, bounds, ratio, min_size)


def fit_ratio(box: Box, ratio: float, bounds: Box) -> Box:
    """The largest box of shape ``ratio`` inside ``box``, centered on it."""
    w, h = width(box), height(box)
    if w <= 0 or h <= 0:
        box, (w, h) = bounds, (width(bounds), height(bounds))
    if w / h > ratio:
        w = h * ratio
    else:
        h = w / ratio
    cx, cy = center(box)
    fitted = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    return moved(fitted, 0, 0, bounds)


def scaled(box: Box, factor: float) -> Box:
    return tuple(v * factor for v in box)  # type: ignore[return-value]


def is_tiny(box: Box, min_size: float) -> bool:
    return width(box) < min_size or height(box) < min_size


def aspect(size: tuple[float, float]) -> float:
    w, h = size
    return w / h if h else 1.0


def same(a: Box, b: Box, tolerance: float = 1e-6) -> bool:
    return all(math.isclose(x, y, abs_tol=tolerance) for x, y in zip(a, b, strict=True))
