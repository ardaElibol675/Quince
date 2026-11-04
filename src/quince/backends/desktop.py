"""Desktop integration without GTK: applications, MIME types, launching programs, terminals.

Follows the freedesktop.org specifications:

- *Desktop Entry*: ``applications/*.desktop`` in the XDG data dirs; the first data dir wins
  for a desktop file id. ``Exec`` field codes (``%f %F %u %U %i %c %k``) are expanded here.
- *MIME Applications Associations*: ``mimeapps.list`` (and ``<desktop>-mimeapps.list``) in
  the config and data dirs, plus the legacy ``defaults.list``; defaults, added and removed
  associations.
- *Shared MIME-info*: ``mime/globs2``, ``mime/aliases`` and ``mime/subclasses``, so a file
  gets the same type as in Nautilus and apps for parent types (``text/plain`` for Markdown)
  are offered too.

Programs are started detached, in their own session, with an environment cleaned of
Quince's own Python settings (``PYTHONPATH``, the virtualenv), so they behave exactly as when
started from the desktop.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from quince.backends.process import clean_env
from quince.core.formats import Format
from quince.core.jobs import JobError

log = logging.getLogger(__name__)

DIRECTORY_TYPE = "inode/directory"
UNKNOWN_TYPE = "application/octet-stream"
GSETTINGS_TIMEOUT_S = 2.0
SNIFF_BYTES = 512

# --- XDG directories --------------------------------------------------------


def _env_dirs(env: Mapping[str, str], home_var: str, home: str, dirs_var: str, dirs: str):
    value = env.get(home_var, "")
    first = Path(value) if os.path.isabs(value) else Path.home() / home
    rest = [Path(d) for d in (env.get(dirs_var) or dirs).split(":") if os.path.isabs(d)]
    return list(dict.fromkeys([first, *rest]))


def data_dirs(env: Mapping[str, str] = os.environ) -> list[Path]:
    return _env_dirs(
        env, "XDG_DATA_HOME", ".local/share", "XDG_DATA_DIRS", "/usr/local/share:/usr/share"
    )


def config_dirs(env: Mapping[str, str] = os.environ) -> list[Path]:
    return _env_dirs(env, "XDG_CONFIG_HOME", ".config", "XDG_CONFIG_DIRS", "/etc/xdg")


def current_desktops(env: Mapping[str, str] = os.environ) -> list[str]:
    """``XDG_CURRENT_DESKTOP`` entries, e.g. ``["ubuntu", "GNOME"]``."""
    return [d for d in env.get("XDG_CURRENT_DESKTOP", "").split(":") if d]


def locale_variants(env: Mapping[str, str] = os.environ) -> list[str]:
    """Locale keys to try for localized values, most specific first.

    ``tr_TR.UTF-8@euro`` → ``tr_TR@euro``, ``tr_TR``, ``tr@euro``, ``tr``.
    """
    value = env.get("LC_ALL") or env.get("LC_MESSAGES") or env.get("LANG") or ""
    if value in ("", "C", "POSIX") or value.startswith("C."):
        return []
    value, _, modifier = value.partition("@")
    value = value.split(".")[0]
    lang, _, country = value.partition("_")
    mod = f"@{modifier}" if modifier else ""
    variants = []
    if country:
        variants += [f"{lang}_{country}{mod}", f"{lang}_{country}"]
    variants += [f"{lang}{mod}", lang]
    return list(dict.fromkeys(v for v in variants if v))


# --- desktop entry files ----------------------------------------------------

_ESCAPES = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\", ";": "\\;"}


def unescape(value: str) -> str:
    """Desktop entry string escapes. ``\\;`` is kept for ``split_list``."""
    out = []
    i = 0
    while i < len(value):
        c = value[i]
        if c == "\\" and i + 1 < len(value):
            out.append(_ESCAPES.get(value[i + 1], value[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def split_list(value: str) -> list[str]:
    """A ``;``-separated list value; ``\\;`` is a literal semicolon."""
    items, current = [], []
    i = 0
    while i < len(value):
        if value.startswith("\\;", i):
            current.append(";")
            i += 2
        elif value[i] == ";":
            items.append("".join(current))
            current = []
            i += 1
        else:
            current.append(value[i])
            i += 1
    if current:
        items.append("".join(current))
    return [item.strip() for item in items if item.strip()]


def parse_groups(text: str) -> dict[str, dict[str, str]]:
    """Groups of an INI-style file (desktop entries, mimeapps.list); raw values."""
    groups: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = groups.setdefault(line[1:-1], {})
        elif current is not None and "=" in line:
            key, _, value = line.partition("=")
            current.setdefault(key.strip(), value.strip())
    return groups


@dataclass(frozen=True)
class DesktopApp:
    id: str  # desktop file id, e.g. "org.gnome.gedit.desktop"
    path: Path
    name: str
    exec: str = ""
    icon: str = ""
    mime_types: tuple[str, ...] = ()
    terminal: bool = False
    working_dir: Path | None = None
    no_display: bool = False
    hidden: bool = False
    only_show_in: tuple[str, ...] = ()
    not_show_in: tuple[str, ...] = ()
    try_exec: str = ""

    def should_show(
        self, desktops: Sequence[str], which: Callable[[str], str | None] = shutil.which
    ) -> bool:
        """Whether menus and choosers list this app (like ``g_app_info_should_show``)."""
        if self.hidden or self.no_display or not self.exec:
            return False
        wanted = {d.casefold() for d in desktops}
        if self.only_show_in and not wanted & {d.casefold() for d in self.only_show_in}:
            return False
        if wanted & {d.casefold() for d in self.not_show_in}:
            return False
        return not self.try_exec or which(self.try_exec) is not None


def _bool(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def parse_desktop_entry(
    text: str, app_id: str, path: Path, locales: Sequence[str] = ()
) -> DesktopApp | None:
    """The application described by a ``.desktop`` file, or None if it is not one."""
    entry = parse_groups(text).get("Desktop Entry")
    if entry is None or entry.get("Type", "").strip() != "Application":
        return None

    def localized(key: str) -> str:
        for loc in locales:
            if f"{key}[{loc}]" in entry:
                return unescape(entry[f"{key}[{loc}]"])
        return unescape(entry.get(key, ""))

    name = localized("Name") or app_id.removesuffix(".desktop")
    workdir = unescape(entry.get("Path", ""))
    return DesktopApp(
        id=app_id,
        path=path,
        name=name,
        exec=unescape(entry.get("Exec", "")),
        icon=localized("Icon"),
        mime_types=tuple(split_list(unescape(entry.get("MimeType", "")))),
        terminal=_bool(entry.get("Terminal")),
        working_dir=Path(workdir) if os.path.isabs(workdir) else None,
        no_display=_bool(entry.get("NoDisplay")),
        hidden=_bool(entry.get("Hidden")),
        only_show_in=tuple(split_list(entry.get("OnlyShowIn", ""))),
        not_show_in=tuple(split_list(entry.get("NotShowIn", ""))),
        try_exec=unescape(entry.get("TryExec", "")),
    )


def scan_applications(dirs: Iterable[Path], locales: Sequence[str] = ()) -> dict[str, DesktopApp]:
    """Every desktop file under ``<dir>/applications``; the first dir wins for an id.

    The desktop file id is the path below ``applications/`` with ``/`` replaced by ``-``.
    A hidden entry still claims its id, which hides the same id in later dirs.
    """
    apps: dict[str, DesktopApp | None] = {}
    for base in dirs:
        root = base / "applications"
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.desktop")):
            app_id = str(path.relative_to(root)).replace("/", "-")
            if app_id in apps:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                log.debug("cannot read %s: %s", path, exc)
                continue
            apps[app_id] = parse_desktop_entry(text, app_id, path, locales)
    return {k: v for k, v in apps.items() if v is not None}


# --- shared MIME-info -------------------------------------------------------


@dataclass
class MimeDatabase:
    aliases: dict[str, str] = field(default_factory=dict)
    parents: dict[str, list[str]] = field(default_factory=dict)
    # Simple "*.ext" globs (lower case unless case sensitive) and literal names.
    extensions: dict[str, tuple[int, str]] = field(default_factory=dict)
    literals: dict[str, tuple[int, str]] = field(default_factory=dict)
    patterns: list[tuple[int, str, str]] = field(default_factory=list)  # weight, mime, glob

    @classmethod
    def load(cls, dirs: Iterable[Path]) -> MimeDatabase:
        db = cls()
        # The first dir wins, so read the others first and let earlier ones overwrite.
        for base in reversed(list(dirs)):
            mime = base / "mime"
            db._read_pairs(mime / "aliases", lambda a, b: db.aliases.__setitem__(a, b))
            db._read_pairs(mime / "subclasses", db._add_parent)
            db._read_globs(mime / "globs2")
        return db

    @staticmethod
    def _lines(path: Path) -> list[str]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return [ln for ln in text.splitlines() if ln and not ln.startswith("#")]

    def _read_pairs(self, path: Path, add: Callable[[str, str], None]) -> None:
        for line in self._lines(path):
            parts = line.split()
            if len(parts) == 2:
                add(parts[0], parts[1])

    def _add_parent(self, mime: str, parent: str) -> None:
        parents = self.parents.setdefault(mime, [])
        if parent not in parents:
            parents.append(parent)

    def _read_globs(self, path: Path) -> None:
        for line in self._lines(path):
            parts = line.split(":")
            if len(parts) < 3 or not parts[0].isdigit():
                continue
            weight, mime, glob = int(parts[0]), parts[1], parts[2]
            case_sensitive = len(parts) > 3 and "cs" in parts[3].split(",")
            key = glob if case_sensitive else glob.lower()
            simple_ext = key.startswith("*.") and not any(c in key[2:] for c in "*?[")
            if simple_ext:
                self._keep(self.extensions, key[2:], weight, mime)
            elif not any(c in key for c in "*?["):
                self._keep(self.literals, key, weight, mime)
            else:
                self.patterns.append((weight, mime, glob))

    @staticmethod
    def _keep(table: dict[str, tuple[int, str]], key: str, weight: int, mime: str) -> None:
        if key not in table or weight > table[key][0]:
            table[key] = (weight, mime)

    def canonical(self, mime: str) -> str:
        return self.aliases.get(mime, mime)

    def from_name(self, name: str) -> str | None:
        """The MIME type for a file name by its glob patterns, or None."""
        lower = name.lower()
        for literal in (name, lower):
            if literal in self.literals:
                return self.literals[literal][1]
        best: tuple[int, int, str] | None = None  # weight, pattern length, mime
        for i, ch in enumerate(name):
            if ch == ".":
                for ext in (name[i + 1 :], lower[i + 1 :]):
                    if ext in self.extensions:
                        weight, mime = self.extensions[ext]
                        candidate = (weight, len(ext), mime)
                        if best is None or candidate[:2] > best[:2]:
                            best = candidate
        for weight, mime, glob in self.patterns:
            if fnmatch.fnmatchcase(lower, glob.lower()):
                candidate = (weight, len(glob), mime)
                if best is None or candidate[:2] > best[:2]:
                    best = candidate
        return best[2] if best else None

    def ancestors(self, mime: str) -> list[str]:
        """Parent types, nearest first. ``text/*`` types are ``text/plain`` too."""
        result: list[str] = []
        queue = list(self.parents.get(self.canonical(mime), []))
        if mime.startswith("text/") and mime != "text/plain":
            queue.append("text/plain")
        while queue:
            parent = self.canonical(queue.pop(0))
            if parent != mime and parent not in result:
                result.append(parent)
                queue.extend(self.parents.get(parent, []))
        return result


def _looks_like_text(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            head = f.read(SNIFF_BYTES)
    except OSError:
        return False
    if b"\0" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        return exc.start >= len(head) - 3  # a character cut off at the end
    return True


def mime_type(path: Path, fmt: Format | None, db: MimeDatabase) -> str:
    """The MIME type of a file or folder, as a file manager would see it.

    Quince's own detection (``fmt``, checked against the content) comes first, then the
    shared-mime-info globs, then a quick text check.
    """
    path = Path(path)
    if path.is_dir():
        return DIRECTORY_TYPE
    if fmt is not None and fmt.mime_types:
        return db.canonical(fmt.mime_types[0])
    if by_name := db.from_name(path.name):
        return db.canonical(by_name)
    return "text/plain" if _looks_like_text(path) else UNKNOWN_TYPE


# --- which applications open a type -----------------------------------------


@dataclass
class Associations:
    defaults: dict[str, list[str]] = field(default_factory=dict)
    added: dict[str, list[str]] = field(default_factory=dict)
    removed: dict[str, set[str]] = field(default_factory=dict)

    def read(self, text: str, *, defaults_only: bool = False) -> None:
        """Merge one file; files must be read from the highest precedence down."""
        groups = parse_groups(text)
        for mime, value in groups.get("Default Applications", {}).items():
            self.defaults.setdefault(mime, []).extend(split_list(value))
        if defaults_only:
            return
        for mime, value in groups.get("Added Associations", {}).items():
            removed = self.removed.get(mime, set())
            known = self.added.setdefault(mime, [])
            known.extend(a for a in split_list(value) if a not in removed and a not in known)
        for mime, value in groups.get("Removed Associations", {}).items():
            self.removed.setdefault(mime, set()).update(split_list(value))


def association_files(env: Mapping[str, str] = os.environ) -> list[tuple[Path, bool]]:
    """``mimeapps.list`` files from the highest precedence down; True marks a legacy
    ``defaults.list`` (defaults only)."""
    desktops = [d.lower() for d in current_desktops(env)]
    names = [f"{d}-mimeapps.list" for d in desktops] + ["mimeapps.list"]
    files: list[tuple[Path, bool]] = []
    for base in config_dirs(env):
        files += [(base / n, False) for n in names]
    for base in data_dirs(env):
        files += [(base / "applications" / n, False) for n in names]
        files.append((base / "applications" / "defaults.list", True))
    return files


class AppDatabase:
    """Installed applications and which ones open which MIME types."""

    def __init__(
        self,
        apps: Mapping[str, DesktopApp],
        associations: Associations,
        mime: MimeDatabase,
        desktops: Sequence[str] = (),
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.apps = dict(apps)
        self.assoc = associations
        self.mime = mime
        self.desktops = list(desktops)
        self._which = which
        self._shown = {i: a for i, a in self.apps.items() if a.should_show(desktops, which)}
        self._declared: dict[str, list[str]] = {}
        for app in sorted(self._shown.values(), key=lambda a: a.name.casefold()):
            for mt in app.mime_types:
                self._declared.setdefault(mime.canonical(mt), []).append(app.id)

    @classmethod
    def load(cls, env: Mapping[str, str] = os.environ) -> AppDatabase:
        dirs = data_dirs(env)
        apps = scan_applications(dirs, locale_variants(env))
        assoc = Associations()
        for path, legacy in association_files(env):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            assoc.read(text, defaults_only=legacy)
        return cls(apps, assoc, MimeDatabase.load(dirs), current_desktops(env))

    def default_for(self, mime: str) -> DesktopApp | None:
        """The default app, like GIO: for the type, then each parent type, an explicit
        default, else the first added association (the app chosen last in a file manager)."""
        for mt in (self.mime.canonical(mime), *self.mime.ancestors(mime)):
            removed = self.assoc.removed.get(mt, set())
            for app_id in [*self.assoc.defaults.get(mt, []), *self.assoc.added.get(mt, [])]:
                if app_id in self._shown and app_id not in removed:
                    return self._shown[app_id]
        return None

    def for_type(self, mime: str) -> list[DesktopApp]:
        """Apps for ``mime``: the default first, then added associations, then apps that
        declare the type, then apps for its parent types."""
        ids: list[str] = []
        default = self.default_for(mime)
        if default is not None:
            ids.append(default.id)
        for mt in (self.mime.canonical(mime), *self.mime.ancestors(mime)):
            removed = self.assoc.removed.get(mt, set())
            for app_id in [*self.assoc.added.get(mt, []), *self._declared.get(mt, [])]:
                if app_id not in removed and app_id not in ids:
                    ids.append(app_id)
        return [self._shown[i] for i in ids if i in self._shown]

    def for_types(self, mimes: Iterable[str]) -> list[DesktopApp]:
        """Apps that open every one of ``mimes``, in the order of the first type."""
        lists = [self.for_type(m) for m in dict.fromkeys(mimes)]
        if not lists:
            return []
        common = set.intersection(*({a.id for a in apps} for apps in lists))
        return [a for a in lists[0] if a.id in common]

    def shown(self) -> list[DesktopApp]:
        """Every app a menu would list, by name."""
        return sorted(self._shown.values(), key=lambda a: a.name.casefold())

    def get(self, app_id: str) -> DesktopApp | None:
        return self._shown.get(app_id)


# --- Exec lines -------------------------------------------------------------

_QUOTED_ESCAPES = '"`$\\'
_DEPRECATED_CODES = "dDnNvm"


def split_exec(value: str) -> list[str]:
    """Split an (already unescaped) ``Exec`` value into arguments, honoring quotes."""
    args: list[str] = []
    current: list[str] = []
    quoted = in_arg = False
    i = 0
    while i < len(value):
        c = value[i]
        if quoted:
            if c == "\\" and i + 1 < len(value) and value[i + 1] in _QUOTED_ESCAPES:
                current.append(value[i + 1])
                i += 1
            elif c == '"':
                quoted = False
            else:
                current.append(c)
        elif c == '"':
            quoted = in_arg = True
        elif c in " \t\n":
            if in_arg:
                args.append("".join(current))
                current, in_arg = [], False
        else:
            current.append(c)
            in_arg = True
        i += 1
    if quoted:
        raise ValueError("unterminated quote in Exec")
    if in_arg:
        args.append("".join(current))
    return args


def _uri(path: Path) -> str:
    return Path(os.path.abspath(path)).as_uri()


def _expand_arg(arg: str, app: DesktopApp, file: Path | None) -> str | None:
    """Expand the field codes inside one argument; None drops the argument."""
    out: list[str] = []
    dropped = False
    i = 0
    while i < len(arg):
        c = arg[i]
        if c != "%" or i + 1 == len(arg):
            out.append(c)
            i += 1
            continue
        code = arg[i + 1]
        i += 2
        if code == "%":
            out.append("%")
        elif code in "fF":
            out.append(str(file) if file else "")
            dropped |= file is None
        elif code in "uU":
            out.append(_uri(file) if file else "")
            dropped |= file is None
        elif code == "c":
            out.append(app.name)
        elif code == "k":
            out.append(str(app.path))
        elif code in _DEPRECATED_CODES or code == "i":
            dropped = True
        else:
            out.append("%" + code)
    text = "".join(out)
    return None if dropped and not text else text


def expand_exec(app: DesktopApp, files: Sequence[Path]) -> list[list[str]]:
    """The command lines that open ``files`` with ``app``.

    ``%F``/``%U`` take every file in one command; ``%f``/``%u`` take one, so the app is
    started once per file. Without a file code the app is started once, without files.
    """
    args = split_exec(app.exec)
    if not args:
        raise ValueError(f"{app.id} has no command")
    joined = " ".join(args)
    multi = "%F" in joined or "%U" in joined
    single = "%f" in joined or "%u" in joined
    one_command = multi or not single or not files
    batches = [list(files)] if one_command else [[f] for f in files]

    commands = []
    for batch in batches:
        argv: list[str] = []
        for arg in args:
            if arg == "%F":
                argv += [str(f) for f in batch]
            elif arg == "%U":
                argv += [_uri(f) for f in batch]
            elif arg == "%i":
                if app.icon:
                    argv += ["--icon", app.icon]
            else:
                expanded = _expand_arg(arg, app, batch[0] if batch else None)
                if expanded is not None:
                    argv.append(expanded)
        commands.append(argv)
    return commands


# --- starting programs ------------------------------------------------------


def desktop_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for desktop programs: without Quince's Python and virtualenv."""
    env = clean_env() if base is None else {k: v for k, v in base.items() if k != "PYTHONPATH"}
    for key in ("PYTHONHOME", "VIRTUAL_ENV", "PYSIDE6_OPTION_PYTHON_ENUM"):
        env.pop(key, None)
    if sys.prefix != sys.base_prefix:
        venv_bin = os.path.join(sys.prefix, "bin")
        parts = [p for p in env.get("PATH", "").split(os.pathsep) if p and p != venv_bin]
        env["PATH"] = os.pathsep.join(parts)
    return env


def spawn(argv: Sequence[str], cwd: Path | None = None) -> None:
    """Start a program detached from Quince; raises ``JobError`` if it cannot start."""
    name = Path(argv[0]).name
    log.info("starting %s", " ".join(argv))
    try:
        proc = subprocess.Popen(
            list(argv),
            cwd=str(cwd) if cwd else str(Path.home()),
            env=desktop_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise JobError(f"{name} is not installed") from exc
    except OSError as exc:
        raise JobError(f"Cannot start {name}: {exc.strerror}") from exc
    # Collect the exit status so no zombie is left behind.
    threading.Thread(target=proc.wait, name="quince-reap", daemon=True).start()


def launch(app: DesktopApp, files: Sequence[Path], terminal: Terminal | None = None) -> None:
    """Open ``files`` with ``app``."""
    try:
        commands = expand_exec(app, files)
    except ValueError as exc:
        raise JobError(f"{app.name} cannot be started: {exc}") from exc
    for argv in commands:
        if app.terminal:
            if terminal is None:
                raise JobError(f"{app.name} needs a terminal, and none was found")
            argv = terminal.wrap(argv)
        spawn(argv, app.working_dir)


# --- terminals --------------------------------------------------------------

# Program name -> (arguments to start in a folder, arguments before a command to run).
TERMINALS: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "gnome-terminal": (("--working-directory={dir}",), ("--",)),
    "kgx": (("--working-directory={dir}",), ("--",)),
    "terminator": (("--working-directory={dir}",), ("-x",)),
    "xfce4-terminal": (("--working-directory={dir}",), ("-x",)),
    "mate-terminal": (("--working-directory={dir}",), ("-x",)),
    "tilix": (("--working-directory={dir}",), ("-e",)),
    "lxterminal": (("--working-directory={dir}",), ("-e",)),
    "foot": (("--working-directory={dir}",), ()),
    "konsole": (("--workdir", "{dir}"), ("-e",)),
    "qterminal": (("--workdir", "{dir}"), ("-e",)),
    "alacritty": (("--working-directory", "{dir}"), ("-e",)),
    "kitty": (("--directory", "{dir}"), ()),
    "xterm": ((), ("-e",)),
}
FALLBACK_TERMINALS = (
    "x-terminal-emulator",
    "gnome-terminal",
    "kgx",
    "konsole",
    "xfce4-terminal",
    "mate-terminal",
    "tilix",
    "terminator",
    "alacritty",
    "kitty",
    "foot",
    "lxterminal",
    "qterminal",
    "xterm",
)


@dataclass(frozen=True)
class Terminal:
    command: str  # what to run
    name: str  # the program behind it, e.g. "terminator" for x-terminal-emulator

    def _flags(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return TERMINALS.get(self.name, ((), ("-e",)))

    def open_args(self, folder: Path) -> list[str]:
        workdir, _run = self._flags()
        return [self.command, *(a.format(dir=folder) for a in workdir)]

    def wrap(self, argv: Sequence[str]) -> list[str]:
        _workdir, run = self._flags()
        return [self.command, *run, *argv]

    def open_in(self, folder: Path) -> None:
        # The folder is also the working directory, for terminals without a flag for it.
        spawn(self.open_args(folder), cwd=folder)


def program_name(path: str) -> str:
    """The real program behind a command: alternatives symlinks and wrappers resolved."""
    name = Path(os.path.realpath(path)).name
    for suffix in (".wrapper", ".real"):
        name = name.removesuffix(suffix)
    return name


def gsettings_terminal() -> str | None:
    """GNOME's configured terminal (``org.gnome.desktop.default-applications.terminal``)."""
    gsettings = shutil.which("gsettings")
    if gsettings is None:
        return None
    try:
        proc = subprocess.run(
            [gsettings, "get", "org.gnome.desktop.default-applications.terminal", "exec"],
            capture_output=True,
            text=True,
            timeout=GSETTINGS_TIMEOUT_S,
            env=desktop_env(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = proc.stdout.strip().strip("'\"")
    return value if proc.returncode == 0 and value else None


def find_terminal(
    which: Callable[[str], str | None] = shutil.which,
    configured: Callable[[], str | None] = gsettings_terminal,
) -> Terminal | None:
    """The user's terminal: GNOME's setting, then ``x-terminal-emulator``, then known ones."""
    for candidate in (configured(), *FALLBACK_TERMINALS):
        if not candidate:
            continue
        path = which(candidate)
        if path is None:
            continue
        name = program_name(path)
        if name == "gnome-terminal" and Path(path).name != "gnome-terminal":
            # Debian's gnome-terminal.wrapper only understands xterm options.
            path = which("gnome-terminal") or path
        return Terminal(path, name)
    return None
