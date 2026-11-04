import os
import sys
from pathlib import Path

import pytest

from quince.backends import desktop
from quince.backends.desktop import (
    AppDatabase,
    Associations,
    DesktopApp,
    MimeDatabase,
    Terminal,
    expand_exec,
    split_exec,
)
from quince.core import formats
from quince.core.jobs import JobError

# --- desktop entry values -------------------------------------------------------


def test_unescape_and_lists():
    assert desktop.unescape(r"a\sb\tc\\d") == "a b\tc\\d"
    assert desktop.split_list("image/png;image/jpeg;") == ["image/png", "image/jpeg"]
    assert desktop.split_list(r"a\;b;c") == ["a;b", "c"]
    assert desktop.split_list("") == []


def test_locale_variants():
    assert desktop.locale_variants({"LANG": "tr_TR.UTF-8"}) == ["tr_TR", "tr"]
    assert desktop.locale_variants({"LANG": "sr_RS@latin"}) == [
        "sr_RS@latin",
        "sr_RS",
        "sr@latin",
        "sr",
    ]
    assert desktop.locale_variants({"LC_ALL": "C.UTF-8", "LANG": "tr_TR"}) == []
    assert desktop.locale_variants({}) == []


ENTRY = """\
# comment
[Desktop Entry]
Type=Application
Name=Text Editor
Name[tr]=Metin Düzenleyici
Exec=gedit %U
Icon=org.gnome.gedit
MimeType=text/plain;text/markdown;
Terminal=false
Path=/srv

[Desktop Action new-window]
Name=New Window
Exec=gedit --new-window
"""


def test_parse_desktop_entry():
    app = desktop.parse_desktop_entry(ENTRY, "gedit.desktop", Path("/a/gedit.desktop"), ["tr"])
    assert app.name == "Metin Düzenleyici"
    assert app.exec == "gedit %U"  # not overwritten by the action group
    assert app.mime_types == ("text/plain", "text/markdown")
    assert app.working_dir == Path("/srv")
    assert not app.terminal
    english = desktop.parse_desktop_entry(ENTRY, "gedit.desktop", Path("/a"), [])
    assert english.name == "Text Editor"


def test_non_applications_are_ignored():
    assert desktop.parse_desktop_entry("[Desktop Entry]\nType=Link\n", "x", Path("x")) is None
    assert desktop.parse_desktop_entry("[Other]\nType=Application\n", "x", Path("x")) is None


def _app(app_id="a.desktop", **kw):
    return DesktopApp(id=app_id, path=Path(f"/apps/{app_id}"), name=kw.pop("name", app_id), **kw)


def test_should_show():
    which = {"present": "/usr/bin/present"}.get
    assert _app(exec="x").should_show(["GNOME"], which)
    assert not _app(exec="").should_show([], which)
    assert not _app(exec="x", no_display=True).should_show([], which)
    assert not _app(exec="x", hidden=True).should_show([], which)
    assert not _app(exec="x", only_show_in=("KDE",)).should_show(["ubuntu", "GNOME"], which)
    assert _app(exec="x", only_show_in=("gnome",)).should_show(["ubuntu", "GNOME"], which)
    assert not _app(exec="x", not_show_in=("GNOME",)).should_show(["GNOME"], which)
    assert not _app(exec="x", try_exec="missing").should_show([], which)
    assert _app(exec="x", try_exec="present").should_show([], which)


# --- a fake XDG setup ----------------------------------------------------------------


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def entry(name, exec_, mime="", **extra):
    lines = ["[Desktop Entry]", "Type=Application", f"Name={name}", f"Exec={exec_}"]
    if mime:
        lines.append(f"MimeType={mime}")
    lines += [f"{k}={v}" for k, v in extra.items()]
    return "\n".join(lines) + "\n"


