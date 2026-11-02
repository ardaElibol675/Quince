import shutil
import tarfile
import zipfile

import pytest
from PIL import Image

pytestmark = pytest.mark.integration


def test_in_process_fixtures(make_fixtures, tmp_path):
    """Fixtures that need only Python packages are always generated correctly."""
    table = make_fixtures.fixtures()
    names = ["sample.png", "sample.jpg", "sample.heic", "sample.avif", "sample.pdf",
             "sample.zip", "sample.tar.gz", "sample.srt", "sample.csv"]  # fmt: skip
    for name in names:
        table[name](tmp_path / name)

    import pillow_heif

    pillow_heif.register_heif_opener()
    for name in ("sample.png", "sample.jpg", "sample.heic", "sample.avif"):
        with Image.open(tmp_path / name) as img:
            assert img.size == (64, 48)
    assert zipfile.ZipFile(tmp_path / "sample.zip").namelist() == list(
        make_fixtures.ARCHIVE_MEMBERS
    )
    with tarfile.open(tmp_path / "sample.tar.gz") as tf:
        assert sorted(tf.getnames()) == sorted(make_fixtures.ARCHIVE_MEMBERS)
    assert (tmp_path / "sample.pdf").read_bytes().startswith(b"%PDF")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_ffmpeg_fixture(make_fixtures, tmp_path):
    out = tmp_path / "sample-audio.wav"
    make_fixtures.make_audio("wav", out)
    assert out.read_bytes()[:4] == b"RIFF"


def test_missing_tool_is_skipped_not_failed(make_fixtures, tmp_path, monkeypatch):
    monkeypatch.setattr(make_fixtures.shutil, "which", lambda _name: None)
    results = make_fixtures.generate(tmp_path)
    by_name = {r.name: r for r in results}
    assert by_name["sample-audio.mp3"].reason == "skipped: ffmpeg not installed"
    assert by_name["sample.png"].ok
    assert not any(r.reason.startswith("failed") for r in results)
