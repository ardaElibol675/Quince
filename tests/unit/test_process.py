import os
import sys
import threading
import time

import pytest

from quince.backends import process
from quince.backends.process import BackendError
from quince.core.jobs import JobCancelled


class FakeCtx:
    def __init__(self):
        self.cancelled = False


def test_runs_and_collects_output():
    lines = []
    result = process.run(["sh", "-c", "echo one; echo two; echo err >&2"], on_stdout=lines.append)
    assert result.returncode == 0
    assert lines == ["one", "two"]
    assert result.stderr_tail == ("err",)


def test_nonzero_exit_gives_last_error_line():
    with pytest.raises(BackendError) as exc:
        process.run(["sh", "-c", "echo noise >&2; echo 'Invalid data found' >&2; exit 3"])
    assert exc.value.reason == "sh: Invalid data found"
    assert "noise" in exc.value.details


def test_nonzero_exit_without_stderr():
    with pytest.raises(BackendError, match="exited with code 4"):
        process.run(["sh", "-c", "exit 4"])
    assert process.run(["sh", "-c", "exit 4"], check=False).returncode == 4


def test_missing_program():
    with pytest.raises(BackendError, match="quince-no-such-tool is not installed"):
        process.run(["quince-no-such-tool"])


def test_cancel_kills_the_process_group():
    ctx = FakeCtx()
    threading.Timer(0.3, lambda: setattr(ctx, "cancelled", True)).start()
    start = time.monotonic()
    with pytest.raises(JobCancelled):
        # A child of the shell must die too, or the pipes would stay open.
        process.run(["sh", "-c", "sleep 30 & sleep 30"], ctx)
    assert time.monotonic() - start < 5


def test_timeout():
    with pytest.raises(BackendError, match=r"longer than 0\.3 s"):
        process.run(["sleep", "30"], timeout_s=0.3)


def test_child_environment_has_no_pythonpath(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/opt/ros/humble/lib/python3.10/site-packages")
    result = process.run(
        [sys.executable, "-c", "import os; print(os.environ.get('PYTHONPATH', 'none'))"]
    )
    assert result.stdout_tail == ("none",)
    assert process.clean_env({"A": "1"})["A"] == "1"
    assert "PYTHONPATH" not in process.clean_env()
    assert os.environ["PYTHONPATH"]  # the parent is untouched
