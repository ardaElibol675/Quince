import pytest

from quince.core import naming
from quince.core.naming import output_path, unique_path


def test_free_name_is_used_as_is(tmp_path):
    assert output_path(tmp_path / "photo.png", extension="jpg") == tmp_path / "photo.jpg"


def test_collisions_count_up(tmp_path):
    src = tmp_path / "photo.png"
    (tmp_path / "photo.jpg").touch()
    assert output_path(src, extension="jpg") == tmp_path / "photo (1).jpg"
    (tmp_path / "photo (1).jpg").touch()
    assert output_path(src, extension=".jpg") == tmp_path / "photo (2).jpg"


def test_never_returns_an_existing_path_even_the_source(tmp_path):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"original")
    out = output_path(src, extension="jpg")
    assert out == tmp_path / "photo (1).jpg"
    assert src.read_bytes() == b"original"


def test_dangling_symlink_counts_as_taken(tmp_path):
    (tmp_path / "photo.jpg").symlink_to(tmp_path / "nowhere")
    assert output_path(tmp_path / "photo.png", extension="jpg") == tmp_path / "photo (1).jpg"


def test_tool_suffix_keeps_extension(tmp_path):
    src = tmp_path / "photo.jpg"
    assert output_path(src, suffix="cropped") == tmp_path / "photo-cropped.jpg"
    (tmp_path / "photo-cropped.jpg").touch()
    assert output_path(src, suffix="cropped") == tmp_path / "photo-cropped (1).jpg"


def test_compound_extensions(tmp_path):
    src = tmp_path / "backup.tar.gz"
    assert output_path(src, extension="zip") == tmp_path / "backup.zip"
    (tmp_path / "backup.tar.gz").touch()
    assert unique_path(src) == tmp_path / "backup (1).tar.gz"


def test_no_extension_for_output_folders(tmp_path):
    src = tmp_path / "slides.pptx"
    (tmp_path / "slides").mkdir()
    assert output_path(src, extension="") == tmp_path / "slides (1)"


def test_numbered_name_continues_counting(tmp_path):
    (tmp_path / "photo (1).jpg").touch()
    assert unique_path(tmp_path / "photo (1).jpg") == tmp_path / "photo (2).jpg"


def test_reserved_paths_within_a_batch(tmp_path):
    reserved = {tmp_path / "a.webp"}
    assert output_path(tmp_path / "a.png", extension="webp", reserved=reserved) == (
        tmp_path / "a (1).webp"
    )


def test_other_directory(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    assert output_path(tmp_path / "a.png", extension="jpg", directory=out_dir) == out_dir / "a.jpg"


def test_long_names_are_shortened_to_fit(tmp_path):
    stem = "ş" * 200  # 400 bytes in UTF-8
    out = output_path(tmp_path / f"{stem}.png", extension="jpg", suffix="cropped")
    assert len(out.name.encode()) <= naming.MAX_NAME_BYTES
    assert out.name.endswith(".jpg")
    out.touch()
    again = output_path(tmp_path / f"{stem}.png", extension="jpg", suffix="cropped")
    assert again != out and again.name.endswith(" (1).jpg")
    assert len(again.name.encode()) <= naming.MAX_NAME_BYTES


def test_gives_up_instead_of_overwriting(tmp_path, monkeypatch):
    monkeypatch.setattr(naming, "MAX_ATTEMPTS", 3)
    for name in ("a.txt", "a (1).txt", "a (2).txt", "a (3).txt"):
        (tmp_path / name).touch()
    with pytest.raises(FileExistsError):
        unique_path(tmp_path / "a.txt")


def test_chosen_name_replaces_the_stem_and_stays_free(tmp_path):
    src = tmp_path / "photo.jpg"
    src.touch()
    assert output_path(src, extension="png", stem="holiday") == tmp_path / "holiday.png"
    (tmp_path / "holiday.png").touch()
    assert output_path(src, extension="png", stem="holiday") == tmp_path / "holiday (1).png"
    # the source itself is never chosen, even when the user types its name
    assert output_path(src, stem="photo") == tmp_path / "photo (1).jpg"


def test_chosen_name_is_cleaned(tmp_path):
    src = tmp_path / "photo.jpg"
    assert naming.clean_stem("  a/b  ") == "a-b"
    assert naming.clean_stem(" ..hidden. ") == "hidden"
    assert output_path(src, stem="   ") == tmp_path / "photo.jpg"  # empty: the source's
