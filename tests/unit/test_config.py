import logging
from pathlib import Path

import pytest

from quince.core import config, paths
from quince.core.config import Config, OutputLocation, WheelPrefs


def test_defaults():
    cfg = Config()
    assert cfg.triggers.keys["convert"] == ("Shift_L", "Shift_R")
    assert cfg.triggers.keys["tools"] == ("Control_L", "Control_R")
    assert cfg.triggers.keys["actions"] == ("Alt_L",)
    assert cfg.triggers.max_tap_ms == 400
    assert cfg.output.location is OutputLocation.SOURCE
    assert cfg.wheel("convert") == WheelPrefs()
    assert set(cfg.wheels) == set(config.WHEELS) == {"convert", "tools", "actions"}


def test_no_default_trigger_uses_super():
    keys = [k for keys in config.DEFAULT_TRIGGERS.values() for k in keys]
    assert not any(k.startswith(("Super", "Hyper")) for k in keys)
    config.validate_triggers(config.DEFAULT_TRIGGERS)


def test_missing_file_gives_defaults(tmp_path):
    assert config.load(tmp_path / "nope.toml") == Config()


def test_round_trip(tmp_path):
    cfg, warnings = config.from_dict(
        {
            "triggers": {"convert": ["Shift_R"], "max_tap_ms": 300},
            "output": {"location": "folder", "folder": str(tmp_path / "out")},
            "wheels": {"convert": {"order": ["convert.image.png"], "disabled": ["convert.x.y"]}},
        }
    )
    assert warnings == []
    path = config.save(cfg, tmp_path / "sub" / "config.toml")
    assert path.read_text().startswith("# Quince settings")
    loaded = config.load(path)
    assert loaded == cfg
    assert loaded.triggers.keys["convert"] == ("Shift_R",)
    assert loaded.triggers.keys["tools"] == ("Control_L", "Control_R")
    assert loaded.output.directory_for(tmp_path / "a.png") == tmp_path / "out"
    assert loaded.wheel("convert").disabled == {"convert.x.y"}
    assert not list(path.parent.glob(".config-*")), "temporary file left behind"


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        ({"triggers": {"tools": ["Super_L"]}}, "Super_L cannot be used"),
        ({"triggers": {"tools": ["Shift_L"]}}, "bound to both"),
        ({"triggers": {"convert": "Shift_L"}}, "must be a list"),
        ({"triggers": {"max_tap_ms": 5}}, "max_tap_ms"),
        ({"triggers": {"max_tap_ms": True}}, "max_tap_ms"),
        ({"triggers": []}, "must be a table"),
    ],
)
def test_invalid_triggers_fall_back_to_defaults(data, fragment):
    cfg, warnings = config.from_dict(data)
    assert any(fragment in w for w in warnings), warnings
    assert cfg.triggers == Config().triggers


def test_invalid_output_falls_back(tmp_path):
    cfg, warnings = config.from_dict({"output": {"location": "cloud"}})
    assert cfg.output.location is OutputLocation.SOURCE and warnings
    cfg, warnings = config.from_dict({"output": {"location": "folder"}})
    assert cfg.output.location is OutputLocation.SOURCE
    assert any("not set" in w for w in warnings)
    cfg, warnings = config.from_dict({"output": {"folder": "relative/dir"}})
    assert cfg.output.folder is None and warnings
    cfg, _ = config.from_dict({"output": {"location": "folder", "folder": "~/Converted"}})
    assert cfg.output.folder == Path.home() / "Converted"
    assert Config().output.directory_for(tmp_path / "a.png") == tmp_path


def test_invalid_wheel_prefs_and_unknown_keys():
    cfg, warnings = config.from_dict(
        {
            "colour": "red",
            "wheels": {"convert": {"order": "x", "speed": 1}, "extra": {}, "tools": 3},
        }
    )
    assert cfg.wheels == Config().wheels
    joined = "\n".join(warnings)
    for fragment in ("'colour'", "wheels.convert.order", "'speed'", "'extra'", "[wheels.tools]"):
        assert fragment in joined


