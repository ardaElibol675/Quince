"""Options forms for tools that need a few settings (CLAUDE.md Phase 12: no full editor).

An operation's ``start`` step describes a ``Form``; the UI shows it as a small dialog
(``Host.ask_options``) prefilled with the values used last time, and returns the values.
Forms are data, so they are unit tested without Qt and every tool gets the same dialog.

The last values are state, not settings: ``~/.local/state/quince/tool-options.json``,
``{form id: {field key: value}}``; editors keep their last settings there too
(``load_section`` / ``save_section``). A broken file or a value that no longer fits a field means
that field's default.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from quince.core import paths

log = logging.getLogger(__name__)

FILE_NAME = "tool-options.json"
Values = dict[str, Any]


@dataclass(frozen=True)
class Choice:
    """A list of options; ``options`` are (value, label) pairs."""

    key: str
    label: str
    options: tuple[tuple[str, str], ...]
    default: str = ""

    def __post_init__(self) -> None:
        if not self.options:
            raise ValueError(f"field {self.key}: no options")
        if not self.default:
            object.__setattr__(self, "default", self.options[0][0])
        if self.default not in self.values:
            raise ValueError(f"field {self.key}: default {self.default!r} is not an option")

    @property
    def values(self) -> tuple[str, ...]:
        return tuple(v for v, _label in self.options)

    def clean(self, value: Any) -> str:
        return value if value in self.values else self.default


@dataclass(frozen=True)
class Number:
    """A number between ``minimum`` and ``maximum`` (whole when ``decimals`` is 0)."""

    key: str
    label: str
    minimum: float
    maximum: float
    default: float
    suffix: str = ""
    decimals: int = 0

    def clean(self, value: Any) -> float | int:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = float(self.default)
        if number != number:  # NaN
            number = float(self.default)
        number = min(self.maximum, max(self.minimum, number))
        return round(number) if self.decimals == 0 else round(number, self.decimals)


@dataclass(frozen=True)
class Text:
    """A line of text, e.g. a file name; empty means the default."""

    key: str
    label: str
    default: str = ""
    max_length: int = 200

    def clean(self, value: Any) -> str:
        text = str(value).strip() if isinstance(value, str) else ""
        return (text or self.default)[: self.max_length]


Field = Choice | Number | Text


@dataclass(frozen=True)
class Shown:
    """Show a field only while another field (a ``Choice``) has one of ``values``."""

    key: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class Form:
    """A small options dialog. ``id`` names the remembered values (e.g. ``"compress.image"``).

    ``remember`` lists the keys whose values are kept for next time (all when None); a file
    name, for example, should not be.
    """

    id: str
    title: str
    fields: tuple[Field, ...]
    accept_label: str = "OK"
    note: str = ""
    shown_when: Mapping[str, Shown] = field(default_factory=dict)
    remember: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        keys = [f.key for f in self.fields]
        if len(set(keys)) != len(keys):
            raise ValueError(f"form {self.id}: duplicate field keys")
        for key, rule in self.shown_when.items():
            if key not in keys or rule.key not in keys:
                raise ValueError(f"form {self.id}: unknown field in shown_when")

    def get(self, key: str) -> Field:
        return next(f for f in self.fields if f.key == key)

    def defaults(self) -> Values:
        return {f.key: f.clean(f.default) for f in self.fields}

    def clean(self, values: Mapping[str, Any]) -> Values:
        """Every field's value, fitted to the field; missing or bad ones are defaults."""
        return {f.key: f.clean(values.get(f.key, f.default)) for f in self.fields}

    def is_shown(self, key: str, values: Mapping[str, Any]) -> bool:
        rule = self.shown_when.get(key)
        return rule is None or values.get(rule.key) in rule.values

    def remembered_keys(self) -> tuple[str, ...]:
        return tuple(f.key for f in self.fields) if self.remember is None else self.remember


# --- remembered values -------------------------------------------------------------------


def default_file() -> Path:
    return paths.state_dir() / FILE_NAME


def _read(path: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        log.warning("cannot read %s (%s); ignoring it", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)}


def initial_values(form: Form, path: Path | None = None) -> Values:
    """The defaults, overridden by what was used last time (only remembered keys)."""
    saved = _read(path or default_file()).get(form.id, {})
    keys = form.remembered_keys()
    return form.clean({**form.defaults(), **{k: v for k, v in saved.items() if k in keys}})


def remember(form: Form, values: Mapping[str, Any], path: Path | None = None) -> None:
    cleaned = form.clean(values)
    save_section(form.id, {k: cleaned[k] for k in form.remembered_keys()}, path)


def load_section(key: str, path: Path | None = None) -> dict[str, Any]:
    """What was saved under ``key`` (e.g. an editor's last settings); {} when nothing."""
    return dict(_read(path or default_file()).get(key, {}))


def save_section(key: str, values: Mapping[str, Any], path: Path | None = None) -> None:
    """Keep ``values`` (JSON-compatible) under ``key`` for next time; failures are logged."""
    path = path or default_file()
    data = _read(path)
    data[key] = dict(values)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tool-options-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as exc:
        log.warning("cannot save tool options to %s: %s", path, exc)


# --- ordering --------------------------------------------------------------------------

_DIGITS = re.compile(r"(\d+)")


def natural_key(path: Path | str) -> tuple:
    """Sort key for file names: ``page2`` before ``page10``, case-insensitive."""
    name = Path(path).name.casefold()
    return tuple(int(part) if part.isdigit() else part for part in _DIGITS.split(name))


def natural_order(items: Sequence[Path]) -> list[Path]:
    return sorted(items, key=natural_key)
