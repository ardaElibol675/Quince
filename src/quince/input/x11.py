"""Global input watcher for X11: XInput2 raw events plus XFixes selection tracking.

Runs on its own thread with its own X connection. Raw events reach the root window
even while another client (e.g. Nautilus during a drag) holds a pointer or keyboard
grab, as long as the client announces XInput >= 2.1. python-xlib hardcodes 2.0 in
``xinput_query_version`` and does not parse raw events, so both are done here.

When a drag starts, the watcher also asks the drag source which formats it offers
(``TARGETS`` of ``XdndSelection``) and then for its ``text/uri-list``, so the wheel knows
the files before it opens. Drag sources answer any client, not only the drop target. A
source that offers no ``text/uri-list`` (a text drag from an editor or a browser) carries
no files.
"""

from __future__ import annotations

import logging
import select
import threading
import time
from collections.abc import Callable, Iterable

from Xlib import XK, X, display, error
from Xlib.ext import ge, xfixes, xinput
from Xlib.protocol import rq

from quince.input import drag
from quince.input.xdnd import parse_uri_list

log = logging.getLogger(__name__)

XI_VERSION = (2, 2)
POLL_INTERVAL_S = 0.2
# Give up on reading the dragged files after this; the drag-enter data is used instead.
FETCH_TIMEOUT_S = 1.0

_RAW_EVENTS = (
    xinput.RawKeyPress,
    xinput.RawKeyRelease,
    xinput.RawButtonPress,
    xinput.RawButtonRelease,
    xinput.RawMotion,
)
_RAW_MASK = (
    xinput.RawKeyPressMask
    | xinput.RawKeyReleaseMask
    | xinput.RawButtonPressMask
    | xinput.RawButtonReleaseMask
    | xinput.RawMotionMask
)

# xXIRawEvent body after the generic event header; valuator data that follows is ignored.
RawEventData = rq.Struct(
    rq.Card16("deviceid"),
    rq.Card32("time"),
    rq.Card32("detail"),
    rq.Card16("sourceid"),
    rq.Card16("valuators_len"),
    rq.Card32("flags"),
    rq.Pad(4),
)


class X11Unavailable(RuntimeError):
    """No usable X11 display, or a required extension is missing."""


def _keycode_names(disp: display.Display, names: Iterable[str]) -> dict[int, str]:
    """Map keycodes whose base keysym is one of ``names`` to that name."""
    wanted = {XK.string_to_keysym(name): name for name in names}
    wanted.pop(X.NoSymbol, None)
    setup = disp.display.info
    result: dict[int, str] = {}
    for code in range(setup.min_keycode, setup.max_keycode + 1):
        name = wanted.get(disp.keycode_to_keysym(code, 0))
        if name is not None:
            result[code] = name
    return result


