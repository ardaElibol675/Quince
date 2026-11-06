"""Wheel geometry: N equal slices in a single ring (pure math, no Qt).

Angles are in degrees, measured clockwise from 12 o'clock; slice 0 is centered at the top.
Coordinates are relative to the wheel center, with y pointing down (screen coordinates).

The ring keeps a fixed thickness. When N grows, the ring grows too, so every slice keeps at
least ``MIN_SLICE_ARC`` pixels of arc at its label radius (CLAUDE.md Hard Rules 1 and 2).

A layout is in wheel units; ``scale`` (the wheel size setting) maps them to screen pixels.
The painter scales its drawing, ``window_size`` and ``hit_test`` work in screen pixels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

BASE_INNER_RADIUS = 64.0  # the center disc, which shows the highlighted item's name
RING_WIDTH = 88.0
MIN_SLICE_ARC = 76.0
MAX_LABEL_WIDTH = 112.0
MAX_ICON_SIZE = 32
MIN_ICON_SIZE = 20
# Room around the ring for the shadow and the open animation.
WINDOW_MARGIN = 28
# Pointer positions this far outside the ring still count as on a slice.
HIT_SLOP = 6.0


@dataclass(frozen=True)
class Slice:
    index: int
    start: float  # degrees
    span: float  # degrees
    label_x: float  # label anchor (center of icon + text), relative to the wheel center
    label_y: float
    label_width: float

    @property
    def center(self) -> float:
        return (self.start + self.span / 2) % 360

    @property
    def end(self) -> float:
        return self.start + self.span


@dataclass(frozen=True)
class WheelLayout:
    count: int
    inner_radius: float
    outer_radius: float
    icon_size: int
    font_px: int
    slices: tuple[Slice, ...]
    scale: float = 1.0

    @property
    def label_radius(self) -> float:
        return (self.inner_radius + self.outer_radius) / 2

    @property
    def window_size(self) -> int:
        """Side of the square window that holds the wheel, in screen pixels."""
        return math.ceil(2 * (self.outer_radius + WINDOW_MARGIN) * self.scale)

    @property
    def screen_radius(self) -> float:
        """The outer radius in screen pixels."""
        return self.outer_radius * self.scale

    def slice_arc(self) -> float:
        """Arc length of one slice at the label radius, in pixels."""
        if self.count == 0:
            return 0.0
        return 2 * math.pi * self.label_radius / self.count


def point_on_circle(radius: float, degrees: float) -> tuple[float, float]:
    rad = math.radians(degrees)
    return radius * math.sin(rad), -radius * math.cos(rad)


def angle_of(x: float, y: float) -> float:
    """Clockwise angle from 12 o'clock of the point (x, y), in [0, 360)."""
    return math.degrees(math.atan2(x, -y)) % 360


def layout(count: int, scale: float = 1.0) -> WheelLayout:
    """The layout for ``count`` slices (0 gives an empty ring), drawn ``scale`` times larger."""
    if count < 0:
        raise ValueError("count must be >= 0")
    if scale <= 0:
        raise ValueError("scale must be > 0")
    needed_label_radius = count * MIN_SLICE_ARC / (2 * math.pi)
    inner = max(BASE_INNER_RADIUS, needed_label_radius - RING_WIDTH / 2)
    outer = inner + RING_WIDTH
    label_radius = (inner + outer) / 2

    if count <= 1:
        label_width = MAX_LABEL_WIDTH
    else:
        span = math.radians(360 / count)
        chord = 2 * label_radius * math.sin(min(span, math.pi) / 2)
        label_width = min(MAX_LABEL_WIDTH, chord * 0.9)
    icon_size = int(max(MIN_ICON_SIZE, min(MAX_ICON_SIZE, label_width * 0.42)))
    font_px = 13 if count <= 8 else 12 if count <= 14 else 11

    slices = []
    span_deg = 360 / count if count else 0.0
    for i in range(count):
        center = i * span_deg
        x, y = point_on_circle(label_radius, center)
        slices.append(Slice(i, (center - span_deg / 2) % 360, span_deg, x, y, label_width))
    return WheelLayout(count, inner, outer, icon_size, font_px, tuple(slices), scale)


def region(wheel: WheelLayout, x: float, y: float) -> str:
    """``"center"``, ``"ring"`` or ``"outside"`` for a point relative to the center (in
    screen pixels)."""
    r = math.hypot(x, y) / wheel.scale
    if r < wheel.inner_radius:
        return "center"
    if r <= wheel.outer_radius + HIT_SLOP and wheel.count:
        return "ring"
    return "outside"


def hit_test(wheel: WheelLayout, x: float, y: float) -> int | None:
    """Index of the slice under (x, y), or None for the center and empty space."""
    if region(wheel, x, y) != "ring":
        return None
    span = 360 / wheel.count
    return int(((angle_of(x, y) + span / 2) % 360) // span) % wheel.count


def clamp_center(
    cx: float, cy: float, radius: float, screen: tuple[float, float, float, float]
) -> tuple[float, float]:
    """Move a wheel center so a circle of ``radius`` stays inside ``screen`` (x, y, w, h).

    If the screen is smaller than the wheel, the wheel is centered on the screen.
    """
    sx, sy, sw, sh = screen

    def clamp(value: float, low: float, size: float) -> float:
        if size < 2 * radius:
            return low + size / 2
        return min(max(value, low + radius), low + size - radius)

    return clamp(cx, sx, sw), clamp(cy, sy, sh)
