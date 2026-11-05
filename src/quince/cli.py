"""Command line interface: ``quince [--version] <command>``."""

from __future__ import annotations

import argparse
import logging
import os
import platform
import sys
from collections.abc import Sequence
from pathlib import Path

from quince import __version__
from quince.core import config, deps, paths
from quince.core.operations import Kind, Operation
from quince.core.registry import Registry, discover
from quince.core.selection import Selection

WHEELS = config.WHEELS
SCHEMES = ("system", "light", "dark")  # quince.ui.theme.colors.SCHEMES, without importing Qt


def _status_row(status: deps.DependencyStatus) -> str:
    dep = status.dependency
    mark = "✓" if status.available else "✗"
    version = status.version or ("found" if status.available else "missing")
    hint = ""
    if not status.available:
        if dep.apt:
            hint = f"  (apt: {dep.apt})"
        elif dep.pip:
            hint = f"  (pip: {dep.pip})"
    return f"  {mark} {dep.id:<14} {version:<12} {dep.enables}{hint}"


def render_doctor(
    statuses: Sequence[deps.DependencyStatus],
    registry: Registry | None = None,
    *,
    terminal: object = ...,
    self_tests: Sequence[tuple[str, bool | None, str]] = (),
) -> str:
    """The ``quince doctor`` report. ``terminal`` is the found ``Terminal`` (or None);
    left out, the terminal line is omitted. ``self_tests`` are (name, passed, detail)
    rows; passed None means skipped."""
    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "unknown")
    lines = [
        f"Quince {__version__} - dependency report",
        "",
        "Environment",
        f"  Python   {platform.python_version()}",
        f"  Session  {session} ({desktop})",
    ]
    if session != "x11":
        lines.append("  ! The global wheel needs an X11 session; Wayland is not supported.")
    if terminal is None:
        lines.append("  ! No terminal program found; Open in Terminal will not work.")
    elif terminal is not ...:
        lines.append(f"  Terminal {terminal.command} ({terminal.name})")

    required = [s for s in statuses if s.dependency.required]
    optional = [s for s in statuses if not s.dependency.required]
    lines += ["", "Required", *map(_status_row, required)]
    lines += ["", "Optional", *map(_status_row, optional)]

    missing_required = [s for s in required if not s.available]
    missing_optional = [s for s in optional if not s.available]
    lines.append("")
    if missing_required:
        lines.append(f"Summary: {len(missing_required)} required dependency(s) missing.")
    else:
        lines.append("Summary: all required dependencies found.")
    if missing_optional:
        lines.append(
            f"         {len(missing_optional)} optional dependency(s) missing; "
            "operations that need them are hidden."
        )
    apt = deps.missing_apt_packages(statuses)
    if apt:
        lines.append(f"Install with: sudo apt install {' '.join(apt)}")

    if self_tests:
        lines += ["", "Self-tests"]
        for name, passed, detail in self_tests:
            mark = "-" if passed is None else ("✓" if passed else "✗")
            lines.append(f"  {mark} {name:<22} {detail}")

    if registry is not None:
        hidden = registry.unavailable(deps.Availability(statuses))
        lines += ["", f"Operations: {len(registry)} registered, {len(hidden)} hidden"]
        lines += [f"  ✗ {h.label:<14} {h.reason}" for h in hidden]
    return "\n".join(lines)


def run_self_tests(
    statuses: Sequence[deps.DependencyStatus],
) -> list[tuple[str, bool | None, str]]:
    """Real conversions of the routes that matter most (CLAUDE.md Decision Log)."""
    available = deps.Availability(statuses)
    tests: list[tuple[str, bool | None, str]] = []
    if available.missing(("pandoc",)):
        tests.append(("DOCX → Markdown", None, "skipped: pandoc is missing"))
    else:
        from quince.backends import pandoc

        passed, detail = pandoc.self_test()
        tests.append(("DOCX → Markdown", passed, detail))
    if available.missing(("pymupdf", "pymupdf4llm")):
        tests.append(("PDF → Markdown", None, "skipped: pymupdf4llm is missing"))
    else:
        from quince.backends import pdf

        passed, detail = pdf.self_test()
        tests.append(("PDF → Markdown", passed, detail))
    return tests