@pytest.fixture
def xdg(tmp_path):
    home, system, config = tmp_path / "home", tmp_path / "system", tmp_path / "config"
    apps = system / "applications"
    write(apps / "viewer.desktop", entry("Image Viewer", "viewer %F", "image/png;"))
    write(apps / "gimp.desktop", entry("GIMP", "gimp %U", "image/png;image/jpeg;"))
    write(apps / "editor.desktop", entry("Editor", "editor %F", "text/plain;"))
    write(apps / "md.desktop", entry("Markdown App", "md %f", "text/markdown;"))
    write(apps / "files.desktop", entry("Files", "files %U", "inode/directory;"))
    write(apps / "hidden.desktop", entry("Helper", "helper %f", "image/png;", NoDisplay="true"))
    write(apps / "kde" / "paint.desktop", entry("KPaint", "kpaint %u", "image/png;"))
    write(apps / "term.desktop", entry("Top", "top", "", Terminal="true"))
    # A user entry with the same id wins over the system one.
    write(home / "applications" / "editor.desktop", entry("My Editor", "myedit %F", "text/plain;"))
    write(apps / "gnome-mimeapps.list", "[Default Applications]\nimage/png=gimp.desktop\n")
    write(
        config / "mimeapps.list",
        "[Default Applications]\n"
        "image/png=missing.desktop;viewer.desktop;\n"
        "[Added Associations]\n"
        "image/png=kde-paint.desktop;\n"
        "text/markdown=editor.desktop;\n"
        "[Removed Associations]\n"
        "image/png=gimp.desktop;\n",
    )
    mime = system / "mime"
    write(
        mime / "globs2",
        "# generated\n"
        "50:image/png:*.png\n"
        "50:text/markdown:*.md\n"
        "50:application/x-compressed-tar:*.tar.gz\n"
        "50:application/gzip:*.gz\n"
        "50:text/x-makefile:Makefile\n"
        "50:text/x-readme:README*\n"
        "50:text/x-c:*.C:cs\n"
        "50:text/x-c++src:*.c\n",
    )
    write(mime / "aliases", "image/x-png image/png\n")
    write(
        mime / "subclasses",
        "text/markdown text/plain\napplication/x-compressed-tar application/gzip\n",
    )
    return {
        "XDG_DATA_HOME": str(home),
        "XDG_DATA_DIRS": str(system),
        "XDG_CONFIG_HOME": str(config),
        "XDG_CONFIG_DIRS": str(tmp_path / "none"),
        "XDG_CURRENT_DESKTOP": "ubuntu:GNOME",
        "LANG": "C",
    }


def test_scan_ids_and_precedence(xdg):
    apps = desktop.scan_applications(desktop.data_dirs(xdg))
    assert "kde-paint.desktop" in apps  # subfolder becomes a prefix
    assert apps["editor.desktop"].name == "My Editor"  # the user's file wins


def test_mime_database(xdg):
    db = MimeDatabase.load(desktop.data_dirs(xdg))
    assert db.from_name("photo.PNG") == "image/png"
    assert db.from_name("backup.tar.gz") == "application/x-compressed-tar"  # longest wins
    assert db.from_name("x.gz") == "application/gzip"
    assert db.from_name("Makefile") == "text/x-makefile"
    assert db.from_name("README.first") == "text/x-readme"
    assert db.from_name("main.C") == "text/x-c"  # case-sensitive glob
    assert db.from_name("main.c") == "text/x-c++src"
    assert db.from_name("unknown.zzz") is None
    assert db.canonical("image/x-png") == "image/png"
    assert db.ancestors("text/markdown") == ["text/plain"]
    assert db.ancestors("text/x-makefile") == ["text/plain"]
    assert db.ancestors("application/x-compressed-tar") == ["application/gzip"]
    assert db.ancestors("text/plain") == []


def test_mime_type_of_paths(xdg, tmp_path):
    db = MimeDatabase.load(desktop.data_dirs(xdg))
    folder = tmp_path / "f"
    folder.mkdir()
    assert desktop.mime_type(folder, None, db) == "inode/directory"
    # Quince's own detection (content checked) comes first.
    assert desktop.mime_type(tmp_path / "x.md", formats.get("png"), db) == "image/png"
    assert desktop.mime_type(tmp_path / "x.md", None, db) == "text/markdown"
    text = write(tmp_path / "notes", "plain words\n")
    binary = tmp_path / "blob"
    binary.write_bytes(b"\0\1\2\3")
    assert desktop.mime_type(text, None, db) == "text/plain"
    assert desktop.mime_type(binary, None, db) == "application/octet-stream"


