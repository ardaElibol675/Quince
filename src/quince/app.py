"""The Quince background service: global drag watcher, wheel and single-instance socket.

``quince service`` runs it in the background. A second ``quince service`` finds the
running one through a local socket and exits; ``quince service --stop`` asks it to quit,
``quince service --restart`` stops it and opens Home, which starts a fresh one.
Other ``quince`` commands talk to it over the same socket, one line per request:
``<command>[ <argument>]`` (``ping``, ``quit``, ``home``, ``library``, ``open <json>``).
``ensure_service`` starts the service first when none is running.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

# Tests set QUINCE_INSTANCE so a nested-X service never meets the real one.
SERVER_NAME = os.environ.get("QUINCE_INSTANCE") or f"quince-{os.getuid()}"
COMMAND_TIMEOUT_MS = 1000
START_TIMEOUT_S = 15.0
START_POLL_S = 0.1
STOP_TIMEOUT_S = 10.0


def send_command(command: str, name: str = SERVER_NAME) -> str | None:
    """Send one command line to a running service; its answer, or None if none is running."""
    if QCoreApplication.instance() is None:
        _app = QCoreApplication([])
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(COMMAND_TIMEOUT_MS):
        return None
    sock.write((command + "\n").encode())
    sock.flush()
    answer = ""
    if sock.waitForReadyRead(COMMAND_TIMEOUT_MS):
        answer = bytes(sock.readLine()).decode(errors="replace").strip()
    sock.disconnectFromServer()
    return answer


class ServiceUnavailable(Exception):
    pass


def service_command() -> list[str]:
    return [sys.executable, "-m", "quince", "service"]


def ensure_service(
    name: str = SERVER_NAME,
    *,
    command: list[str] | None = None,
    timeout_s: float = START_TIMEOUT_S,
) -> bool:
    """Start the service in the background unless one answers. True when it was started.

    Raises ``ServiceUnavailable`` when it does not come up (no X11 session, a crash, ...).
    """
    if send_command("ping", name) is not None:
        return False
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    proc = subprocess.Popen(
        command or service_command(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if send_command("ping", name) is not None:
            log.info("started the Quince service (pid %d)", proc.pid)
            return True
        if proc.poll() is not None:
            raise ServiceUnavailable(
                f"the Quince service stopped at start (exit {proc.returncode})"
            )
        time.sleep(START_POLL_S)
    raise ServiceUnavailable("the Quince service did not answer")


def stop_service(name: str = SERVER_NAME, *, timeout_s: float = STOP_TIMEOUT_S) -> bool:
    """Ask the service to quit and wait until it no longer answers. True when one was running.

    Raises ``ServiceUnavailable`` when it still answers after ``timeout_s``.
    """
    if send_command("quit", name) is None:
        return False
    deadline = time.monotonic() + timeout_s
    # While it shuts down, a connection may still be accepted without an answer ("").
    while send_command("ping", name) is not None:
        if time.monotonic() >= deadline:
            raise ServiceUnavailable("the Quince service did not stop")
        time.sleep(START_POLL_S)
    log.info("stopped the Quince service")
    return True


class InstanceServer(QObject):
    """Answers commands from other ``quince`` processes, one line per connection.

    A handler gets the rest of the line after the command name (or "") and returns the answer.
    """

    def __init__(self, handlers: Mapping[str, Callable[[str], str]], name: str = SERVER_NAME):
        super().__init__()
        self.name = name
        self.handlers = handlers
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._on_connection)

    def listen(self) -> bool:
        """Start listening. Call only after ``send_command`` found no running instance."""
        if self._server.listen(self.name):
            return True
        # A crashed instance leaves its socket file behind; nobody answers on it.
        QLocalServer.removeServer(self.name)
        return self._server.listen(self.name)

    def close(self) -> None:
        self._server.close()

    def _on_connection(self) -> None:
        while (sock := self._server.nextPendingConnection()) is not None:
            sock.readyRead.connect(lambda s=sock: self._on_ready(s))
            sock.disconnected.connect(sock.deleteLater)
            # The command may have arrived before readyRead was connected.
            self._on_ready(sock)

    def _on_ready(self, sock: QLocalSocket) -> None:
        while sock.canReadLine():
            line = bytes(sock.readLine()).decode(errors="replace").strip()
            command, _sep, arg = line.partition(" ")
            handler = self.handlers.get(command)
            if handler is None:
                answer = f"unknown command {command!r}"
            else:
                try:
                    answer = handler(arg)
                except Exception as exc:
                    log.exception("command %r failed", command)
                    answer = f"error: {exc}"
            log.debug("command %r -> %r", line[:200], answer)
            sock.write((answer + "\n").encode())
            sock.flush()


class _Bridge(QObject):
    """Carries watcher signals from the X11 thread to the GUI thread (queued)."""

    received = Signal(object)


def run_service(*, demo_ops: bool = False) -> int:
    from PySide6.QtWidgets import QApplication

    from quince.core import config
    from quince.core.jobs import JobQueue
    from quince.core.registry import discover
    from quince.input import drag
    from quince.input.x11 import X11InputWatcher, X11Unavailable
    from quince.ui.editors.manager import EditorManager
    from quince.ui.feedback.jobs import JobBridge, JobsPanel
    from quince.ui.feedback.toast import Toast
    from quince.ui.host import QtHost
    from quince.ui.library.model import LibraryModel
    from quince.ui.runner import OperationRunner
    from quince.ui.shelf.window import ShelfWindow
    from quince.ui.shell import Shell, app_icon
    from quince.ui.wheel.controller import DragWheelController
    from quince.ui.wheel.window import WheelWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("quince")
    app.setApplicationDisplayName("Quince")
    app.setDesktopFileName("quince")
    app.setQuitOnLastWindowClosed(False)

    if send_command("ping") is not None:
        print("Quince is already running.")
        return 0
    handlers: dict[str, Callable[[str], str]] = {
        "ping": lambda _arg: "pong",
        "quit": lambda _arg: _quit(app),
    }
    server = InstanceServer(handlers)
    if not server.listen():
        log.error("cannot listen on %s", SERVER_NAME)
        return 1

    app.setWindowIcon(app_icon())
    cfg = config.load()
    registry = discover()
    if demo_ops:
        from quince.operations.demo import OPERATIONS

        for op in OPERATIONS:
            registry.register(op)
    wheel = WheelWindow()
    toast = Toast()
    panel = JobsPanel()
    job_bridge = JobBridge()
    job_bridge.event.connect(panel.on_event)
    jobs = JobQueue(on_event=job_bridge.event.emit)
    panel.cancel_requested.connect(jobs.cancel)
    controller = DragWheelController(wheel, registry)
    shelf: list[ShelfWindow] = []

    def get_shelf() -> ShelfWindow:
        if not shelf:
            shelf.append(ShelfWindow())
        return shelf[0]

    host = QtHost(toast, get_shelf)
    runner = OperationRunner(jobs, host, after_drag=controller.after_drag)
    editors = EditorManager(
        runner.submit_request, notify=host.notify, notify_error=host.notify_error
    )
    runner.open_editor = editors.open
    controller.operation_chosen.connect(runner.run)
    library = LibraryModel()
    job_bridge.event.connect(library.on_job_event)
    shell = Shell(
        wheel,
        registry,
        library,
        run_operation=runner.run,
        drag_busy=lambda: controller.in_drag_session,
        notify=host.notify_error,
        quit_app=lambda: _quit(app),
    )
    handlers.update(shell.handlers())
    bridge = _Bridge()
    bridge.received.connect(controller.handle)
    tracker = drag.DragTracker(cfg.triggers.keys, max_tap_ms=cfg.triggers.max_tap_ms)
    watcher = X11InputWatcher(tracker, bridge.received.emit)
    try:
        watcher.start()
    except X11Unavailable as exc:
        log.error("%s", exc)
        server.close()
        return 2

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: app.quit())
    tick = QTimer()
    tick.start(200)
    tick.timeout.connect(lambda: None)  # lets Python handle signals while Qt runs

    keys = ", ".join(f"{w}={'/'.join(k)}" for w, k in cfg.triggers.keys.items())
    log.info("Quince service running (%s; tap <= %d ms)", keys, cfg.triggers.max_tap_ms)
    try:
        return app.exec()
    finally:
        watcher.stop()
        server.close()
        jobs.shutdown(wait=True)  # cancels running jobs; their temporary files are removed
        log.info("Quince service stopped")


def _quit(app) -> str:
    QTimer.singleShot(0, app.quit)
    return "bye"
