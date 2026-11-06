"""The Image Editor's pipeline: one fixed order, boxes and shapes kept on the same content."""

from __future__ import annotations

import pytest
from PIL import Image

from quince.backends import imagepipeline as ip
from quince.backends.imageedit import Adjust, FrameStyle, Redaction
from quince.backends.imagepipeline import Geometry, ImageEdit
from quince.backends.painting import Shape
from tests.unit.test_imageedit import quadrants

RED, GREEN, BLUE, WHITE = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)


@pytest.mark.parametrize("rotate", range(4))
@pytest.mark.parametrize("flip_h", [False, True])
@pytest.mark.parametrize("flip_v", [False, True])
def test_geometry_round_trips(rotate, flip_h, flip_v):
    geometry = Geometry((400, 300), (50, 20, 350, 220), rotate, flip_h, flip_v)
    for point in ((50, 20), (123.5, 77.25), (350, 220)):
        back = geometry.inverse(geometry.forward(point))
        assert back == pytest.approx(point)
    box = geometry.forward_box((60, 30, 100, 90))
    assert geometry.inverse_box(box) == pytest.approx((60, 30, 100, 90))
    w, h = geometry.out_size
    assert (w, h) == ((200, 300) if rotate % 2 else (300, 200))


def test_geometry_matches_the_pixels():
    """A point maps where Pillow moves the pixel under it."""
    im = quadrants((40, 20))
    for rotate in range(4):
        for flip_h in (False, True):
            edit = ImageEdit(crop=(0, 0, 40, 20), adjust=Adjust(rotate=rotate, flip_h=flip_h))
            out = ip.render(im, edit, im.size)
            x, y = edit.geometry(im.size).forward((5.5, 5.5))  # inside the red quarter
            assert out.getpixel((int(x), int(y)))[:3] == RED


def test_order_crop_turn_colors_redact_annotate_resize_frame():
    im = quadrants((40, 20))
    edit = ImageEdit(
        crop=(20.0, 0.0, 40.0, 20.0),  # the green and white half
        adjust=Adjust(rotate=1, size=(40, 40)),
        redactions=(Redaction((20, 0, 30, 10), "solid"),),  # the top-left of green, original px
        frame=FrameStyle(padding=0.1, background=(0, 0, 0, 255)),
    )
    out = ip.render(im, edit, im.size)
    assert out.size == (48, 48)  # 40 + 2 * 4
    assert out.getpixel((1, 1))[:3] == (0, 0, 0)  # frame
    # turned clockwise: green (top) is now on the right, white on the left
    assert out.getpixel((10, 24))[:3] == WHITE
    assert out.getpixel((36, 36))[:3] == GREEN
    # the black box stayed on green's top-left corner, now at the top right
    assert out.getpixel((40, 8))[:3] == (0, 0, 0)
    assert ip.framed_size(edit, im.size) == (48, 48)


def test_areas_outside_the_crop_are_left_out():
    edit = ImageEdit(crop=(0, 0, 20, 20), redactions=(Redaction((25, 0, 40, 10)),))
    assert ip.redactions_in(edit, edit.geometry((40, 20))) == []


def test_preview_scale_matches_the_full_picture(qapp):
    big = quadrants((400, 200))
    small = big.resize((100, 50))
    edit = ImageEdit(
        crop=(0.0, 0.0, 200.0, 200.0),
        redactions=(Redaction((10, 10, 90, 90), "solid"),),
        shapes=(Shape("rect", ((100, 100), (180, 180)), (0, 255, 0), 8),),
    )
    full = ip.render(big, edit, big.size)
    preview = ip.render(small, edit, big.size)
    assert preview.size == (50, 50) and full.size == (200, 200)
    for fx, fy in ((50, 50), (140, 100), (195, 195)):
        assert preview.getpixel((fx // 4, fy // 4))[:3] == full.getpixel((fx, fy))[:3]


def test_text_stays_upright_and_on_its_place(qapp):
    text = Shape("text", ((10.0, 10.0),), (0, 0, 0), 12.0, "Hi")
    geometry = Geometry((100, 50), None, 1)
    turned = geometry.forward_shape(text)
    assert turned.kind == "text" and turned.text == "Hi"
    assert geometry.inverse_shape(turned).points[0] == pytest.approx((10.0, 10.0))


def test_rescaled_size_follows_a_new_crop():
    assert ip.rescaled_size((50, 25), (100, 50), (60, 50)) == (30, 25)
    assert ip.rescaled_size(None, (100, 50), (60, 50)) is None


def test_transparency_and_describe():
    assert not ip.has_transparency(Image.new("RGBA", (2, 2), (0, 0, 0, 255)))
    assert ip.has_transparency(Image.new("RGBA", (2, 2), (0, 0, 0, 0)))
    assert ip.describe(ImageEdit()) == []
    assert ip.describe(ImageEdit(crop=(0, 0, 1, 1), frame=FrameStyle())) == ["cropped", "framed"]
    assert ImageEdit().is_empty
