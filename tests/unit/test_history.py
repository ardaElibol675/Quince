import sqlite3
from pathlib import Path

from quince.core import history
from quince.core.history import History, should_record


def test_should_record_only_convert_and_tools():
    assert should_record("convert.image.jpg")
    assert should_record("tool.compress")
    assert not should_record("action.copy")
    assert not should_record("action.move")
    assert not should_record("")


def test_default_file_is_in_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert history.default_file() == tmp_path / "quince" / "history.sqlite3"


def test_record_and_read_back_newest_first(tmp_path):
    h = History(tmp_path / "h.sqlite3")
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.webp"
    first = h.record([a], op_id="convert.image.jpg", title="Convert to JPG",
                     sources=[tmp_path / "a.png"], when=100.0)  # fmt: skip
    h.record([b], op_id="convert.image.webp", title="Convert to WebP", when=200.0)
    entries = h.entries()
    assert [e.path for e in entries] == [b, a]
    assert entries[1].id == first[0].id
    assert entries[1].sources == (tmp_path / "a.png",)
    assert entries[1].title == "Convert to JPG" and entries[1].created == 100.0
    # A new History object on the same file sees the same entries.
    assert [e.path for e in History(tmp_path / "h.sqlite3").entries()] == [b, a]


def test_record_without_outputs_writes_nothing(tmp_path):
    h = History(tmp_path / "h.sqlite3")
    assert h.record([], op_id="convert.image.jpg", title="x") == []
    assert not (tmp_path / "h.sqlite3").exists()


def test_search_matches_every_word_in_name_folder_title_and_sources(tmp_path):
    h = History(tmp_path / "h.sqlite3")
    h.record([tmp_path / "Rapor özet.md"], op_id="convert.text.md",
             title="Convert to Markdown", sources=[tmp_path / "rapor.pdf"])  # fmt: skip
    h.record([tmp_path / "photo.jpg"], op_id="convert.image.jpg", title="Convert to JPG")
    assert [e.name for e in h.entries("markdown")] == ["Rapor özet.md"]
    assert [e.name for e in h.entries("RAPOR.PDF")] == ["Rapor özet.md"]
    assert [e.name for e in h.entries("özet rapor")] == ["Rapor özet.md"]
    assert [e.name for e in h.entries("jpg photo")] == ["photo.jpg"]
    assert h.entries("photo markdown") == []
    assert len(h.entries("  ")) == 2
    assert len(h.entries(tmp_path.name)) == 2  # the folder counts


def test_recent_skips_missing_files_and_duplicates(tmp_path):
    h = History(tmp_path / "h.sqlite3")
    kept = tmp_path / "kept.png"
    kept.write_bytes(b"x")
    gone = tmp_path / "gone.png"
    other = tmp_path / "other.png"
    other.write_bytes(b"x")
    h.record([kept], op_id="convert.image.png", title="t", when=1)
    h.record([gone], op_id="convert.image.png", title="t", when=2)
    h.record([kept], op_id="convert.image.png", title="t", when=3)
    h.record([other], op_id="convert.image.png", title="t", when=4)
    assert [e.path for e in h.recent(10)] == [other, kept]
    assert [e.path for e in h.recent(1)] == [other]
    assert not h.entries("gone")[0].exists


def test_remove_and_clear_forget_entries_but_keep_files(tmp_path):
    h = History(tmp_path / "h.sqlite3")
    out = tmp_path / "a.jpg"
    out.write_bytes(b"x")
    [entry] = h.record([out], op_id="convert.image.jpg", title="t")
    h.record([tmp_path / "b.jpg"], op_id="convert.image.jpg", title="t")
    h.remove([entry.id])
    assert [e.name for e in h.entries()] == ["b.jpg"]
    assert out.exists()
    h.clear()
    assert h.entries() == []


def test_oldest_entries_are_dropped_past_the_limit(tmp_path):
    h = History(tmp_path / "h.sqlite3", max_entries=3)
    for i in range(5):
        h.record([tmp_path / f"{i}.jpg"], op_id="convert.image.jpg", title="t", when=i)
    assert [e.name for e in h.entries()] == ["4.jpg", "3.jpg", "2.jpg"]


def test_a_broken_database_is_moved_aside(tmp_path):
    path = tmp_path / "h.sqlite3"
    path.write_bytes(b"this is not a database" * 100)
    h = History(path)
    assert h.entries() == []
    h.record([tmp_path / "a.jpg"], op_id="convert.image.jpg", title="t")
    assert [e.name for e in History(path).entries()] == ["a.jpg"]
    assert len(list(tmp_path.glob("h.sqlite3.broken-*"))) == 1


def test_a_database_from_a_newer_quince_is_not_used(tmp_path):
    path = tmp_path / "h.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 99")
    h = History(path)
    assert h.entries() == []
    assert list(tmp_path.glob("h.sqlite3.broken-*"))  # kept, not deleted


def test_an_unwritable_location_means_an_empty_library(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    h = History(blocker / "sub" / "h.sqlite3")
    assert h.record([Path("/x/a.jpg")], op_id="convert.image.jpg", title="t") == []
    assert h.entries() == []
