import json
from pathlib import Path

import pytest

from quince.core import forms
from quince.core.forms import Choice, Form, Number, Shown, Text, natural_order

FORM = Form(
    id="test.form",
    title="T",
    fields=(
        Choice("mode", "Mode", (("a", "A"), ("b", "B"))),
        Number("n", "N", 1, 10, 5),
        Number("x", "X", 0.1, 2.0, 1.0, decimals=1),
        Text("name", "Name", default="Archive"),
    ),
    shown_when={"n": Shown("mode", ("b",))},
    remember=("mode", "n", "x"),
)


def test_defaults_and_clean():
    assert FORM.defaults() == {"mode": "a", "n": 5, "x": 1.0, "name": "Archive"}
    cleaned = FORM.clean({"mode": "zzz", "n": 99, "x": "nan", "name": "  Yedek  ", "extra": 1})
    assert cleaned == {"mode": "a", "n": 10, "x": 1.0, "name": "Yedek"}
    assert FORM.clean({"n": "3.6", "x": 0.04, "name": ""}) == {
        "mode": "a", "n": 4, "x": 0.1, "name": "Archive"
    }  # fmt: skip


def test_shown_when():
    assert not FORM.is_shown("n", {"mode": "a"})
    assert FORM.is_shown("n", {"mode": "b"})
    assert FORM.is_shown("x", {"mode": "a"})


def test_invalid_forms():
    with pytest.raises(ValueError):
        Choice("c", "C", ())
    with pytest.raises(ValueError):
        Choice("c", "C", (("a", "A"),), default="b")
    with pytest.raises(ValueError):
        Form("f", "F", (Number("n", "N", 0, 1, 0), Number("n", "N", 0, 1, 0)))
    with pytest.raises(ValueError):
        Form("f", "F", (Number("n", "N", 0, 1, 0),), shown_when={"n": Shown("nope", ("a",))})


def test_remembered_values_round_trip(tmp_path):
    path = tmp_path / "options.json"
    assert forms.initial_values(FORM, path) == FORM.defaults()
    forms.remember(FORM, {"mode": "b", "n": 7, "x": 1.5, "name": "Not kept"}, path)
    assert forms.initial_values(FORM, path) == {"mode": "b", "n": 7, "x": 1.5, "name": "Archive"}
    data = json.loads(path.read_text())
    assert data == {"test.form": {"mode": "b", "n": 7, "x": 1.5}}


def test_broken_or_stale_remembered_values(tmp_path):
    path = tmp_path / "options.json"
    path.write_text("{ broken")
    assert forms.initial_values(FORM, path) == FORM.defaults()
    path.write_text(json.dumps({"test.form": {"mode": "gone", "n": 1000}}))
    assert forms.initial_values(FORM, path)["mode"] == "a"
    assert forms.initial_values(FORM, path)["n"] == 10


def test_default_file_is_in_the_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert forms.default_file() == tmp_path / "quince" / "tool-options.json"


def test_natural_order():
    names = ["page10.pdf", "Page2.pdf", "page1.pdf", "a.pdf", "part 10.mp4", "part 9.mp4"]
    assert [p.name for p in natural_order([Path(n) for n in names])] == [
        "a.pdf", "page1.pdf", "Page2.pdf", "page10.pdf", "part 9.mp4", "part 10.mp4"
    ]  # fmt: skip


def test_sections_for_editor_settings(tmp_path):
    path = tmp_path / "tool-options.json"
    assert forms.load_section("editor.frame", path) == {}
    forms.save_section("editor.frame", {"padding": 0.1, "background": [0, 0, 0, 255]}, path)
    forms.save_section("editor.collage", {"layout": "row"}, path)
    assert forms.load_section("editor.frame", path) == {
        "padding": 0.1,
        "background": [0, 0, 0, 255],
    }
    assert forms.load_section("editor.collage", path) == {"layout": "row"}
    path.write_text("not json")
    assert forms.load_section("editor.frame", path) == {}  # a broken file means defaults