def cmd_doctor(_args: argparse.Namespace) -> int:
    statuses = deps.check_all()
    from quince.backends.desktop import find_terminal

    print(
        render_doctor(
            statuses, discover(), terminal=find_terminal(), self_tests=run_self_tests(statuses)
        )
    )
    return 1 if any(s.dependency.required and not s.available for s in statuses) else 0


def _wheel_section(title: str, ops: Sequence[Operation], empty: str) -> list[str]:
    lines = [f"{title} ({len(ops)})" if ops else title]
    if not ops:
        return [*lines, f"  {empty}"]
    width = max(len(op.label) for op in ops)
    return [*lines, *(f"  {op.label:<{width}}  {op.id}" for op in ops)]


def render_ops(
    selection: Selection,
    registry: Registry,
    available: deps.Availability,
    cfg: config.Config,
) -> str:
    """The ``quince list-ops`` report: what each wheel offers for ``selection``."""
    names = ", ".join(i.path.name or str(i.path) for i in selection.items)
    lines = [f"Selection: {names}", f"  {selection.describe()}"]
    hidden: list[str] = []
    disabled: list[str] = []
    for kind, title in ((Kind.CONVERT, "Convert"), (Kind.TOOL, "Tools"), (Kind.ACTION, "Actions")):
        prefs = cfg.wheel(kind.wheel)
        shown = registry.query(selection, kind, available=available, prefs=prefs)
        if kind is not Kind.ACTION and not selection.is_uniform:
            empty = (
                "(only file actions for folders and mixed selections)"
                if selection.has_folders or selection.is_mixed
                else "(unknown file type)"
            )
        else:
            empty = "(nothing available)"
        lines += ["", *_wheel_section(title, shown, empty)]
        for op in registry.query(selection, kind):
            if reason := available.reason(op.requirements(selection)):
                hidden.append(f"  {op.label} ({op.id}): {reason}")
            elif op.id in prefs.disabled:
                disabled.append(f"  {op.label} ({op.id})")
    if hidden:
        lines += ["", "Hidden, missing dependencies", *hidden]
    if disabled:
        lines += ["", "Disabled in config", *disabled]
    return "\n".join(lines)


def cmd_list_ops(args: argparse.Namespace) -> int:
    missing = [p for p in args.paths if not Path(p).exists()]
    if missing:
        print(f"quince: no such file or folder: {', '.join(missing)}", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.WARNING, format="quince: %(message)s")
    selection = Selection.from_paths(Path(p).absolute() for p in args.paths)
    report = render_ops(selection, discover(), deps.Availability.probe(), config.load())
    print(report)
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.WARNING, format="quince: %(message)s")
    path = paths.config_file()
    if args.path:
        print(path)
        return 0
    state = "exists" if path.exists() else "not created yet, defaults apply"
    print(f"# {path} ({state})")
    print(config.dumps(config.load(path)), end="")
    return 0


def cmd_wheel_demo(args: argparse.Namespace) -> int:
    from quince.ui.wheel import demo

    _setup_logging(args.verbose)
    return demo.run(
        count=args.n,
        wheel=args.wheel,
        files=args.files,
        scheme=args.theme,
        animations=not args.no_animations,
        once=args.once,
        grab=not args.no_grab,
    )


def _setup_logging(verbose: bool, *, to_file: bool = False) -> None:
    from quince.core.logs import setup_logging

    setup_logging(verbose, to_file=to_file)


def cmd_service(args: argparse.Namespace) -> int:
    from quince import app

    if args.stop:
        answer = app.send_command("quit")
        print("Quince stopped." if answer is not None else "Quince is not running.")
        return 0 if answer is not None else 1
    _setup_logging(args.verbose, to_file=True)
    return app.run_service(demo_ops=args.demo_ops)


