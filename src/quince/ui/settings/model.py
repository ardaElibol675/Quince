"""What the Settings window shows and stores, as plain logic (no Qt), so it can be tested.

- Trigger keys: a fixed list of keys a wheel can use; a key chosen for two wheels is an
  error (not saved), a wheel without keys or Right Alt (AltGr on many layouts) a warning.
- Wheels: every operation of a wheel in the user's order, each on or off; the order is
  stored only when it differs from the default one.
- Output names: every suffix the operations declare, and the one of conversions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from quince.core import formats
from quince.core.config import CONVERTED, WHEELS, WheelPrefs
from quince.core.deps import Availability
from quince.core.operations import Kind, Operation
from quince.core.registry import Registry

WHEEL_NAMES = {"convert": "Convert", "tools": "Tools", "actions": "Actions"}

# Keys offered as triggers, in the order shown. Super is never offered (Hard Rule 8).
TRIGGER_KEYS: tuple[tuple[str, str], ...] = (
    ("Shift_L", "Left Shift"),
    ("Shift_R", "Right Shift"),
    ("Control_L", "Left Ctrl"),
    ("Control_R", "Right Ctrl"),
    ("Alt_L", "Left Alt"),
    ("Alt_R", "Right Alt"),
    ("ISO_Level3_Shift", "AltGr"),
    ("Menu", "Menu key"),
)
_KEY_LABELS = dict(TRIGGER_KEYS)


def key_label(name: str) -> str:
    return _KEY_LABELS.get(name, name)


def offered_keys(triggers: Mapping[str, Sequence[str]]) -> list[str]:
    """The fixed keys, then any other key the config file names (kept, so it can be unset)."""
    keys = [name for name, _label in TRIGGER_KEYS]
    for wheel_keys in triggers.values():
        keys += [k for k in wheel_keys if k not in keys]
    return keys


@dataclass(frozen=True)
class TriggerCheck:
    errors: tuple[str, ...]  # the keys cannot be used (not saved)
    warnings: tuple[str, ...]  # saved, but worth knowing


def check_triggers(triggers: Mapping[str, Sequence[str]]) -> TriggerCheck:
    errors, warnings = [], []
    owners: dict[str, list[str]] = {}
    for wheel in WHEELS:
        for key in triggers.get(wheel, ()):
            owners.setdefault(key, []).append(wheel)
            if key.startswith(("Super", "Hyper")):
                errors.append(f"{key_label(key)} cannot be a trigger")
    for key, wheels in owners.items():
        if len(wheels) > 1:
            names = " and ".join(WHEEL_NAMES[w] for w in wheels)
            errors.append(f"{key_label(key)} is chosen for both {names}")
    for wheel in WHEELS:
        if not triggers.get(wheel):
            warnings.append(f"{WHEEL_NAMES[wheel]} has no key: it cannot open during a drag")
    if "Alt_R" in owners:
        warnings.append(
            "On many keyboard layouts (Turkish too) Right Alt is AltGr; "
            "if Right Alt does nothing, choose AltGr instead"
        )
    return TriggerCheck(tuple(errors), tuple(warnings))


def wheel_key_name(keys: Sequence[str]) -> str:
    """One short name for a wheel's keys: ``Shift`` for both Shifts, ``Left Alt`` for one."""
    labels = [key_label(k) for k in keys]
    sides = {label.removeprefix("Left ").removeprefix("Right ") for label in labels}
    if len(sides) == 1 and len(labels) > 1:
        return sides.pop()
    return " or ".join(labels)


def trigger_hint(triggers: Mapping[str, Sequence[str]]) -> str:
    """The Home hint, naming the configured keys."""
    names = [wheel_key_name(triggers[w]) for w in WHEELS if triggers.get(w)]
    if not names:
        return "Select files, then press Enter for a wheel."
    return f"Select files, then press Enter or tap {' / '.join(names)} for a wheel."


# --- wheels ---------------------------------------------------------------------------


def operation_text(op: Operation) -> str:
    """How an operation is listed: ``JPG — JPEG image`` or ``Edit image``."""
    title = op.title.rstrip("…").rstrip(".")
    if op.kind is Kind.CONVERT:
        category = formats.get(op.routes[0].target).category.label
        return f"{op.label} — {title} ({category})" if title != op.label else op.label
    return title


@dataclass(frozen=True)
class WheelRow:
    id: str
    text: str
    icon: str
    enabled: bool


def wheel_rows(registry: Registry, wheel: str, prefs: WheelPrefs) -> list[WheelRow]:
    """Every operation of ``wheel``, in the user's order, each with its on/off state."""
    ops = registry.operations(Kind.from_wheel(wheel))
    rank = {op_id: i for i, op_id in enumerate(prefs.order)}
    ops = sorted(ops, key=lambda op: rank.get(op.id, len(rank)))
    return [WheelRow(op.id, operation_text(op), op.icon, op.id not in prefs.disabled) for op in ops]


def prefs_from_rows(registry: Registry, wheel: str, rows: Iterable[tuple[str, bool]]) -> WheelPrefs:
    """The preferences for rows (id, on) in the order shown.

    The order is kept only when it differs from the default; operations in neither list keep
    their default place, so a new operation shows up without anyone listing it.
    """
    rows = list(rows)
    ids = tuple(op_id for op_id, _on in rows)
    default = tuple(op.id for op in registry.operations(Kind.from_wheel(wheel)))
    order = () if ids == default else ids
    return WheelPrefs(order, frozenset(op_id for op_id, on in rows if not on))


def unavailable_by_reason(
    registry: Registry, available: Availability, wheel: str
) -> list[tuple[str, list[str]]]:
    """What ``wheel`` hides because of missing programs: (reason, what), reasons in order."""
    kind = Kind.from_wheel(wheel)
    groups: dict[str, list[str]] = {}
    for hidden in registry.unavailable(available):
        if registry.get(hidden.id).kind is kind:
            groups.setdefault(hidden.reason, []).append(hidden.label)
    return list(groups.items())


# --- output names ---------------------------------------------------------------------


@dataclass(frozen=True)
class SuffixRow:
    key: str  # the default word, or CONVERTED
    default: str  # "" for conversions
    used_by: str  # e.g. "Edit image, Edit video"


def suffix_rows(registry: Registry) -> list[SuffixRow]:
    """Conversions first, then every declared suffix in the order the tools list them."""
    users: dict[str, list[str]] = {}
    for op in registry.operations():
        for suffix in op.suffixes:
            names = users.setdefault(suffix, [])
            if (name := operation_text(op)) not in names:
                names.append(name)
    rows = [SuffixRow(CONVERTED, "", "Convert")]
    rows += [SuffixRow(key, key, ", ".join(names)) for key, names in users.items()]
    return rows


def example_name(word: str, extension: str = "jpg") -> str:
    """``photo-word.jpg`` (``photo.jpg`` without a word)."""
    return f"photo-{word}.{extension}" if word else f"photo.{extension}"
