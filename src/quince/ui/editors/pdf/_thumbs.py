"""Page pictures for the PDF Editor, rendered on a thread of their own.

``image(source, number, pixels)`` returns the picture of a page at about that size (longer
side) when it is ready, else the nearest size already made (or None), and asks for it; the
pages asked for last are made first, so the pages on screen come before those scrolled past.
``ready`` is emitted after each one. Pictures come in a few fixed sizes and are kept up to a
memory budget (least recently used dropped first).
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Sequence

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from quince.backends import pdfedit

log = logging.getLogger(__name__)

SIZES = (160, 320, 640, 1280)  # pixels on the longer side
BUDGET = 256 * 1024 * 1024  # bytes of pictures kept
MAX_PENDING = 400
MAX_OPEN = 8  # documents kept open by the thread

Key = tuple[int, int, int]  # source, page number, size


def size_for(pixels: float) -> int:
    return next((s for s in SIZES if s >= pixels), SIZES[-1])


class Thumbnails(QObject):
    ready = Signal()

    def __init__(self, sources: Sequence[pdfedit.Source], budget: int = BUDGET) -> None:
        super().__init__()
        self._sources = sources  # the editor's list; sources are only ever added
        self._budget = budget
        self._cache: OrderedDict[Key, QImage] = OrderedDict()
        self._bytes = 0
        self._failed: set[Key] = set()
        self._pending: OrderedDict[Key, None] = OrderedDict()
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._stopped = False
        self._thread = threading.Thread(target=self._work, name="quince-thumbnails", daemon=True)
        self._thread.start()

    def image(self, source: int, number: int, pixels: float) -> QImage | None:
        wanted = size_for(pixels)
        key = (source, number, wanted)
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
            if key not in self._failed and not self._stopped:
                self._pending[key] = None
                self._pending.move_to_end(key)
                while len(self._pending) > MAX_PENDING:
                    self._pending.popitem(last=False)
                self._wake.notify()
            near = [k for k in self._cache if k[:2] == key[:2]]
            if not near:
                return None
            best = min(near, key=lambda k: (k[2] < wanted, abs(k[2] - wanted)))
            return self._cache[best]

    def failed(self, source: int, number: int) -> bool:
        with self._lock:
            return any(k[:2] == (source, number) for k in self._failed)

    def idle(self) -> bool:
        """Nothing waiting to be made (for tests)."""
        with self._lock:
            return not self._pending and not self._busy

    def close(self) -> None:
        with self._lock:
            self._stopped = True
            self._pending.clear()
            self._wake.notify()

    # --- the thread ---------------------------------------------------------------------

    _busy = False

    def _work(self) -> None:
        docs: OrderedDict[int, object] = OrderedDict()
        try:
            while True:
                with self._lock:
                    while not self._pending and not self._stopped:
                        self._wake.wait()
                    if self._stopped:
                        return
                    key, _ = self._pending.popitem(last=True)
                    self._busy = True
                try:
                    image = self._render(docs, key)
                except Exception as exc:
                    log.warning("cannot show page %d of source %d: %s", key[1] + 1, key[0], exc)
                    image = None
                with self._lock:
                    self._busy = False
                    if self._stopped:
                        return
                    if image is None:
                        self._failed.add(key)
                    else:
                        self._store(key, image)
                try:
                    self.ready.emit()
                except RuntimeError:  # the editor is gone
                    return
        finally:
            for doc in docs.values():
                doc.close()  # type: ignore[attr-defined]

    def _render(self, docs: OrderedDict, key: Key) -> QImage:
        import pymupdf

        source, number, size = key
        doc = docs.get(source)
        if doc is None:
            doc = pdfedit.open_source(self._sources[source])
            docs[source] = doc
            while len(docs) > MAX_OPEN:
                docs.popitem(last=False)[1].close()
        docs.move_to_end(source)
        page = doc[number]
        zoom = size / max(page.rect.width, page.rect.height, 1.0)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        return image.copy()

    def _store(self, key: Key, image: QImage) -> None:
        self._cache[key] = image
        self._bytes += image.sizeInBytes()
        while self._bytes > self._budget and len(self._cache) > 1:
            _old, dropped = self._cache.popitem(last=False)
            self._bytes -= dropped.sizeInBytes()
