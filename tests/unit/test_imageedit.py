from pathlib import Path

import pytest
from PIL import Image, ImageSequence

from quince.backends import imageedit
from quince.backends.imageedit import Adjust, FrameStyle, Redaction
from quince.core.jobs import JobQueue, JobState
from quince.core.selection import Selection
from tests.unit.test_jobs import wait_finished

GPS = 0x8825
MAKE = 0x010F


def quadrants(size=(40, 20)) -> Image.Image:
    """Red top-left, green top-right, blue bottom-left, white bottom-right."""
    w, h = size
    im = Image.new("RGB", size)
    for box, color in (
        ((0, 0, w // 2, h // 2), (255, 0, 0)),
        ((w // 2, 0, w, h // 2), (0, 255, 0)),
        ((0, h // 2, w // 2, h), (0, 0, 255)),
        ((w // 2, h // 2, w, h), (255, 255, 255)),
    ):
        im.paste(color, box)
    return im


def test_output_format():
    assert imageedit.output_format("jpg") == "jpg"
    assert imageedit.output_format("svg") == "png"
    assert imageedit.output_format("jpg", alpha=True) == "png"
    assert imageedit.output_format("webp", alpha=True) == "webp"


def test_pixel_box_rounds_and_stays_inside():
    assert imageedit.pixel_box((-5, 2.4, 10.6, 999), (40, 20)) == (0, 2, 11, 20)
    assert imageedit.pixel_box((39.9, 0, 39.9, 0), (40, 20)) == (39, 0, 40, 1)  # at least 1 px
    assert imageedit.crop(quadrants(), (20, 0, 40, 10)).getpixel((0, 0)) == (0, 255, 0)


def test_rotation_and_mirrors():
    im = quadrants()
    turned = imageedit.adjust(im, Adjust(rotate=1))
    assert turned.size == (20, 40)
    assert turned.getpixel((19, 0)) == (255, 0, 0)  # top-left went to the top-right
    mirrored = imageedit.adjust(im, Adjust(flip_h=True))
    assert mirrored.getpixel((0, 0)) == (0, 255, 0)
    flipped = imageedit.adjust(im, Adjust(flip_v=True))
    assert flipped.getpixel((0, 0)) == (0, 0, 255)


@pytest.mark.parametrize("start", [Adjust(), Adjust(flip_h=True), Adjust(rotate=1, flip_v=True)])
@pytest.mark.parametrize("turns", [1, -1, 2])
def test_rotated_turns_the_current_result(start, turns):
    """Turning the state must look like turning what is on screen."""
    im = quadrants()
    on_screen = imageedit.adjust(im, start)
    expected = imageedit.adjust(on_screen, Adjust(rotate=turns % 4))
    assert imageedit.adjust(im, start.rotated(turns)).tobytes() == expected.tobytes()


def test_rotated_swaps_a_new_size():
    assert Adjust(size=(100, 50)).rotated(1).size == (50, 100)
    assert Adjust(size=(100, 50)).rotated(2).size == (100, 50)


def test_resize_and_colors_keep_transparency():
    im = Image.new("RGBA", (40, 20), (100, 100, 100, 128))
    out = imageedit.adjust(im, Adjust(size=(20, 10), brightness=50, saturation=-100))
    assert out.size == (20, 10)
    r, g, b, a = out.getpixel((5, 5))
    assert a == 128  # the transparency is untouched
    assert r == g == b and abs(r - 150) <= 3  # resizing rounds through premultiplied alpha
    dark = imageedit.adjust(quadrants(), Adjust(brightness=-100))
    assert dark.getextrema() == ((0, 0), (0, 0), (0, 0))
    assert imageedit.adjust(im, Adjust(size=(20, 10)), resize=False).size == (40, 20)


def test_frame_padding_background_corners():
    im = Image.new("RGB", (100, 50), (200, 0, 0))
    out = imageedit.frame(im, FrameStyle(padding=0.2, background=(0, 0, 255, 255)))
    assert out.mode == "RGB" and out.size == (120, 70)
    assert out.getpixel((2, 2)) == (0, 0, 255)
    assert out.getpixel((60, 35)) == (200, 0, 0)
    rounded = imageedit.frame(im, FrameStyle(padding=0.0, radius=0.4, background=(0, 0, 0, 0)))
    assert rounded.mode == "RGBA"
    assert rounded.getpixel((0, 0))[3] == 0  # the corner is cut off
    assert rounded.getpixel((50, 25))[3] == 255


def test_frame_shadow_darkens_below_the_picture():
    im = Image.new("RGB", (100, 100), (255, 255, 255))
    style = FrameStyle(padding=0.2, background=(255, 255, 255, 255), shadow=1.0)
    out = imageedit.frame(im, style)
    below = out.getpixel((70, 123))
    above = out.getpixel((70, 16))
    assert below[0] < above[0] <= 255


def test_rounded_mask_is_smooth():
    mask = imageedit.rounded_mask((80, 80), 20)
    values = {mask.getpixel((x, 5)) for x in range(0, 20)}
    assert 0 in values and 255 in values
    assert any(0 < v < 255 for v in values)  # antialiased edge


def test_redact_styles_change_only_their_area():
    im = quadrants((80, 40))
    solid = imageedit.redact(im, [Redaction((0, 0, 40, 20), "solid")])
    assert solid.getpixel((10, 10)) == (0, 0, 0)
    assert solid.getpixel((60, 10)) == (0, 255, 0)
    # Blur and pixelate only use the pixels inside the area: a one-color area stays one color.
    for style in ("blur", "pixelate"):
        out = imageedit.redact(im, [Redaction((0, 0, 40, 20), style)])
        assert out.getpixel((10, 10)) == (255, 0, 0)
        assert out.getpixel((60, 10)) == (0, 255, 0)
    mixed = imageedit.redact(im, [Redaction((20, 0, 60, 20), "pixelate")])
    assert len(mixed.crop((20, 0, 60, 20)).getcolors(1000)) <= 16  # big blocks
    with pytest.raises(ValueError):
        Redaction((0, 0, 1, 1), "swirl")


def test_load_gives_an_upright_preview(tmp_path):
    src = tmp_path / "big.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # shown turned clockwise
    Image.new("RGB", (3000, 1000), (10, 20, 30)).save(src, exif=exif.tobytes())
    loaded = imageedit.load(src, "jpg", max_side=600)
    assert loaded.size == (1000, 3000)
    assert loaded.preview.size == (200, 600)
    assert loaded.scale == pytest.approx(0.2)
    assert not loaded.animated


# --- saving through the job queue -----------------------------------------------------------


def _save(src: Path, fmt: str, transform, **kwargs):
    queue = JobQueue(workers=1)

    def run(ctx):
        target = kwargs.get("target_format") or imageedit.output_format(fmt)
        out = ctx.new_output(src, extension=target, suffix="edited")
        imageedit.save_edited(ctx, src, fmt, out, transform, **kwargs)

    try:
        snap = wait_finished(queue, queue.submit(run, Selection.from_paths([src]), title="t"))
    finally:
        queue.shutdown()
    assert snap.state is JobState.SUCCEEDED, snap.error
    return snap


def test_save_keeps_exif_but_not_orientation(tmp_path):
    src = tmp_path / "photo.jpg"
    exif = Image.Exif()
    exif[MAKE] = "Quince Camera"
    exif[0x0112] = 6
    quadrants((40, 20)).save(src, exif=exif.tobytes(), quality=95)
    snap = _save(src, "jpg", lambda im: imageedit.crop(im, (0, 0, 20, 40)))
    (out,) = snap.outputs
    assert out.name == "photo-edited.jpg"
    with Image.open(out) as im:
        assert im.size == (20, 40)  # cropped from the upright picture
        assert im.getexif().get(MAKE) == "Quince Camera"
        assert 0x0112 not in im.getexif()
    assert src.exists()


def test_save_without_exif(tmp_path):
    src = tmp_path / "photo.jpg"
    exif = Image.Exif()
    exif[MAKE] = "Quince Camera"
    quadrants().save(src, exif=exif.tobytes())
    (out,) = _save(src, "jpg", lambda im: im, keep_exif=False).outputs
    with Image.open(out) as im:
        assert MAKE not in im.getexif()


def test_save_animation_edits_every_frame(tmp_path):
    src = tmp_path / "anim.gif"
    frames = [Image.new("RGB", (40, 20), c) for c in ((255, 0, 0), (0, 0, 255), (0, 255, 0))]
    frames[0].save(src, save_all=True, append_images=frames[1:], duration=80, loop=0)
    (out,) = _save(src, "gif", lambda im: imageedit.crop(im, (0, 0, 10, 10))).outputs
    with Image.open(out) as im:
        assert im.n_frames == 3
        assert all(f.size == (10, 10) for f in ImageSequence.Iterator(im))


def test_save_transparent_result_of_a_jpg_as_png(tmp_path):
    src = tmp_path / "photo.jpg"
    quadrants().save(src)
    style = FrameStyle(padding=0.1, background=(0, 0, 0, 0), radius=0.2)
    snap = _save(src, "jpg", lambda im: imageedit.frame(im, style), target_format="png")
    (out,) = snap.outputs
    assert out.suffix == ".png"
    assert imageedit.SAVED_AS_PNG_NOTE in snap.notes
    with Image.open(out) as im:
        assert im.mode == "RGBA"
