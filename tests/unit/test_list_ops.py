from pathlib import Path

import pytest

from quince import cli
from quince.core import config, formats
from quince.core.deps import DEPENDENCIES, Availability, DependencyStatus
from quince.core.registry import discover
from quince.core.selection import Item, Selection


def _sel(*names):
    items = []
    for name in names:
        if name.endswith("/"):
            items.append(Item(Path(name), is_dir=True))
        else:
            items.append(Item(Path(name), format=formats.from_filename(name)))
    return Selection(tuple(items))


def _report(sel, available=None, cfg=None):
    return cli.render_ops(
        sel, discover(), available or Availability.everything(), cfg or config.Config()
    )


def test_png_report():
    report = _report(_sel("/x/photo.png"))
    assert "1 file, image (PNG)" in report
    assert "Convert (" in report and "convert.image.jpg" in report
    assert "action.copy_path" in report


def test_folder_and_mixed_reports_show_only_actions():
    for sel in (_sel("/x/folder/"), _sel("/x/a.png", "/x/b.pdf")):
        report = _report(sel)
        assert "(only file actions for folders and mixed selections)" in report
        assert "convert." not in report
        assert "action.copy_path" in report


def test_unknown_type_report():
    assert "(unknown file type)" in _report(_sel("/x/blob.xyz"))


def test_hidden_and_disabled_are_explained():
    no_rsvg = Availability(DependencyStatus(d, d.id != "rsvg-convert") for d in DEPENDENCIES)
    report = _report(_sel("/x/logo.svg"), available=no_rsvg)
    assert "Hidden, missing dependencies" in report
    assert "rsvg-convert (sudo apt install librsvg2-bin)" in report

    cfg, _ = config.from_dict({"wheels": {"convert": {"disabled": ["convert.image.webp"]}}})
    report = _report(_sel("/x/photo.png"), cfg=cfg)
    assert "Disabled in config" in report
    assert report.count("convert.image.webp") == 1


def test_list_ops_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert cli.main(["list-ops", str(img), str(tmp_path)]) == 0
    assert "1 file, 1 folder" in capsys.readouterr().out
    assert cli.main(["list-ops", str(tmp_path / "missing.png")]) == 2
    assert "no such file" in capsys.readouterr().err


def test_config_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cli.main(["config", "--path"]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "quince" / "config.toml")
    assert cli.main(["config"]) == 0
    out = capsys.readouterr().out
    assert "defaults apply" in out and "max_tap_ms = 400" in out


@pytest.mark.parametrize("missing", [(), ("rsvg-convert",)])
def test_doctor_lists_hidden_operations(missing):
    statuses = [DependencyStatus(d, d.id not in missing) for d in DEPENDENCIES]
    report = cli.render_doctor(statuses, discover())
    assert "Operations:" in report
    assert ("SVG → PNG" in report) == bool(missing)
