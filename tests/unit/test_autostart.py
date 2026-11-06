import configparser

from quince.integrations import autostart

COMMAND = ["/opt/My Venv/bin/python", "-m", "quince"]


def test_entry_starts_the_service():
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string(autostart.autostart_entry(COMMAND))
    entry = parser["Desktop Entry"]
    assert entry["Exec"] == '"/opt/My Venv/bin/python" -m quince service'
    assert entry["Type"] == "Application"
    assert entry["X-GNOME-Autostart-enabled"] == "true"


def test_enable_and_disable(tmp_path):
    path = autostart.autostart_file(tmp_path)
    assert path == tmp_path / "autostart" / "quince.desktop"
    assert not autostart.is_enabled(tmp_path)
    autostart.set_enabled(True, command=COMMAND, base=tmp_path)
    assert path.is_file() and autostart.is_enabled(tmp_path)
    assert oct(path.stat().st_mode & 0o777) == "0o644"
    autostart.set_enabled(False, base=tmp_path)
    assert not path.exists() and not autostart.is_enabled(tmp_path)
    autostart.set_enabled(False, base=tmp_path)  # already off: nothing to do


def test_entry_turned_off_elsewhere_counts_as_off(tmp_path):
    path = autostart.autostart_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("[Desktop Entry]\nType=Application\nX-GNOME-Autostart-enabled=false\n")
    assert not autostart.is_enabled(tmp_path)
    path.write_text("[Desktop Entry]\nType=Application\nHidden=true\n")
    assert not autostart.is_enabled(tmp_path)


def test_default_folder_follows_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert autostart.autostart_file() == tmp_path / "autostart" / "quince.desktop"
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")  # invalid per the XDG spec
    assert autostart.autostart_file().parent.parent.name == ".config"
