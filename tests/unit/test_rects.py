import pytest

from quince.ui.editors import rects

BOUNDS = (0.0, 0.0, 400.0, 300.0)


def approx(box):
    return pytest.approx(box, abs=1e-6)


def test_handle_at_corners_edges_and_nothing():
    box = (100.0, 100.0, 200.0, 180.0)
    assert rects.handle_at(box, (101, 99), 5) == "nw"
    assert rects.handle_at(box, (203, 182), 5) == "se"
    assert rects.handle_at(box, (150, 98), 5) == "n"
    assert rects.handle_at(box, (130, 180), 5) == "s"  # anywhere along an edge
    assert rects.handle_at(box, (198, 140), 5) == "e"
    assert rects.handle_at(box, (150, 140), 5) is None  # inside
    assert rects.handle_at(box, (300, 140), 5) is None  # far away


def test_moved_stops_at_the_edges():
    box = (100.0, 100.0, 200.0, 200.0)
    assert rects.moved(box, 10, -20, BOUNDS) == approx((110, 80, 210, 180))
    assert rects.moved(box, 500, 500, BOUNDS) == approx((300, 200, 400, 300))
    assert rects.moved(box, -500, 0, BOUNDS) == approx((0, 100, 100, 200))


def test_resize_corner_free_and_clamped():
    box = (100.0, 100.0, 200.0, 200.0)
    assert rects.resized(box, "se", (250, 220), BOUNDS) == approx((100, 100, 250, 220))
    assert rects.resized(box, "se", (900, 900), BOUNDS) == approx((100, 100, 400, 300))
    # Dragging past the anchor never flips the box: it stops at the minimum size.
    assert rects.resized(box, "se", (50, 50), BOUNDS, min_size=4) == approx((100, 100, 104, 104))
    assert rects.resized(box, "nw", (90, 80), BOUNDS) == approx((90, 80, 200, 200))


def test_resize_corner_keeps_ratio_inside_bounds():
    box = (100.0, 100.0, 200.0, 200.0)
    # The larger extent wins: 200 wide asks for 200 high, which still fits.
    assert rects.resized(box, "se", (300, 150), BOUNDS, ratio=1.0) == approx((100, 100, 300, 300))
    wide = rects.resized(box, "se", (390, 290), BOUNDS, ratio=16 / 9)
    assert rects.width(wide) / rects.height(wide) == pytest.approx(16 / 9)
    assert wide[2] <= 400 and wide[3] <= 300


def test_resize_edge_with_and_without_ratio():
    box = (100.0, 100.0, 200.0, 200.0)
    assert rects.resized(box, "e", (260, 0), BOUNDS) == approx((100, 100, 260, 200))
    assert rects.resized(box, "n", (0, 40), BOUNDS) == approx((100, 40, 200, 200))
    grown = rects.resized(box, "e", (300, 0), BOUNDS, ratio=1.0)
    assert grown == approx((100, 50, 300, 250))  # the height grows around the middle
    # The ratio cannot be kept past the top / bottom: the width gives way.
    capped = rects.resized(box, "e", (400, 0), BOUNDS, ratio=1.0)
    assert rects.height(capped) <= 300 and rects.width(capped) == pytest.approx(
        rects.height(capped)
    )


def test_from_points_any_direction_and_square():
    assert rects.from_points((50, 60), (10, 20), BOUNDS) == approx((10, 20, 50, 60))
    assert rects.from_points((50, 60), (500, 70), BOUNDS) == approx((50, 60, 400, 70))
    square = rects.from_points((50, 60), (150, 90), BOUNDS, ratio=1.0)
    assert square == approx((50, 60, 150, 160))


def test_fit_ratio_is_centered_and_inside():
    out = rects.fit_ratio((0, 0, 400, 300), 1.0, BOUNDS)
    assert out == approx((50, 0, 350, 300))
    tall = rects.fit_ratio((0, 0, 400, 300), 9 / 16, BOUNDS)
    assert rects.width(tall) / rects.height(tall) == pytest.approx(9 / 16)
    assert rects.height(tall) == pytest.approx(300)
