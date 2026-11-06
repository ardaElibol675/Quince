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


def test_entry_is_skipped_when_quince_is_gone():
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string(autostart.autostart_entry(["/usr/bin/quince"]))
    assert parser["Desktop Entry"]["TryExec"] == "/usr/bin/quince"
    assert parser["Desktop Entry"]["Exec"] == "/usr/bin/quince service"


def _system_entry(folder):
    path = autostart.autostart_file(folder)
    path.parent.mkdir(parents=True)
    path.write_text(autostart.autostart_entry(["quince"]))
    return path


def test_system_entry_applies_until_the_user_turns_it_off(tmp_path):
    user, system = tmp_path / "user", tmp_path / "etc-xdg"
    dirs = [tmp_path / "missing", system]
    assert not autostart.is_enabled(user, dirs)
    _system_entry(system)
    assert autostart.system_autostart_file(dirs) == system / "autostart/quince.desktop"
    assert autostart.is_enabled(user, dirs)

    path = autostart.set_enabled(False, base=user, system_dirs=dirs)
    assert path.is_file() and "Hidden=true" in path.read_text()  # hides the system entry
    assert not autostart.is_enabled(user, dirs)

    autostart.set_enabled(True, command=["/x/python", "-m", "quince"], base=user, system_dirs=dirs)
    assert not path.exists()  # the system entry applies again
    assert autostart.is_enabled(user, dirs)


def test_system_entry_turned_off_by_the_admin(tmp_path):
    user, system = tmp_path / "user", tmp_path / "etc-xdg"
    entry = _system_entry(system)
    entry.write_text(entry.read_text().replace("enabled=true", "enabled=false"))
    assert not autostart.is_enabled(user, [system])
    autostart.set_enabled(True, command=["quince"], base=user, system_dirs=[system])
    assert autostart.autostart_file(user).is_file()  # the user's own entry
    assert autostart.is_enabled(user, [system])


def test_system_folders_follow_xdg_config_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_DIRS", f"{tmp_path}/a:relative:{tmp_path}/b")
    assert autostart.config_dirs() == [tmp_path / "a", tmp_path / "b"]
    monkeypatch.setenv("XDG_CONFIG_DIRS", "")
    assert autostart.config_dirs() == [autostart.Path("/etc/xdg")]


def test_foreign_user_entry(tmp_path):
    packaged = ["/usr/bin/quince"]
    assert autostart.foreign_user_entry(packaged, tmp_path) is None  # no entry
    autostart.set_enabled(True, command=COMMAND, base=tmp_path, system_dirs=[])
    assert autostart.foreign_user_entry(packaged, tmp_path) == autostart.autostart_file(tmp_path)
    assert autostart.foreign_user_entry(COMMAND, tmp_path) is None  # starts this Quince
    autostart.autostart_file(tmp_path).write_text(autostart.DISABLED_ENTRY)
    assert autostart.foreign_user_entry(packaged, tmp_path) is None  # off
