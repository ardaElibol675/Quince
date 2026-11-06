import re
from pathlib import Path

import pytest

from quince.core import deps
from quince.core.config import CONVERTED, WheelPrefs
from quince.core.operations import Accepts, Kind, Operation
from quince.core.registry import Registry, discover
from quince.operations.demo import OPERATIONS as DEMO_OPERATIONS
from quince.ui.settings import model

SRC = Path(__file__).resolve().parents[2] / "src" / "quince"


@pytest.fixture(scope="module")
def registry():
    return discover()


def _tool(op_id, label, order=10, suffixes=()):
    return Operation(
        id=op_id, kind=Kind.TOOL, label=label, icon="x", accepts=Accepts(), order=order,
        suffixes=suffixes,
    )  # fmt: skip


def test_trigger_conflicts_are_errors_and_gaps_warnings():
    ok = model.check_triggers(
        {"convert": ("Shift_L",), "tools": ("Control_L",), "actions": ("Alt_L",)}
    )
    assert ok.errors == () and ok.warnings == ()

    clash = model.check_triggers({"convert": ("Shift_L",), "tools": ("Shift_L",), "actions": ()})
    assert clash.errors == ("Left Shift is chosen for both Convert and Tools",)
    assert clash.warnings == ("Actions has no key: it cannot open during a drag",)

    altgr = model.check_triggers(
        {"convert": ("Shift_L",), "tools": ("Super_L",), "actions": ("Alt_R",)}
    )
    assert altgr.errors == ("Super_L cannot be a trigger",)
    assert any("AltGr" in w for w in altgr.warnings)


def test_super_is_never_offered():
    assert not any(k.startswith(("Super", "Hyper")) for k, _label in model.TRIGGER_KEYS)
    keys = model.offered_keys({"convert": ("Shift_L", "F13")})
    assert keys[-1] == "F13" and keys.count("Shift_L") == 1


def test_trigger_hint_names_the_keys():
    hint = model.trigger_hint(
        {"convert": ("Shift_L", "Shift_R"), "tools": ("Control_R",), "actions": ("Alt_L",)}
    )
    assert (
        hint == "Select files, then press Enter or tap Shift / Right Ctrl / Left Alt for a wheel."
    )
    assert "tap" not in model.trigger_hint({"convert": (), "tools": (), "actions": ()})


def test_wheel_rows_follow_the_users_order_and_switches():
    reg = Registry([_tool("tool.a", "A", 1), _tool("tool.b", "B", 2), _tool("tool.c", "C", 3)])
    rows = model.wheel_rows(reg, "tools", WheelPrefs(("tool.c",), frozenset({"tool.a"})))
    assert [(r.id, r.enabled) for r in rows] == [
        ("tool.c", True), ("tool.a", False), ("tool.b", True),
    ]  # fmt: skip


def test_prefs_keep_the_order_only_when_it_changed():
    reg = Registry([_tool("tool.a", "A", 1), _tool("tool.b", "B", 2)])
    same = model.prefs_from_rows(reg, "tools", [("tool.a", True), ("tool.b", False)])
    assert same == WheelPrefs((), frozenset({"tool.b"}))
    moved = model.prefs_from_rows(reg, "tools", [("tool.b", True), ("tool.a", True)])
    assert moved == WheelPrefs(("tool.b", "tool.a"), frozenset())


def test_convert_rows_name_format_and_category(registry):
    rows = model.wheel_rows(registry, "convert", WheelPrefs())
    texts = {r.id: r.text for r in rows}
    assert texts["convert.image.jpg"] == "JPG — JPEG image (Image)"
    assert len(rows) == len(registry.operations(Kind.CONVERT))


def test_unavailable_items_are_grouped_by_reason(registry):
    nothing = deps.Availability([])
    groups = dict(model.unavailable_by_reason(registry, nothing, "tools"))
    assert groups, "every tool needs something"
    assert all(reason.startswith("needs ") for reason in groups)
    assert model.unavailable_by_reason(registry, deps.Availability.everything(), "convert") == []


def test_suffix_rows_start_with_conversions(registry):
    rows = model.suffix_rows(registry)
    assert rows[0] == model.SuffixRow(CONVERTED, "", "Convert")
    by_key = {r.key: r for r in rows}
    assert "Compress" in by_key["compressed"].used_by
    assert by_key["edited"].used_by.count("Edit") >= 4  # image, video, sound, PDF
    assert len(by_key) == len(rows)
    assert model.example_name("small") == "photo-small.jpg"
    assert model.example_name("") == "photo.jpg"


def test_operation_suffixes_are_checked():
    with pytest.raises(ValueError, match="bad suffix"):
        _tool("tool.x", "X", suffixes=(CONVERTED,))
    with pytest.raises(ValueError, match="bad suffix"):
        _tool("tool.x", "X", suffixes=("-x",))


_NEW_OUTPUT = re.compile(r"new_output\([^)]*?suffix=\"([^\"]+)\"", re.S)
_SUFFIXED = re.compile(r"suffixed\(([^)]*)\)")
_LITERAL = re.compile(r"\"([^\"]+)\"")


def test_every_suffix_in_use_is_declared_by_an_operation(registry):
    """Settings can rename only declared suffixes; a literal one would slip past it."""
    declared = {s for op in (*registry, *DEMO_OPERATIONS) for s in op.suffixes}
    used = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        used |= set(_NEW_OUTPUT.findall(text))
        for args in _SUFFIXED.findall(text):
            used |= set(_LITERAL.findall(args))
    assert {"compressed", "edited", "parts", "pages", "frame", "frames"} <= used
    assert used - declared == set()
    assert declared - used == set(), "declared but never used"
