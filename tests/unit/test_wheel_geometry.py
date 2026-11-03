import math

import pytest

from quince.ui.wheel import geometry
from quince.ui.wheel.geometry import angle_of, clamp_center, hit_test, layout, point_on_circle

ALL_N = range(1, 21)


def test_angles_clockwise_from_top():
    assert angle_of(0, -10) == pytest.approx(0)
    assert angle_of(10, 0) == pytest.approx(90)
    assert angle_of(0, 10) == pytest.approx(180)
    assert angle_of(-10, 0) == pytest.approx(270)
    x, y = point_on_circle(10, 90)
    assert (x, y) == pytest.approx((10, 0))


@pytest.mark.parametrize("n", ALL_N)
def test_equal_contiguous_slices_cover_the_ring(n):
    wheel = layout(n)
    assert wheel.count == n == len(wheel.slices)
    assert sum(s.span for s in wheel.slices) == pytest.approx(360)
    assert {round(s.span, 9) for s in wheel.slices} == {round(360 / n, 9)}
    for a, b in zip(wheel.slices, wheel.slices[1:], strict=False):
        gap = (b.start - a.end + 180) % 360 - 180  # signed angular difference
        assert gap == pytest.approx(0, abs=1e-9)
    assert wheel.slices[0].center == pytest.approx(0)  # slice 0 at the top
    assert wheel.outer_radius - wheel.inner_radius == pytest.approx(geometry.RING_WIDTH)


@pytest.mark.parametrize("n", ALL_N)
def test_hit_test_finds_each_slice(n):
    wheel = layout(n)
    for s in wheel.slices:
        assert hit_test(wheel, s.label_x, s.label_y) == s.index
        # Just inside both edges of the slice, in the middle of the ring.
        for angle in (s.start + 0.5, s.start + s.span - 0.5):
            x, y = point_on_circle(wheel.label_radius, angle)
            assert hit_test(wheel, x, y) == s.index


@pytest.mark.parametrize("n", ALL_N)
def test_center_and_outside_hit_nothing(n):
    wheel = layout(n)
    assert hit_test(wheel, 0, 0) is None
    assert hit_test(wheel, 0, -(wheel.inner_radius - 1)) is None
    assert hit_test(wheel, 0, -(wheel.outer_radius + geometry.HIT_SLOP + 1)) is None
    assert hit_test(wheel, 0, -(wheel.outer_radius + geometry.HIT_SLOP - 1)) == 0
    assert geometry.region(wheel, 0, 0) == "center"
    assert geometry.region(wheel, 0, -wheel.label_radius) == "ring"
    assert geometry.region(wheel, 1e4, 0) == "outside"


@pytest.mark.parametrize("n", ALL_N)
def test_labels_fit_inside_their_slice(n):
    wheel = layout(n)
    assert wheel.slice_arc() >= geometry.MIN_SLICE_ARC - 1e-9 or n * geometry.MIN_SLICE_ARC < (
        2 * math.pi * wheel.label_radius
    )
    for s in wheel.slices:
        r = math.hypot(s.label_x, s.label_y)
        assert r == pytest.approx(wheel.label_radius)
        assert 0 < s.label_width <= geometry.MAX_LABEL_WIDTH
        if n > 1:
            chord = 2 * r * math.sin(math.radians(min(s.span, 180)) / 2)
            assert s.label_width <= chord
    assert geometry.MIN_ICON_SIZE <= wheel.icon_size <= geometry.MAX_ICON_SIZE
    assert wheel.font_px >= 11


def test_ring_grows_with_n_but_not_for_small_n():
    radii = [layout(n).outer_radius for n in range(0, 21)]
    assert radii == sorted(radii)
    assert layout(1).outer_radius == layout(7).outer_radius  # base size up to 7
    assert layout(20).outer_radius > layout(9).outer_radius
    assert layout(20).window_size > 2 * layout(20).outer_radius


def test_empty_layout():
    wheel = layout(0)
    assert wheel.slices == () and wheel.slice_arc() == 0
    assert hit_test(wheel, 0, -wheel.label_radius) is None
    with pytest.raises(ValueError):
        layout(-1)


def test_clamp_center_keeps_the_wheel_on_screen():
    screen = (0, 0, 1920, 1080)
    assert clamp_center(500, 500, 100, screen) == (500, 500)
    assert clamp_center(10, 10, 100, screen) == (100, 100)
    assert clamp_center(1919, 1079, 100, screen) == (1820, 980)
    # Second monitor to the right, offset vertically.
    assert clamp_center(1925, 5, 150, (1920, -200, 2560, 1440)) == (2070, 5)
    # Screen smaller than the wheel: centered.
    assert clamp_center(0, 0, 300, (0, 0, 400, 400)) == (200, 200)
