"""Every image route on the real generated fixtures (scripts/make_fixtures.py).

Each route must produce a file that is detected as the target format, decodes, keeps the
size, and handles transparency as documented in ``quince.backends.imaging``.
"""

import shutil
from pathlib import Path

import pytest
from PIL import Image

from quince.backends import imaging
from quince.core import filetype, formats
from quince.core.deps import Availability
from quince.core.jobs import JobQueue, JobState
from quince.core.registry import discover
from quince.core.selection import Selection
from tests.unit.test_jobs import wait_finished

pytestmark = pytest.mark.integration

GENERATED = Path(__file__).resolve().parent.parent / "fixtures" / "generated"
REGISTRY = discover()
ROUTES = [r for r in REGISTRY.routes if r.backend == "imaging"]
AVAILABLE = Availability.probe()
SIZE = (64, 48)  # every raster fixture
WITH_ALPHA = {"png", "webp", "heic", "tiff", "avif", "gif", "svg"}  # fixtures with a clear corner


def convert(paths, target):
    op = REGISTRY.get(f"convert.{formats.get(target).category.value}.{target}")
    queue = JobQueue(workers=1)
    try:
        return wait_finished(queue, queue.submit(op.run, Selection.from_paths(paths), title="t"))
    finally:
        queue.shutdown()


def fixture_copy(fmt: str, tmp_path: Path) -> Path:
    src = GENERATED / f"sample.{formats.get(fmt).extension}"
    if not src.exists():
        pytest.skip(f"{src.name} missing; run scripts/make_fixtures.py")
    dst = tmp_path / src.name
    shutil.copyfile(src, dst)
    return dst


@pytest.mark.parametrize("route", ROUTES, ids=[f"{r.source}-{r.target}" for r in ROUTES])
def test_route(route, tmp_path):
    if missing := AVAILABLE.missing(route.all_requires):
        pytest.skip(f"needs {', '.join(missing)}")
    src = fixture_copy(route.source, tmp_path)
    snap = convert([src], route.target)
    assert snap.state is JobState.SUCCEEDED, snap.error
    (out,) = snap.outputs
    assert out == tmp_path / f"sample.{formats.get(route.target).extension}"
    assert filetype.detect(out).id == route.target
    assert src.read_bytes() == (GENERATED / src.name).read_bytes()  # original untouched
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".quince-tmp-")]

    if route.target == "pdf":
        import pymupdf

        with pymupdf.open(out) as doc:
            assert doc.page_count == 1
            rect = doc[0].rect
            assert rect.width / rect.height == pytest.approx(SIZE[0] / SIZE[1], rel=0.02)
            if route.source != "svg":
                assert doc[0].get_images(), "the page holds the image"
        return

    imaging._register_heif()
    with Image.open(out) as im:
        im.load()
        if route.source != "svg":
            assert im.size == SIZE
        corner = im.convert("RGBA").getpixel((0, 0))
    if route.source in WITH_ALPHA:
        if imaging.TARGETS[route.target].alpha:
            assert corner[3] < 40, "transparency is kept"
            assert not snap.notes
        else:
            assert corner[:3] >= (235, 235, 235), "transparency becomes white"
            assert "Transparent areas were filled with white" in snap.notes


def test_several_files_each_get_their_own_output(tmp_path):
    files = [fixture_copy(f, tmp_path) for f in ("png", "webp", "bmp")]
    snap = convert(files, "jpg")
    assert snap.state is JobState.SUCCEEDED
    assert sorted(p.name for p in snap.outputs) == [
        "sample (1).jpg",
        "sample (2).jpg",
        "sample.jpg",
    ]
    assert all(filetype.detect(p).id == "jpg" for p in snap.outputs)


def test_animated_gif(tmp_path):
    src = GENERATED / "sample-animated.gif"
    if not src.exists():
        pytest.skip("run scripts/make_fixtures.py")
    shutil.copyfile(src, tmp_path / src.name)
    gif = tmp_path / src.name
    snap = convert([gif], "webp")
    with Image.open(snap.outputs[0]) as im:
        assert im.n_frames == 3  # animation kept
    assert not snap.notes
    snap = convert([gif], "png")
    with Image.open(snap.outputs[0]) as im:
        assert getattr(im, "n_frames", 1) == 1
    assert snap.notes == ("Only the first frame was kept",)


SYSTEM_PYTHON = "/usr/bin/python3"
PIXBUF_CHECK = Path(__file__).resolve().parent / "pixbuf_check.py"


def _pixbuf(paths):
    import os
    import subprocess

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run(
        [SYSTEM_PYTHON, PIXBUF_CHECK, *map(str, paths)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0 or not proc.stdout.startswith("FORMATS"):
        pytest.skip(f"gdk-pixbuf is not usable from {SYSTEM_PYTHON}: {proc.stderr.strip()[-200:]}")
    lines = proc.stdout.splitlines()
    return set(lines[0].split()[1:]), lines[1:]


def test_outputs_open_in_the_system_image_library(tmp_path):
    """Eye of GNOME decodes with gdk-pixbuf: every output its loaders know must load there."""
    outputs = []
    for target in imaging.TARGETS:
        source = "jpg" if target == "png" else "png"
        if AVAILABLE.missing(REGISTRY.routes.find(source, target).all_requires):
            continue
        folder = tmp_path / target
        folder.mkdir()
        snap = convert([fixture_copy(source, folder)], target)
        outputs.append((target, snap.outputs[0]))
    known, lines = _pixbuf([p for _t, p in outputs])
    results = dict(zip([str(p) for _t, p in outputs], lines, strict=True))
    checked = []
    for target, path in outputs:
        if formats.get(target).mime_types[0] not in known:
            continue  # no loader installed (e.g. webp-pixbuf-loader); see docs/DEPENDENCIES.md
        assert results[str(path)] == f"OK {path} 64x48", results[str(path)]
        checked.append(target)
    assert {"jpg", "png", "tiff", "bmp", "gif"} <= set(checked)
