"""Finding and opening editors.

An operation names its editor (``Operation.editor = "image.crop"``); every module in the
editor packages (``quince.ui.editors.image``, ...) that defines ``EDITOR`` (an
``EditorWindow`` subclass with that ``name``) or ``EDITORS`` (several) provides them.
Modules starting with ``_`` are skipped. Adding an editor needs no change here.

``EditorManager.open`` reads the files off the GUI thread, shows the window and, on Save,
hands the editor's job to ``submit``. Several editors may be open at once.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable, Mapping

from PySide6.QtCore import QObject

from quince.core.jobs import JobError
from quince.core.operations import JobRequest, Operation
from quince.core.selection import Selection
from quince.ui.editors.base import EditorWindow
from quince.ui.host import run_off_gui_thread

log = logging.getLogger(__name__)

EDITOR_PACKAGES = ("quince.ui.editors.image", "quince.ui.editors.media")
OPEN_MESSAGE = "Opening the editor…"


def discover(packages: tuple[str, ...] = EDITOR_PACKAGES) -> dict[str, type[EditorWindow]]:
    editors: dict[str, type[EditorWindow]] = {}
    for package_name in packages:
        package = importlib.import_module(package_name)
        for info in pkgutil.iter_modules(package.__path__):
            if info.name.startswith("_"):
                continue
            module = importlib.import_module(f"{package_name}.{info.name}")
            found = getattr(module, "EDITORS", ())
            if getattr(module, "EDITOR", None) is not None:
                found = (*found, module.EDITOR)
            for editor in found:
                if not editor.name:
                    raise ValueError(f"{module.__name__}: an editor has no name")
                if editor.name in editors:
                    raise ValueError(f"duplicate editor name {editor.name!r}")
                editors[editor.name] = editor
    return editors


Submit = Callable[[Operation, Selection, JobRequest], None]


class EditorManager(QObject):
    def __init__(
        self,
        submit: Submit,
        *,
        notify: Callable[[str], None],
        notify_error: Callable[[str], None],
        editors: Mapping[str, type[EditorWindow]] | None = None,
        compute: Callable = run_off_gui_thread,
    ) -> None:
        super().__init__()
        self._submit = submit
        self._notify = notify
        self._notify_error = notify_error
        self._editors = dict(editors) if editors is not None else None
        self._compute = compute
        self.windows: list[EditorWindow] = []

    @property
    def editors(self) -> dict[str, type[EditorWindow]]:
        if self._editors is None:
            self._editors = discover()
        return self._editors

    def open(self, op: Operation, selection: Selection) -> EditorWindow | None:
        editor = self.editors.get(op.editor or "")
        if editor is None:
            log.error("%s: no editor named %r", op.id, op.editor)
            self._notify_error(f"{op.title}: the editor is missing")
            return None
        log.info("opening editor %s for %d item(s)", editor.name, len(selection))
        try:
            data = self._compute(
                lambda: editor.load(selection), on_slow=lambda: self._notify(OPEN_MESSAGE)
            )
        except JobError as exc:
            log.warning(
                "%s: cannot open: %s%s", op.id, exc.reason, f"\n{exc.details}" * bool(exc.details)
            )
            self._notify_error(f"{op.title}: {exc.reason}")
            return None
        except Exception as exc:
            log.exception("%s: editor failed to load", op.id)
            self._notify_error(f"{op.title}: unexpected error: {exc}")
            return None
        try:
            window = editor(selection, data)
        except Exception as exc:
            log.exception("%s: editor window failed", op.id)
            self._notify_error(f"{op.title}: unexpected error: {exc}")
            return None
        window.saved.connect(lambda request: self._submit(op, selection, request))
        window.destroyed.connect(lambda _obj=None, w=window: self._forget(w))
        self.windows.append(window)
        window.present()
        return window

    def _forget(self, window: EditorWindow) -> None:
        if window in self.windows:
            self.windows.remove(window)
