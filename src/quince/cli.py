"""Command line interface: ``quince [--version] <command>``."""

from __future__ import annotations

import argparse
import os
import platform
import sys
from collections.abc import Sequence

from quince import __version__
from quince.core import deps


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


def render_doctor(statuses: Sequence[deps.DependencyStatus]) -> str:
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
    return "\n".join(lines)


def cmd_doctor(_args: argparse.Namespace) -> int:
    statuses = deps.check_all()
    print(render_doctor(statuses))
    return 1 if any(s.dependency.required and not s.available for s in statuses) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quince", description=__doc__)
    parser.add_argument("--version", action="version", version=f"quince {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    doctor = sub.add_parser("doctor", help="check external dependencies")
    doctor.set_defaults(func=cmd_doctor)
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
