"""Runs the operation chosen on a wheel.

- ``run`` operations become background jobs right away.
- ``start`` operations (most file actions) run on the GUI thread once the drag is over and
  the wheel is gone, because they may show a dialog or a menu, which cannot get the pointer
  while the drag source holds it. A ``JobRequest`` they return is queued as a job.
- Operations without either still show the "not implemented yet" toast.

Errors from a ``start`` step are shown in an error toast with the short reason.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from quince.core import config
from quince.core.jobs import JobError, JobQueue
from quince.core.operations import Kind, Operation
from quince.core.selection import Selection

log = logging.getLogger(__name__)


def stub_message(op: Operation, selection: Selection) -> str:
    names = [i.path.name for i in selection.items]
    target = names[0] if len(names) == 1 else f"{len(names)} items"
    return f"{op.title} · {target}\nNot implemented yet"


def job_title(op: Operation) -> str:
    """Card title: "Convert to JPG" for conversions, the operation's title otherwise."""
    return f"Convert to {op.label}" if op.kind is Kind.CONVERT else op.title


class OperationRunner:
    def __init__(
        self,
        jobs: JobQueue,
        host,
        *,
        after_drag: Callable[[Callable[[], None]], None],
        load_config: Callable[[], config.Config] = config.load,
    ) -> None:
        self.jobs = jobs
        self.host = host
        self._after_drag = after_drag
        self._load_config = load_config

    def run(self, op: Operation, selection: Selection) -> None:
        if op.start is not None:
            self._after_drag(lambda: self.start(op, selection))
        elif op.run is not None:
            self._submit(op.run, selection, job_title(op), op.id)
        else:
            log.info("would run %s on %d item(s) (stub)", op.id, len(selection))
            self.host.notify(stub_message(op, selection))

    def start(self, op: Operation, selection: Selection) -> None:
        log.info("starting %s for %d item(s)", op.id, len(selection))
        try:
            request = op.start(selection, self.host)
        except JobError as exc:
            log.warning(
                "%s failed: %s%s", op.id, exc.reason, f"\n{exc.details}" if exc.details else ""
            )
            self.host.notify_error(f"{op.title}: {exc.reason}")
            return
        except Exception as exc:
            log.exception("%s crashed", op.id)
            self.host.notify_error(f"{op.title}: unexpected error: {exc}")
            return
        if request is not None:
            self._submit(request.run, selection, request.title or job_title(op), op.id)

    def _submit(self, run, selection: Selection, title: str, op_id: str) -> None:
        output = self._load_config().output
        self.jobs.submit(run, selection, title=title, op_id=op_id, output=output)