def _ask_service(command: str) -> int:
    """Send ``command`` to the service, starting it first when it is not running."""
    from quince import app

    try:
        app.ensure_service()
    except app.ServiceUnavailable as exc:
        from quince.core.logs import log_file

        print(f"quince: {exc}; see {log_file()}", file=sys.stderr)
        return 1
    answer = app.send_command(command)
    if answer is None:
        print("quince: the Quince service did not answer", file=sys.stderr)
        return 1
    if answer != "ok":
        print(f"quince: {answer}", file=sys.stderr)
        return 1
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    from quince.ui.shell import open_request

    missing = [p for p in args.paths if not os.path.lexists(p)]
    if missing:
        print(f"quince: no such file or folder: {', '.join(missing)}", file=sys.stderr)
        return 2
    paths = [os.path.abspath(p) for p in args.paths]
    return _ask_service(open_request(paths, args.wheel))


def cmd_home(_args: argparse.Namespace) -> int:
    return _ask_service("home")


def cmd_library(_args: argparse.Namespace) -> int:
    return _ask_service("library")


def cmd_integrate(args: argparse.Namespace) -> int:
    from quince.integrations import install

    if args.remove:
        removed = install.uninstall()
        for path in removed:
            print(f"removed {path}")
        if not removed:
            print("Nothing to remove.")
        else:
            print("Restart Nautilus to drop its menu entry: nautilus -q")
        return 0
    for path in install.install():
        print(f"installed {path}")
    print()
    print("The app menu now has Quince (Home), with Library and Quit Quince actions.")
    if install.nautilus_python_available():
        print('Restart Nautilus to load "Open in Quince": nautilus -q')
    else:
        print('"Open in Quince" in Nautilus needs nautilus-python:')
        print("  sudo apt install python3-nautilus && nautilus -q")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quince", description=__doc__)
    parser.add_argument("--version", action="version", version=f"quince {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    doctor = sub.add_parser("doctor", help="check external dependencies")
    doctor.set_defaults(func=cmd_doctor)

    list_ops = sub.add_parser("list-ops", help="show what each wheel offers for files/folders")
    list_ops.add_argument("paths", nargs="+", metavar="PATH")
    list_ops.set_defaults(func=cmd_list_ops)

    cfg = sub.add_parser("config", help="print the effective settings")
    cfg.add_argument("--path", action="store_true", help="only print the config file path")
    cfg.set_defaults(func=cmd_config)

    wheel = sub.add_parser("wheel-demo", help="preview the wheel with any number of slices")
    wheel.add_argument("--n", type=int, default=7, help="number of slices (default 7)")
    wheel.add_argument("--wheel", choices=WHEELS, default="convert", help="wheel to open first")
    wheel.add_argument(
        "--file",
        dest="files",
        action="append",
        default=[],
        metavar="PATH",
        help="show the real wheels for this file (repeatable); overrides --n",
    )
    wheel.add_argument("--theme", choices=SCHEMES, default="system")
    wheel.add_argument("--no-animations", action="store_true")
    wheel.add_argument("--once", action="store_true", help="quit after the first close")
    wheel.add_argument(
        "--no-grab",
        action="store_true",
        help="do not take the keyboard (lets you drag files from Nautilus onto the wheel)",
    )
    wheel.add_argument("-v", "--verbose", action="store_true")
    wheel.set_defaults(func=cmd_wheel_demo)

    service = sub.add_parser("service", help="run the background service (drag + tap wheel)")
    service.add_argument("--stop", action="store_true", help="stop the running service")
    service.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    service.add_argument(
        "--demo-ops", action="store_true", help="add two demo jobs to the Tools wheel (testing)"
    )
    service.set_defaults(func=cmd_service)

    open_ = sub.add_parser("open", help="open the wheel at the pointer for files or folders")
    open_.add_argument(
        "--wheel",
        choices=WHEELS,
        help="wheel to open (default: the first one with items: Convert, Tools, Actions)",
    )
    open_.add_argument("paths", nargs="+", metavar="PATH")
    open_.set_defaults(func=cmd_open)

    home = sub.add_parser("home", help="show the Quince window (starts the service if needed)")
    home.set_defaults(func=cmd_home)
    library = sub.add_parser("library", help="show the Library")
    library.set_defaults(func=cmd_library)

    integrate = sub.add_parser(
        "integrate", help="add Quince to the app menu and the Nautilus context menu"
    )
    integrate.add_argument("--remove", action="store_true", help="remove what was added")
    integrate.set_defaults(func=cmd_integrate)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
