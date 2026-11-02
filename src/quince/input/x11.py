"""Global input watcher for X11: XInput2 raw events plus XFixes selection tracking.

Runs on its own thread with its own X connection. Raw events reach the root window
even while another client (e.g. Nautilus during a drag) holds a pointer or keyboard
grab, as long as the client announces XInput >= 2.1. python-xlib hardcodes 2.0 in
``xinput_query_version`` and does not parse raw events, so both are done here.
"""

from __future__ import annotations

import logging
import select
import threading
from collections.abc import Callable, Iterable

from Xlib import XK, X, display, error
from Xlib.ext import ge, xfixes, xinput
from Xlib.protocol import rq

from quince.input import drag

log = logging.getLogger(__name__)

XI_VERSION = (2, 2)
POLL_INTERVAL_S = 0.2

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