class X11InputWatcher:
    """Feeds raw X11 input into a ``DragTracker`` and reports its signals.

    ``on_signal`` is called on the watcher thread; the UI must hop to its own thread
    (a Qt signal does this automatically).
    """

    def __init__(
        self,
        tracker: drag.DragTracker,
        on_signal: Callable[[drag.Signal], None],
        display_name: str | None = None,
    ) -> None:
        self._tracker = tracker
        self._on_signal = on_signal
        self._display_name = display_name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._disp: display.Display | None = None
        self._keys: dict[int, str] = {}
        self._xdnd_atom = 0
        self._xfixes_event = -1
        self._uri_list_atom = 0
        self._fetch_property = 0
        self._fetch_window = None
        self._targets_atom = 0
        self._fetch: tuple[int, float] | None = None  # (drag time, deadline)
        self._fetch_stage = ""  # "targets", then "uri-list"

    def start(self) -> None:
        """Connect and start the watcher thread; raises ``X11Unavailable`` on failure."""
        self._disp = self._connect()
        self._thread = threading.Thread(target=self._run, name="quince-x11-input", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    # --- setup --------------------------------------------------------------

    def _connect(self) -> display.Display:
        try:
            disp = display.Display(self._display_name)
        except (error.DisplayError, error.ConnectionClosedError) as exc:
            raise X11Unavailable(f"cannot open X display: {exc}") from exc

        xi = disp.query_extension(xinput.extname)
        if not xi.present or not disp.has_extension(xfixes.extname):
            disp.close()
            raise X11Unavailable("XInputExtension and XFIXES are required")
        reply = xinput.XIQueryVersion(
            display=disp.display,
            opcode=xi.major_opcode,
            major_version=XI_VERSION[0],
            minor_version=XI_VERSION[1],
        )
        if (reply.major_version, reply.minor_version) < (2, 1):
            disp.close()
            raise X11Unavailable(
                f"XInput {reply.major_version}.{reply.minor_version} is too old (need 2.1)"
            )
        for event_type in _RAW_EVENTS:
            disp.ge_add_event_data(xi.major_opcode, event_type, RawEventData)

        root = disp.screen().root
        root.xinput_select_events([(xinput.AllMasterDevices, _RAW_MASK)])
        disp.xfixes_query_version()
        self._xfixes_event = disp.query_extension(xfixes.extname).first_event + (
            xfixes.XFixesSelectionNotify
        )
        self._xdnd_atom = disp.intern_atom("XdndSelection")
        self._uri_list_atom = disp.intern_atom("text/uri-list")
        self._targets_atom = disp.intern_atom("TARGETS")
        self._fetch_property = disp.intern_atom("QUINCE_DRAG_FILES")
        self._fetch_window = root.create_window(-10, -10, 1, 1, 0, X.CopyFromParent)
        disp.xfixes_select_selection_input(
            root, self._xdnd_atom, xfixes.XFixesSetSelectionOwnerNotifyMask
        )
        self._refresh_keymap(disp)
        disp.sync()
        log.info(
            "X11 watcher connected: XInput %d.%d, %d tracked keys",
            reply.major_version,
            reply.minor_version,
            len(self._keys),
        )
        return disp

    def _refresh_keymap(self, disp: display.Display) -> None:
        self._keys = _keycode_names(disp, drag.NAV_KEYS | self._tracker.trigger_keys)

    # --- event loop -----------------------------------------------------------

    def _run(self) -> None:
        disp = self._disp
        assert disp is not None
        try:
            while not self._stop.is_set():
                readable, _, _ = select.select([disp.fileno()], [], [], POLL_INTERVAL_S)
                if not readable and not disp.pending_events():
                    continue
                while disp.pending_events():
                    self._handle(disp, disp.next_event())
                if self._fetch is not None and time.monotonic() > self._fetch[1]:
                    log.debug("no answer for the dragged files; waiting for drag enter")
                    self._finish_fetch(None)
        except error.ConnectionClosedError:
            log.error("X11 connection closed; global input watcher stopped")
        except Exception:
            log.exception("X11 input watcher crashed")
        finally:
            disp.close()

    def _handle(self, disp: display.Display, event) -> None:
        if event.type == ge.GenericEventCode and event.evtype in _RAW_EVENTS:
            translated = self._translate_raw(disp, event.evtype, event.data)
        elif (
            # python-xlib registers a copy of the event class, so isinstance() cannot be used.
            event.type == self._xfixes_event
            and event.sub_code == xfixes.XFixesSetSelectionOwnerNotify
        ):
            owner = getattr(event.owner, "id", event.owner)  # python-xlib gives a Window
            if event.selection != self._xdnd_atom or owner == X.NONE:
                return
            log.debug("XdndSelection owner -> 0x%x", owner)
            translated = drag.DndStarted(event.selection_timestamp)
        elif event.type == X.SelectionNotify:
            self._on_selection_notify(disp, event)
            return
        elif event.type == X.MappingNotify:
            disp.refresh_keyboard_mapping(event)
            self._refresh_keymap(disp)
            return
        else:
            return
        if translated is None:
            return
        for signal in self._tracker.feed(translated):
            self._on_signal(signal)
            if isinstance(signal, drag.DragStarted):
                self._request_files(disp, signal.time)

    # --- dragged files ----------------------------------------------------------

    def _request_files(self, disp: display.Display, drag_time: int) -> None:
        self._fetch = (drag_time, time.monotonic() + FETCH_TIMEOUT_S)
        self._convert(disp, "targets", self._targets_atom)

    def _convert(self, disp: display.Display, stage: str, target: int) -> None:
        self._fetch_stage = stage
        self._fetch_window.convert_selection(
            self._xdnd_atom, target, self._fetch_property, X.CurrentTime
        )
        disp.flush()

    def _read_reply(self, disp: display.Display):
        reply = self._fetch_window.get_full_property(self._fetch_property, X.AnyPropertyType)
        self._fetch_window.delete_property(self._fetch_property)
        disp.flush()
        return reply

    def _on_selection_notify(self, disp: display.Display, event) -> None:
        requestor = getattr(event.requestor, "id", event.requestor)
        if self._fetch is None or requestor != self._fetch_window.id:
            return
        if self._fetch_stage == "targets":
            self._on_targets(disp, event)
            return
        if event.property == X.NONE:
            log.debug("drag source refused text/uri-list")
            self._finish_fetch(None)
            return
        reply = self._read_reply(disp)
        if reply is None or reply.property_type != self._uri_list_atom:
            # INCR (very long lists) or an unexpected type: let drag enter provide the data.
            self._finish_fetch(None)
            return
        data = reply.value if isinstance(reply.value, bytes) else bytes(reply.value)
        self._finish_fetch(parse_uri_list(data))

    def _on_targets(self, disp: display.Display, event) -> None:
        if event.property == X.NONE:
            # Some sources do not answer TARGETS; ask for the file list directly.
            self._convert(disp, "uri-list", self._uri_list_atom)
            return
        reply = self._read_reply(disp)
        targets = list(reply.value) if reply is not None and reply.format == 32 else None
        if targets is None or self._uri_list_atom in targets:
            self._convert(disp, "uri-list", self._uri_list_atom)
        else:
            log.debug("drag source offers no text/uri-list")
            self._finish_fetch(())

    def _finish_fetch(self, paths: tuple[str, ...] | None) -> None:
        if self._fetch is None:
            return
        drag_time, self._fetch = self._fetch[0], None
        log.debug("dragged files: %s", "unknown" if paths is None else len(paths))
        self._on_signal(drag.DragFiles(drag_time, paths))

    def _translate_raw(self, disp: display.Display, evtype: int, data) -> drag.InputEvent | None:
        time, detail = data.time, data.detail
        if evtype == xinput.RawMotion:
            if not self._tracker.wants_motion:
                return None
            pointer = disp.screen().root.query_pointer()
            return drag.Motion(time, pointer.root_x, pointer.root_y)
        if evtype == xinput.RawButtonPress:
            pointer = disp.screen().root.query_pointer()
            return drag.ButtonPress(detail, time, pointer.root_x, pointer.root_y)
        if evtype == xinput.RawButtonRelease:
            return drag.ButtonRelease(detail, time)
        key = self._keys.get(detail, f"keycode:{detail}")
        pressed = evtype == xinput.RawKeyPress
        if self._tracker.in_drag:
            log.debug("raw key %s %s during drag", key, "down" if pressed else "up")
        return drag.KeyPress(key, time) if pressed else drag.KeyRelease(key, time)