def test_defaults_and_associations(xdg):
    db = AppDatabase.load(xdg)
    names = [a.name for a in db.for_type("image/png")]
    # Default first (the missing one is skipped), then added, then declared by name.
    # GIMP is removed for PNG; the NoDisplay helper is never listed.
    assert names == ["Image Viewer", "KPaint"]
    assert db.default_for("image/png").id == "viewer.desktop"
    assert [a.name for a in db.for_type("image/x-png")] == names  # aliases resolve
    # No explicit default: the first added association is the default, then parents' apps.
    assert db.default_for("text/markdown").id == "editor.desktop"
    assert [a.name for a in db.for_type("text/markdown")] == ["My Editor", "Markdown App"]
    assert [a.name for a in db.for_type("inode/directory")] == ["Files"]
    assert db.for_type("application/x-unknown") == []


def test_apps_for_several_types(xdg):
    db = AppDatabase.load(xdg)
    assert [a.name for a in db.for_types(["image/png", "image/jpeg"])] == []  # GIMP removed
    assert [a.name for a in db.for_types(["text/markdown", "text/plain"])] == ["My Editor"]
    assert db.for_types([]) == []
    shown = [a.name for a in db.shown()]
    assert "Helper" not in shown and shown == sorted(shown, key=str.casefold)


def test_legacy_defaults_list_has_lowest_precedence(tmp_path):
    assoc = Associations()
    assoc.read("[Default Applications]\nimage/png=a.desktop\n")
    assoc.read(
        "[Default Applications]\nimage/png=b.desktop\n[Added Associations]\n", defaults_only=True
    )
    assert assoc.defaults["image/png"] == ["a.desktop", "b.desktop"]


def test_association_files_order(xdg):
    files = [str(p) for p, _legacy in desktop.association_files(xdg)]
    assert files[0].endswith("config/ubuntu-mimeapps.list")
    assert files[1].endswith("config/gnome-mimeapps.list")
    assert files[2].endswith("config/mimeapps.list")
    assert files[-1].endswith("system/applications/defaults.list")


# --- Exec lines --------------------------------------------------------------------


def test_split_exec():
    assert split_exec("gimp-2.10 %U") == ["gimp-2.10", "%U"]
    assert split_exec('sh -c "echo \\"hi there\\" \\$HOME"') == [
        "sh",
        "-c",
        'echo "hi there" $HOME',
    ]
    assert split_exec('app ""  x') == ["app", "", "x"]
    with pytest.raises(ValueError):
        split_exec('app "open')


def test_expand_exec_field_codes(tmp_path):
    a, b = tmp_path / "a b.png", tmp_path / "ç.png"
    many = _app(exec="viewer --new %F", name="Viewer")
    assert expand_exec(many, [a, b]) == [["viewer", "--new", str(a), str(b)]]
    one = _app(exec="paint %f", name="Paint")
    assert expand_exec(one, [a, b]) == [["paint", str(a)], ["paint", str(b)]]
    uris = _app(exec="browser %U")
    assert expand_exec(uris, [a]) == [["browser", a.as_uri()]]
    assert "%20" in a.as_uri()
    assert expand_exec(_app(exec="b --file=%u"), [b]) == [["b", f"--file={b.as_uri()}"]]
    misc = _app("m.desktop", exec="m %i %c %k 100%% %d", name="My App", icon="m-icon")
    assert expand_exec(misc, []) == [["m", "--icon", "m-icon", "My App", "/apps/m.desktop", "100%"]]
    assert expand_exec(_app(exec="m %i"), []) == [["m"]]  # no icon, no --icon
    assert expand_exec(one, []) == [["paint"]]  # the file code is dropped without files
    assert expand_exec(_app(exec="top"), [a]) == [["top"]]  # no file code: files ignored


def test_desktop_env_drops_quince_python(monkeypatch):
    venv_bin = os.path.join(sys.prefix, "bin")
    base = {
        "PATH": os.pathsep.join([venv_bin, "/usr/bin"]),
        "PYTHONPATH": "/opt/ros",
        "VIRTUAL_ENV": sys.prefix,
        "PYSIDE6_OPTION_PYTHON_ENUM": "True",
        "HOME": "/home/u",
    }
    env = desktop.desktop_env(base)
    assert env["HOME"] == "/home/u"
    assert not {"PYTHONPATH", "VIRTUAL_ENV", "PYSIDE6_OPTION_PYTHON_ENUM"} & env.keys()
    if sys.prefix != sys.base_prefix:
        assert env["PATH"] == "/usr/bin"


