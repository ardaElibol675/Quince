"""The Quince background service: global drag watcher, wheel and single-instance socket.

``quince service`` runs it without a main window. A second ``quince service`` finds the
running one through a local socket and exits; ``quince service --stop`` asks it to quit.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from collections.abc import Callable, Mapping

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

# Tests set QUINCE_INSTANCE so a nested-X service never meets the real one.
SERVER_NAME = os.environ.get("QUINCE_INSTANCE") or f"quince-{os.getuid()}"
COMMAND_TIMEOUT_MS = 1000


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


class InstanceServer(QObject):
    """Answers commands from other ``quince`` processes, one line per connection."""

    def __init__(self, handlers: Mapping[str, Callable[[], str]], name: str = SERVER_NAME):
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
            command = bytes(sock.readLine()).decode(errors="replace").strip()
            handler = self.handlers.get(command)
            answer = handler() if handler else f"unknown command {command!r}"
            log.debug("command %r -> %r", command, answer)
            sock.write((answer + "\n").encode())
            sock.flush()


class _Bridge(QObject):
    """Carries watcher signals from the X11 thread to the GUI thread (queued)."""

    received = Signal(object)


def _stub_message(op, selection) -> str:
    names = [i.path.name for i in selection.items]
    target = names[0] if len(names) == 1 else f"{len(names)} items"
    return f"{op.title} · {target}\nNot implemented yet"


def job_title(op) -> str:
    """Card title: "Convert to JPG" for conversions, the operation's title otherwise."""
    from quince.core.operations import Kind

    return f"Convert to {op.label}" if op.kind is Kind.CONVERT else op.title


def run_service(*, demo_ops: bool = False) -> int:
    from PySide6.QtWidgets import QApplication

    from quince.core import config
    from quince.core.jobs import JobQueue
    from quince.core.registry import discover
    from quince.input import drag
    from quince.input.x11 import X11InputWatcher, X11Unavailable
    from quince.ui.feedback.jobs import JobBridge, JobsPanel
    from quince.ui.feedback.toast import Toast
    from quince.ui.wheel.controller import DragWheelController
    from quince.ui.wheel.window import WheelWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("quince")
    app.setQuitOnLastWindowClosed(False)

    if send_command("ping") is not None:
        print("Quince is already running.")
        return 0
    server = InstanceServer({"ping": lambda: "pong", "quit": lambda: _quit(app)})
    if not server.listen():
        log.error("cannot listen on %s", SERVER_NAME)
        return 1

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

    def on_chosen(op, selection) -> None:
        if op.run is None:
            log.info("would run %s on %d item(s) (stub)", op.id, len(selection))
            toast.show_message(_stub_message(op, selection))
            return
        jobs.submit(
            op.run, selection, title=job_title(op), op_id=op.id, output=config.load().output
        )

    controller.operation_chosen.connect(on_chosen)
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
