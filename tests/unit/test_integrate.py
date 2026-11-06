import configparser
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from quince import cli
from quince.integrations import install

COMMAND = ["/opt/My Venv/bin/python", "-m", "quince"]


def test_exec_arguments_are_quoted_per_the_desktop_entry_spec():
    assert install.exec_line(["/usr/bin/python3", "-m", "quince"], "home") == (
        "/usr/bin/python3 -m quince home"
    )
    assert install.exec_line(["/opt/My Venv/bin/python"]) == '"/opt/My Venv/bin/python"'
    assert install.exec_line(["/a/100%/$x"]) == '"/a/100%%/\\$x"'


def test_desktop_entry_opens_home_and_has_library_and_quit_actions():
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string(install.desktop_entry(COMMAND))
    entry = parser["Desktop Entry"]
    assert entry["Name"] == "Quince" and entry["Icon"] == "quince"
    assert entry["Exec"] == '"/opt/My Venv/bin/python" -m quince home'
    assert entry["StartupWMClass"] == "quince"
    assert parser["Desktop Action library"]["Exec"].endswith(" library")
    assert parser["Desktop Action settings"]["Exec"].endswith(" settings")
    assert entry["Actions"] == "library;settings;quit;"
    assert parser["Desktop Action quit"]["Exec"].endswith(" service --stop")


def test_install_and_uninstall_write_only_into_the_data_home(tmp_path):
    written = install.install(COMMAND, tmp_path)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in written) == [
        "applications/quince.desktop",
        "icons/hicolor/scalable/apps/quince.svg",
        "nautilus-python/extensions/quince_nautilus.py",
    ]
    ext = (tmp_path / "nautilus-python/extensions/quince_nautilus.py").read_text()
    assert f"QUINCE_COMMAND = {COMMAND!r}" in ext
    assert ext.startswith("# Installed by `quince integrate`")
    assert install.install(COMMAND, tmp_path) == written  # again: replaced in place
    assert sorted(install.uninstall(tmp_path)) == sorted(written)
    assert not any(p.exists() for p in written)
    assert install.uninstall(tmp_path) == []


def test_default_command_is_this_python():
    assert install.quince_command() == [sys.executable, "-m", "quince"]


def test_icon_is_packaged():
    assert install.icon_path().read_text().startswith("<svg")


def test_cli_integrate(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(install, "nautilus_python_available", lambda: False)
    assert cli.main(["integrate"]) == 0
    out = capsys.readouterr().out
    assert "sudo apt install python3-nautilus" in out
    assert (tmp_path / "applications/quince.desktop").exists()
    assert cli.main(["integrate", "--remove"]) == 0
    assert not (tmp_path / "applications/quince.desktop").exists()


# --- the Nautilus extension, with a stand-in for nautilus-python --------------------


class _File:
    def __init__(self, path):
        self._path = path

    def get_location(self):
        return types.SimpleNamespace(get_path=lambda: self._path)


class _MenuItem:
    def __init__(self, name, label, tip):
        self.name, self.label, self.tip = name, label, tip
        self.handlers = []

    def connect(self, signal, handler, *args):
        self.handlers.append((signal, handler, args))

    def activate(self):
        for _signal, handler, args in self.handlers:
            handler(self, *args)


def _load_extension(monkeypatch, tmp_path, api="3.0"):
    gi = types.ModuleType("gi")

    def require_version(name, version):
        if version != api:
            raise ValueError(f"Namespace {name} not available for version {version}")

    gi.require_version = require_version
    repo = types.ModuleType("gi.repository")
    repo.GObject = types.SimpleNamespace(GObject=type("GObject", (), {}))
    repo.Nautilus = types.SimpleNamespace(MenuProvider=type("MenuProvider", (), {}),
                                          MenuItem=_MenuItem)  # fmt: skip
    gi.repository = repo
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)
    [ext] = [t for t in install.targets(["/venv/python", "-m", "quince"], tmp_path)
             if t.path.name == "quince_nautilus.py"]  # fmt: skip
    ext.path.parent.mkdir(parents=True)
    ext.path.write_text(ext.content)
    spec = importlib.util.spec_from_file_location("quince_nautilus_test", ext.path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("api", ["3.0", "4.0"])
def test_nautilus_menu_runs_quince_open_with_the_selection(monkeypatch, tmp_path, api):
    module = _load_extension(monkeypatch, tmp_path, api)
    calls = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    monkeypatch.setenv("PYTHONPATH", "/opt/ros/humble/lib")
    provider = module.QuinceMenuProvider()
    files = [_File("/home/u/a b.png"), _File("/home/u/dir")]
    args = (files,) if api == "4.0" else (object(), files)
    [item] = provider.get_file_items(*args)
    assert item.label == "Open in Quince"
    item.activate()
    [(cmd, kw)] = calls
    assert cmd == ["/venv/python", "-m", "quince", "open", "--", "/home/u/a b.png", "/home/u/dir"]
    assert kw["start_new_session"] and "PYTHONPATH" not in kw["env"]
    assert provider.get_background_items(object(), object()) == []


def test_nautilus_menu_is_hidden_for_non_local_items(monkeypatch, tmp_path):
    module = _load_extension(monkeypatch, tmp_path)
    provider = module.QuinceMenuProvider()
    assert provider.get_file_items(object(), [_File("/a.png"), _File(None)]) == []
    assert provider.get_file_items(object(), []) == []


def test_extension_template_has_the_command_placeholder():
    text = install.data_file("quince_nautilus.py")
    assert 'QUINCE_COMMAND = ["quince"]' in text
    assert "import quince" not in text  # runs in Nautilus' Python, never imports Quince
    assert Path(install.__file__).with_name("data").joinpath("quince_nautilus.py").exists()
