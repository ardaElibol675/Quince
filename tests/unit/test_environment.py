"""The development environment matches requirements.lock, and PYTHONPATH cannot interfere."""

import importlib.metadata
import subprocess
import sys
from pathlib import Path

import pytest

from quince import strip_foreign_paths

ROOT = Path(__file__).resolve().parents[2]


def _locked() -> dict[str, str]:
    pins = {}
    for line in (ROOT / "requirements.lock").read_text().splitlines():
        if line and not line.startswith("#"):
            name, _, version = line.partition("==")
            pins[name] = version
    return pins


def test_installed_packages_match_the_lock():
    mismatched = []
    for name, version in _locked().items():
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed = "missing"
        if installed != version:
            mismatched.append(f"{name}: lock {version}, installed {installed}")
    assert not mismatched, (
        "the .venv differs from requirements.lock; run "
        "pip install -c requirements.lock -e '.[dev]' (or update the lock on purpose):\n"
        + "\n".join(mismatched)
    )


def test_every_runtime_dependency_is_locked():
    pins = {n.lower().replace("_", "-") for n in _locked()}
    requires = importlib.metadata.requires("quince") or []
    for req in requires:
        if "extra ==" in req or "python_version" in req:
            continue
        name = req.split(">")[0].split("<")[0].split("=")[0].split(";")[0].strip()
        assert name.lower().replace("_", "-") in pins, f"{name} is not in requirements.lock"


def test_strip_foreign_paths():
    sys_path = ["", "/opt/ros/lib", "/venv/lib/site-packages", "/opt/other"]
    assert strip_foreign_paths(sys_path, "/opt/ros/lib:/opt/other:") == [
        "",
        "/venv/lib/site-packages",
    ]


def test_foreign_pythonpath_cannot_shadow_venv_packages(tmp_path):
    """A fake PIL on PYTHONPATH (like ROS packages) must not win over the .venv one."""
    shadow = tmp_path / "PIL"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("raise ImportError('shadowed by PYTHONPATH')\n")
    code = "import quince, PIL, os; print(PIL.__file__); print(os.environ.get('PYTHONPATH'))"
    out = subprocess.run(
        [sys.executable, "-c", code],
        env={"PYTHONPATH": str(tmp_path), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    assert str(tmp_path) not in out[0]
    assert out[1] == "None"  # children do not inherit it either


@pytest.mark.parametrize("keep", ["1"])
def test_opt_out(tmp_path, keep):
    code = f"import quince, sys; print(any(p == {str(tmp_path)!r} for p in sys.path))"
    out = subprocess.run(
        [sys.executable, "-c", code],
        env={"PYTHONPATH": str(tmp_path), "QUINCE_KEEP_PYTHONPATH": keep, "PATH": "/usr/bin"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert out == "True"