def test_spawn_errors_are_explained(tmp_path):
    with pytest.raises(JobError, match="no-such-program-quince is not installed"):
        desktop.spawn(["no-such-program-quince"])


def test_spawn_starts_detached(tmp_path):
    marker = tmp_path / "ran"
    desktop.spawn(["sh", "-c", f'pwd > "{marker}"'], cwd=tmp_path)
    for _ in range(100):
        if marker.exists() and marker.read_text():
            break
        import time

        time.sleep(0.02)
    assert marker.read_text().strip() == str(tmp_path)


def test_launch(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(desktop, "spawn", lambda argv, cwd=None: started.append((argv, cwd)))
    files = [tmp_path / "a.txt"]
    desktop.launch(_app(exec="edit %f", working_dir=Path("/srv")), files)
    assert started == [(["edit", str(files[0])], Path("/srv"))]
    started.clear()
    term = Terminal("/usr/bin/x-terminal-emulator", "terminator")
    desktop.launch(_app(exec="vim %f", terminal=True), files, term)
    assert started[0][0] == ["/usr/bin/x-terminal-emulator", "-x", "vim", str(files[0])]
    with pytest.raises(JobError, match="needs a terminal"):
        desktop.launch(_app(exec="vim %f", terminal=True, name="Vim"), files, None)
    with pytest.raises(JobError, match="cannot be started"):
        desktop.launch(_app(exec='bad "quote', name="Bad"), files)


# --- terminals --------------------------------------------------------------------------


@pytest.fixture
def bin_dir(tmp_path):
    folder = tmp_path / "bin"
    folder.mkdir()
    return folder


def _exe(folder: Path, name: str, target: str | None = None) -> Path:
    path = folder / name
    if target:
        os.symlink(target, path)
    else:
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    return path


def _which(folder: Path):
    return lambda name: str(folder / name) if os.path.lexists(folder / name) else None


def test_terminal_from_alternatives(bin_dir):
    real = _exe(bin_dir, "terminator")
    _exe(bin_dir, "x-terminal-emulator", str(real))
    term = desktop.find_terminal(_which(bin_dir), configured=lambda: "x-terminal-emulator")
    assert term == Terminal(str(bin_dir / "x-terminal-emulator"), "terminator")
    assert term.open_args(Path("/w d")) == [term.command, "--working-directory=/w d"]
    assert term.wrap(["htop"]) == [term.command, "-x", "htop"]


def test_terminal_wrapper_resolves_to_gnome_terminal(bin_dir):
    wrapper = _exe(bin_dir, "gnome-terminal.wrapper")
    _exe(bin_dir, "x-terminal-emulator", str(wrapper))
    _exe(bin_dir, "gnome-terminal")
    term = desktop.find_terminal(_which(bin_dir), configured=lambda: None)
    assert term == Terminal(str(bin_dir / "gnome-terminal"), "gnome-terminal")
    assert term.wrap(["htop"]) == [term.command, "--", "htop"]


def test_terminal_fallbacks(bin_dir):
    assert desktop.find_terminal(_which(bin_dir), configured=lambda: None) is None
    _exe(bin_dir, "konsole")
    term = desktop.find_terminal(_which(bin_dir), configured=lambda: "missing-term")
    assert term.name == "konsole"
    assert term.open_args(Path("/w")) == [term.command, "--workdir", "/w"]
    unknown = Terminal("/usr/bin/st", "st")
    assert unknown.open_args(Path("/w")) == ["/usr/bin/st"]
    assert unknown.wrap(["top"]) == ["/usr/bin/st", "-e", "top"]


def test_terminal_open_in_uses_the_folder(monkeypatch):
    started = []
    monkeypatch.setattr(desktop, "spawn", lambda argv, cwd=None: started.append((argv, cwd)))
    Terminal("/usr/bin/xterm", "xterm").open_in(Path("/data"))
    assert started == [(["/usr/bin/xterm"], Path("/data"))]
