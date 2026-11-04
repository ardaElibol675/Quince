from pathlib import Path

from quince.core import recent


def test_remember_puts_folders_first_without_duplicates(tmp_path):
    store = tmp_path / "recent.json"
    folders = [tmp_path / n for n in ("a", "b", "c")]
    for f in folders:
        f.mkdir()
    for f in (*folders, folders[0]):
        recent.remember(f, store)
    assert recent.destinations(store) == [folders[0], folders[2], folders[1]]


def test_list_is_capped_and_skips_missing_folders(tmp_path):
    store = tmp_path / "recent.json"
    folders = []
    for i in range(recent.MAX_RECENT + 3):
        folder = tmp_path / f"d{i}"
        folder.mkdir()
        folders.append(folder)
        recent.remember(folder, store)
    assert len(recent.destinations(store)) == recent.MAX_RECENT
    folders[-1].rmdir()
    assert recent.destinations(store)[0] == folders[-2]


def test_broken_file_means_no_recents(tmp_path):
    store = tmp_path / "recent.json"
    store.write_text("{not json")
    assert recent.destinations(store) == []
    store.write_text('{"a": 1}')
    assert recent.destinations(store) == []
    store.write_text('["relative/path", 3]')
    assert recent.destinations(store) == []
    (tmp_path / "x").mkdir()
    assert recent.remember(tmp_path / "x", store) == [tmp_path / "x"]


def test_default_file_is_in_the_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert recent.default_file() == tmp_path / "quince" / "recent-destinations.json"
    recent.remember(tmp_path)
    assert recent.destinations() == [Path(tmp_path)]
