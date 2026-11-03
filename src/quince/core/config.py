"""User configuration in ``~/.config/quince/config.toml``.

A missing file means defaults. Invalid values never stop Quince: each one falls back to its
default and a warning is logged.

```toml
[triggers]
convert = ["Shift_L", "Shift_R"]
tools = ["Control_L", "Control_R"]
actions = ["Alt_L"]
max_tap_ms = 400

[output]
location = "source"          # "source" (next to the original) or "folder"
folder = "~/Converted"       # used when location = "folder"

[wheels.convert]
order = ["convert.image.jpg"] # shown first, in this order; the rest keep their default order
disabled = ["convert.image.bmp"]
```
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any

import tomli_w

from quince.core import paths

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

log = logging.getLogger(__name__)

WHEELS = ("convert", "tools", "actions")
DEFAULT_TRIGGERS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "convert": ("Shift_L", "Shift_R"),
        "tools": ("Control_L", "Control_R"),
        # Right Alt is AltGr (ISO_Level3_Shift) on the Turkish layout.
        "actions": ("Alt_L",),
    }
)
DEFAULT_MAX_TAP_MS = 400
MAX_TAP_MS_RANGE = (50, 2000)


def validate_triggers(triggers: Mapping[str, Sequence[str]]) -> dict[str, str]:
    """Return a key -> wheel map; reject Super/Hyper keys and keys bound twice."""
    by_key: dict[str, str] = {}
    for wheel, keys in triggers.items():
        for key in keys:
            if key.startswith(("Super", "Hyper")):
                raise ValueError(f"{key} cannot be used as a trigger")
            if key in by_key:
                raise ValueError(f"{key} is bound to both {by_key[key]} and {wheel}")
            by_key[key] = wheel
    return by_key


class OutputLocation(str, Enum):
    SOURCE = "source"
    FOLDER = "folder"


@dataclass(frozen=True)
class Triggers:
    keys: Mapping[str, tuple[str, ...]] = DEFAULT_TRIGGERS
    max_tap_ms: int = DEFAULT_MAX_TAP_MS


@dataclass(frozen=True)
class OutputConfig:
    location: OutputLocation = OutputLocation.SOURCE
    folder: Path | None = None

    def directory_for(self, source: Path) -> Path:
        """Where outputs made from ``source`` go."""
        if self.location is OutputLocation.FOLDER and self.folder is not None:
            return self.folder
        return Path(source).parent


@dataclass(frozen=True)
class WheelPrefs:
    """Per-wheel preferences: operation ids shown first (in order) and hidden ids."""

    order: tuple[str, ...] = ()
    disabled: frozenset[str] = frozenset()


def _default_wheels() -> Mapping[str, WheelPrefs]:
    return MappingProxyType({name: WheelPrefs() for name in WHEELS})


@dataclass(frozen=True)
class Config:
    triggers: Triggers = field(default_factory=Triggers)
    output: OutputConfig = field(default_factory=OutputConfig)
    wheels: Mapping[str, WheelPrefs] = field(default_factory=_default_wheels)

    def wheel(self, name: str) -> WheelPrefs:
        return self.wheels.get(name, WheelPrefs())


# --- parsing ----------------------------------------------------------------


def _string_list(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    return None


def _table(data: Mapping[str, Any], key: str, warnings: list[str]) -> Mapping[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        warnings.append(f"[{key}] must be a table; using defaults")
        return {}
    return value


def _warn_unknown(table: Mapping[str, Any], known: Sequence[str], where: str, warnings: list[str]):
    for key in table:
        if key not in known:
            warnings.append(f"unknown setting {where}{key!r} ignored")


def _parse_triggers(data: Mapping[str, Any], warnings: list[str]) -> Triggers:
    table = _table(data, "triggers", warnings)
    _warn_unknown(table, (*WHEELS, "max_tap_ms"), "triggers.", warnings)
    keys = dict(DEFAULT_TRIGGERS)
    for wheel in WHEELS:
        if wheel in table:
            value = _string_list(table[wheel])
            if value is None:
                warnings.append(f"triggers.{wheel} must be a list of key names; using default")
            else:
                keys[wheel] = value
    try:
        validate_triggers(keys)
    except ValueError as exc:
        warnings.append(f"triggers: {exc}; using default triggers")
        keys = dict(DEFAULT_TRIGGERS)

    max_tap_ms = table.get("max_tap_ms", DEFAULT_MAX_TAP_MS)
    low, high = MAX_TAP_MS_RANGE
    if (
        isinstance(max_tap_ms, bool)
        or not isinstance(max_tap_ms, int)
        or not low <= max_tap_ms <= high
    ):
        warnings.append(f"triggers.max_tap_ms must be an integer {low}-{high}; using default")
        max_tap_ms = DEFAULT_MAX_TAP_MS
    return Triggers(MappingProxyType(keys), max_tap_ms)


def _parse_output(data: Mapping[str, Any], warnings: list[str]) -> OutputConfig:
    table = _table(data, "output", warnings)
    _warn_unknown(table, ("location", "folder"), "output.", warnings)
    try:
        location = OutputLocation(table.get("location", OutputLocation.SOURCE.value))
    except ValueError:
        choices = ", ".join(repr(o.value) for o in OutputLocation)
        warnings.append(f"output.location must be one of {choices}; using 'source'")
        location = OutputLocation.SOURCE

    folder = None
    raw = table.get("folder")
    if raw is not None:
        if isinstance(raw, str) and raw and Path(raw).expanduser().is_absolute():
            folder = Path(raw).expanduser()
        else:
            warnings.append("output.folder must be an absolute path (~ allowed); ignored")
    if location is OutputLocation.FOLDER and folder is None:
        warnings.append("output.location is 'folder' but output.folder is not set; using 'source'")
        location = OutputLocation.SOURCE
    return OutputConfig(location, folder)


def _parse_wheels(data: Mapping[str, Any], warnings: list[str]) -> Mapping[str, WheelPrefs]:
    table = _table(data, "wheels", warnings)
    _warn_unknown(table, WHEELS, "wheels.", warnings)
    wheels = {}
    for wheel in WHEELS:
        prefs = table.get(wheel, {})
        if not isinstance(prefs, dict):
            warnings.append(f"[wheels.{wheel}] must be a table; using defaults")
            prefs = {}
        _warn_unknown(prefs, ("order", "disabled"), f"wheels.{wheel}.", warnings)
        lists = {}
        for key in ("order", "disabled"):
            value = _string_list(prefs.get(key, []))
            if value is None:
                warnings.append(f"wheels.{wheel}.{key} must be a list of operation ids; ignored")
                value = ()
            lists[key] = tuple(dict.fromkeys(value))
        wheels[wheel] = WheelPrefs(lists["order"], frozenset(lists["disabled"]))
    return MappingProxyType(wheels)


def from_dict(data: Mapping[str, Any]) -> tuple[Config, list[str]]:
    """Build a Config from parsed TOML. Returns the config and the warnings."""
    warnings: list[str] = []
    _warn_unknown(data, ("triggers", "output", "wheels"), "", warnings)
    config = Config(
        triggers=_parse_triggers(data, warnings),
        output=_parse_output(data, warnings),
        wheels=_parse_wheels(data, warnings),
    )
    return config, warnings


def to_dict(config: Config) -> dict[str, Any]:
    output: dict[str, Any] = {"location": config.output.location.value}
    if config.output.folder is not None:
        output["folder"] = str(config.output.folder)
    return {
        "triggers": {
            **{w: list(config.triggers.keys.get(w, ())) for w in WHEELS},
            "max_tap_ms": config.triggers.max_tap_ms,
        },
        "output": output,
        "wheels": {
            w: {
                "order": list(config.wheel(w).order),
                "disabled": sorted(config.wheel(w).disabled),
            }
            for w in WHEELS
        },
    }


def dumps(config: Config) -> str:
    return tomli_w.dumps(to_dict(config))


# --- files ------------------------------------------------------------------


def load(path: Path | None = None) -> Config:
    """Load the config file; defaults when it does not exist or cannot be read."""
    path = path or paths.config_file()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Config()
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        log.warning("cannot read %s (%s); using default settings", path, exc)
        return Config()
    config, warnings = from_dict(data)
    for warning in warnings:
        log.warning("%s: %s", path, warning)
    return config


def save(config: Config, path: Path | None = None) -> Path:
    """Write the config atomically (temporary file + rename)."""
    path = path or paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "# Quince settings. Invalid values fall back to their defaults.\n\n"
    text += dumps(config)
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".toml", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
