from pathlib import Path

from quince.ui.shelf.model import ShelfItems


def test_add_keeps_order_and_skips_duplicates(tmp_path):
    items = ShelfItems()
    a, b = tmp_path / "a", tmp_path / "b"
    assert items.add([a, b, a]) == [a, b]
    assert items.add([str(b), tmp_path / "x" / ".." / "a"]) == []  # same paths
    assert items.paths == [a, b]
    assert a in items and str(b) in items and len(items) == 2


def test_remove_clear_and_prune(tmp_path):
    items = ShelfItems()
    a, b, c = (tmp_path / n for n in "abc")
    a.write_text("a")
    items.add([a, b, c])
    assert items.remove([c]) == [c]
    assert items.prune() == [b]  # b does not exist
    assert items.paths == [a]
    items.clear()
    assert len(items) == 0


def test_relative_paths_are_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    items = ShelfItems()
    items.add(["photo.png"])
    assert items.paths == [Path(tmp_path) / "photo.png"]
