import pytest
from PIL import Image
from PySide6.QtCore import QPointF

from quince.backends import painting
from quince.backends.painting import Shape


@pytest.fixture(autouse=True)
def _app(qapp):
    return qapp


def test_qimage_round_trip():
    im = Image.new("RGBA", (7, 3), (10, 20, 30, 40))
    im.putpixel((6, 2), (1, 2, 3, 4))
    assert painting.from_qimage(painting.to_qimage(im)).tobytes() == im.tobytes()


def test_annotate_paints_every_kind():
    im = Image.new("RGB", (300, 200), (255, 255, 255))
    shapes = (
        Shape("pen", ((10, 10), (60, 12), (90, 40)), (255, 0, 0), 6),
        Shape("highlight", ((10, 180), (200, 180)), (255, 255, 0), 6),
        Shape("line", ((100, 10), (200, 10)), (0, 0, 0), 4),
        Shape("arrow", ((20, 100), (150, 100)), (0, 0, 255), 6),
        Shape("rect", ((200, 50), (280, 120)), (0, 128, 0), 4),
        Shape("ellipse", ((200, 130), (280, 170)), (0, 128, 0), 4),
        Shape("text", ((30, 120),), (255, 0, 0), 30, "Hi"),
    )
    out = painting.annotate(im, shapes)
    assert out.mode == "RGB" and out.size == im.size
    assert out.getpixel((150, 10))[:3] == (0, 0, 0)  # the line
    assert out.getpixel((148, 100))[2] > 200  # the arrow head
    assert out.getpixel((200, 85))[1] > 100  # the box's left edge
    assert out.getpixel((100, 180))[2] < 200  # the highlighter tints yellow (40 %)
    assert out.getpixel((100, 180))[0] > 200
    assert painting.annotate(im, ()) is im


def test_rgba_stays_rgba():
    im = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    out = painting.annotate(im, (Shape("line", ((0, 25), (50, 25)), (255, 0, 0), 6),))
    assert out.mode == "RGBA"
    assert out.getpixel((25, 25))[3] == 255
    assert out.getpixel((25, 2))[3] == 0


def test_hit_area_and_bounds():
    line = Shape("line", ((0, 0), (100, 0)), (0, 0, 0), 4)
    assert painting.hit_area(line, 4).contains(QPointF(50, 3))
    assert not painting.hit_area(line, 4).contains(QPointF(50, 20))
    text = Shape("text", ((10, 10),), (0, 0, 0), 20, "Hello")
    rect = painting.text_rect(text)
    assert rect.left() == 10 and rect.width() > 20 and rect.height() >= 20
    assert painting.hit_area(text, 2).contains(QPointF(15, 20))
    assert painting.bounds(line).contains(QPointF(50, 1.5))
    assert Shape("text", ((0, 0),), (0, 0, 0), 10, "  ").is_empty


def test_snapping_and_widths():
    assert painting.snap_angle((0, 0), (10, 1)) == pytest.approx((10.05, 0), abs=0.01)
    x, y = painting.snap_angle((0, 0), (10, 9))
    assert x == pytest.approx(y)
    assert painting.snap_square((0, 0), (10, -4)) == (10, -10)
    assert painting.default_width((4000, 3000), 0.006) == 18
    assert painting.default_width((100, 100), 0.006) == 2  # never thinner than 2 px
    with pytest.raises(ValueError):
        Shape("star", ((0, 0),), (0, 0, 0), 1)


def test_rgb_previews_have_no_alpha_channel():
    rgb = painting.to_qimage(Image.new("RGB", (5, 3), (1, 2, 3)))
    assert not rgb.hasAlphaChannel()
    assert painting.from_qimage(rgb).convert("RGB").getpixel((4, 2)) == (1, 2, 3)
    assert painting.to_qimage(Image.new("LA", (2, 2))).hasAlphaChannel()