def test_duplicate_ids_in_order_are_dropped():
    cfg, _ = config.from_dict({"wheels": {"tools": {"order": ["tool.a", "tool.b", "tool.a"]}}})
    assert cfg.wheel("tools").order == ("tool.a", "tool.b")


def test_broken_file_logs_and_uses_defaults(tmp_path, caplog):
    path = tmp_path / "config.toml"
    path.write_text("[triggers\nconvert = ")
    with caplog.at_level(logging.WARNING):
        assert config.load(path) == Config()
    assert "using default settings" in caplog.text


def test_warnings_are_logged_on_load(tmp_path, caplog):
    path = tmp_path / "config.toml"
    path.write_text('[triggers]\nactions = ["Super_L"]\n')
    with caplog.at_level(logging.WARNING):
        cfg = config.load(path)
    assert cfg.triggers == Config().triggers
    assert "Super_L cannot be used" in caplog.text


def test_xdg_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", "relative/ignored")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert paths.config_file() == tmp_path / "cfg" / "quince" / "config.toml"
    assert paths.data_dir() == Path.home() / ".local/share/quince"
    assert paths.state_dir() == Path.home() / ".local/state/quince"


# --- output suffixes and appearance (Phase 17) ------------------------------------------


def test_suffixes_and_appearance_round_trip(tmp_path):
    cfg, warnings = config.from_dict(
        {
            "output": {"suffixes": {"compressed": "small", "converted": "copy"}},
            "appearance": {"theme": "dark", "wheel_size": "large", "animations": False},
        }
    )
    assert warnings == []
    loaded = config.load(config.save(cfg, tmp_path / "config.toml"))
    assert loaded == cfg
    assert loaded.output.suffix("compressed") == "small"
    assert loaded.output.suffix("edited") == "edited"
    assert loaded.output.suffix(config.CONVERTED) == "copy"
    assert loaded.appearance == config.Appearance("dark", "large", False)
    assert loaded.appearance.wheel_scale > 1


def test_default_suffixes():
    out = config.OutputConfig()
    assert out.suffix(None) is None
    assert out.suffix(config.CONVERTED) is None  # conversions add nothing by default
    assert out.suffix("cropped") == "cropped"


def test_suffix_values_are_cleaned_and_defaults_dropped():
    cfg, warnings = config.from_dict(
        {
            "output": {
                "suffixes": {
                    "compressed": " -a/b- ",
                    "edited": "edited",  # the default: not stored
                    "merged": "",  # a tool suffix cannot be empty
                    "joined": 3,
                    "converted": "",
                }
            }
        }
    )
    assert dict(cfg.output.suffixes) == {"compressed": "a-b"}
    assert any("merged must not be empty" in w for w in warnings)
    assert any("joined must be a string" in w for w in warnings)


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        ({"appearance": {"theme": "blue"}}, "appearance.theme"),
        ({"appearance": {"wheel_size": "huge"}}, "appearance.wheel_size"),
        ({"appearance": {"animations": "yes"}}, "appearance.animations"),
        ({"appearance": {"colour": 1}}, "unknown setting appearance.'colour'"),
        ({"output": {"suffixes": []}}, "[suffixes] must be a table"),
    ],
)
def test_invalid_appearance_and_suffixes_fall_back(data, fragment):
    cfg, warnings = config.from_dict(data)
    assert any(fragment in w for w in warnings), warnings
    assert cfg.appearance == config.Appearance()
    assert dict(cfg.output.suffixes) == {}


def test_clean_suffix():
    assert config.clean_suffix("  --small.  ") == "small"
    assert config.clean_suffix("a/b") == "a-b"
    assert len(config.clean_suffix("x" * 100)) == config.MAX_SUFFIX_CHARS
