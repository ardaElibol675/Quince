import pytest
from PIL import Image

from quince.backends import collage
from quince.backends.collage import CollageStyle


def test_grid_cells_equal_and_last_row_centered():
    style = CollageStyle(layout="grid", columns=2, gap=0.0)
    (w, h), cells = collage.layout([1.0, 1.0, 1.0], style, 1000)
    assert (w, h) == (1000, 1000)
    assert [(c.x, c.y, c.w, c.h) for c in cells[:2]] == [(0, 0, 500, 500), (500, 0, 500, 500)]
    assert cells[2].x == pytest.approx(250)  # alone in its row, centered
    assert cells[2].y == pytest.approx(500)


def test_grid_gap_and_median_cell_shape():
    style = CollageStyle(layout="grid", columns=3, gap=0.01)
    (_w, h), cells = collage.layout([1.5, 1.5, 0.5], style, 1000)
    gap = 10
    cell_w = (1000 - 4 * gap) / 3
    assert cells[0].w == pytest.approx(cell_w)
    assert cells[0].h == pytest.approx(cell_w / 1.5)
    assert h == pytest.approx(cell_w / 1.5 + 2 * gap)


def test_mosaic_rows_span_the_width():
    style = CollageStyle(layout="mosaic", columns=2, gap=0.0)
    (_w, _h), cells = collage.layout([2.0, 1.0, 1.0, 1.0, 1.0], style, 900)
    first = cells[:2]
    assert first[0].h == first[1].h
    assert first[0].w + first[1].w == pytest.approx(900)
    assert first[0].w / first[0].h == pytest.approx(2.0)  # shapes are kept
    # The single picture of the last row is not blown up to the full width.
    last = cells[4]
    assert last.h == pytest.approx((cells[0].h + cells[2].h) / 2)
    assert last.x == pytest.approx((900 - last.w) / 2)


def test_row_and_column():
    (w, h), cells = collage.layout([2.0, 1.0], CollageStyle(layout="row", gap=0.0), 900)
    assert w == 900 and h == pytest.approx(300) and cells[1].x == pytest.approx(600)
    (_w, h), cells = collage.layout([2.0, 1.0], CollageStyle(layout="column", gap=0.0), 600)
    assert h == pytest.approx(300 + 600) and cells[1].y == pytest.approx(300)


def test_auto_columns_and_widths():
    assert [collage.auto_columns(n) for n in (1, 2, 4, 5, 9, 10)] == [1, 2, 2, 3, 3, 4]
    sizes = [(1000, 1000)] * 4
    assert collage.default_width(sizes, CollageStyle(layout="grid")) == 2000
    assert collage.default_width(sizes, CollageStyle(layout="row")) == 4000
    tall = [(500, 20000)] * 2
    assert collage.fitted_width(tall, CollageStyle(layout="column"), 4000) <= 800


def test_render_and_output_format():
    pictures = [Image.new("RGB", (400, 300), "red"), Image.new("RGB", (300, 400), "blue")]
    style = CollageStyle(layout="row", gap=0.02, background=(0, 255, 0, 255))
    out = collage.render(pictures, style, 1000)
    assert out.mode == "RGB" and out.width == 1000
    assert out.getpixel((2, 2)) == (0, 255, 0)  # the gap shows the background
    assert out.getpixel((100, out.height // 2)) == (255, 0, 0)
    clear = collage.render(pictures, CollageStyle(background=(0, 0, 0, 0)), 500)
    assert clear.mode == "RGBA"
    assert collage.output_format(["jpg", "jpg"], CollageStyle()) == "jpg"
    assert collage.output_format(["jpg", "png"], CollageStyle()) == "png"
    assert collage.output_format(["jpg"], CollageStyle(background=(0, 0, 0, 0))) == "png"


def test_unknown_layout_is_refused():
    with pytest.raises(ValueError):
        CollageStyle(layout="spiral")
