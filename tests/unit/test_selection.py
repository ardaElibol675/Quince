from pathlib import Path

from quince.core import formats
from quince.core.formats import Category
from quince.core.selection import Item, Selection


def _sel(*specs):
    """Build a selection from format ids; "/" is a folder, None an unknown file."""
    items = []
    for i, spec in enumerate(specs):
        if spec == "/":
            items.append(Item(Path(f"/tmp/dir{i}"), is_dir=True))
        else:
            items.append(Item(Path(f"/tmp/f{i}"), format=formats.find(spec) if spec else None))
    return Selection(tuple(items))


def test_single_and_same_category():
    single = _sel("png")
    assert single.category is Category.IMAGE and single.is_uniform and not single.is_mixed
    many = _sel("png", "jpg", "heic")
    assert many.category is Category.IMAGE and many.is_uniform
    assert many.format_ids == {"png", "jpg", "heic"}
    assert many.describe() == "3 files, image (HEIC, JPG, PNG)"


def test_mixed_categories():
    sel = _sel("png", "pdf")
    assert sel.is_mixed and sel.category is None and not sel.is_uniform
    assert sel.describe() == "2 files, mixed types"


def test_unknown_files():
    alone = _sel(None)
    assert not alone.is_mixed and alone.category is None and not alone.is_uniform
    assert alone.describe() == "1 file, unknown type"
    assert _sel(None, "png").is_mixed


def test_folders():
    sel = _sel("/")
    assert sel.has_folders and not sel.files and sel.category is None
    with_file = _sel("png", "/")
    assert with_file.has_folders and with_file.category is None and not with_file.is_mixed
    assert with_file.describe() == "1 file, 1 folder"


def test_empty():
    sel = Selection()
    assert sel.is_empty and len(sel) == 0 and sel.category is None
    assert sel.describe() == "nothing selected"


def test_from_paths(tmp_path):
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "folder").mkdir()
    sel = Selection.from_paths([tmp_path / "a.png", str(tmp_path / "folder")])
    assert [i.is_dir for i in sel.items] == [False, True]
    assert sel.items[0].format_id == "png"
    assert sel.folders[0].path == tmp_path / "folder"
