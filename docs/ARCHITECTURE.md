# Architecture

This document grows phase by phase. It records how the system works and what was learned
while building it.

## Global drag detection on X11 (Phase 1 spike)

### Pipeline

```
X server ──raw XI2 events + XFixes──▶ X11InputWatcher (own thread, own X connection)
                                         │ translates to drag.InputEvent
                                         ▼
                                     DragTracker (pure state machine, unit tested)
                                         │ DragStarted / TriggerTapped / DragKey / DragEnded
                                         ▼
                          Qt signal (queued) ──▶ GUI thread ──▶ overlay / wheel
```

- `src/quince/input/x11.py` is the only code that talks to X11 for input.
- `src/quince/input/drag.py` has no X11 or Qt imports. All the rules live there.
- `quince spike-drag` (`src/quince/ui/spike/`) wires both to a debug overlay. Phase 4 replaces
  the overlay with the wheel.

### How a drag is recognized

1. The watcher announces **XInput 2.2** and selects raw key, button and motion events on the
   root window for all master devices. Since XI 2.1, raw events reach the root window even
   while another client holds an active pointer or keyboard grab. GTK and Qt drag sources
   hold both grabs during a drag.
2. **Button 1** goes down. The pointer position is read with `QueryPointer`, because raw
   motion events carry device deltas, not screen coordinates.
3. The pointer moves at least **8 px**. Positions are only queried until then.
4. **`XdndSelection` gets a new owner** while the button is held. XFixes
   `SetSelectionOwnerNotify` reports this. Every XDND source (GTK, Qt, ...) owns this
   selection when a drag-and-drop starts. This is what separates a file drag from
   text selection, rubber-band selection or moving a window.
5. Steps 3 and 4 may arrive in either order. When both are true, the drag is in progress.
   Releasing button 1 ends it.

### How a tap is recognized

All of these must hold:
- A trigger key is pressed and released while a drag is in progress, both at press and at release.
- No other key is held at the press, and no other key or button is pressed before the release.
- The key is held for at most `max_tap_ms` (default 400 ms).

Auto-repeat presses are ignored. Super/Hyper keys are rejected as triggers. On the Turkish
layout the right Alt key is AltGr (`ISO_Level3_Shift`), so the Actions trigger is `Alt_L` only.

### The overlay window and drops

- The window flags are `Tool | Frameless | StaysOnTop | X11BypassWindowManagerHint |
  WindowDoesNotAcceptFocus`, with a translucent background. It is an override-redirect window:
  the window manager neither moves it nor focuses it, and it never takes keyboard focus. While
  a drag runs, the keyboard belongs to the drag source anyway, so keys are read from raw events.
- `setAcceptDrops(True)` makes Qt mark the window `XdndAware`, so drag sources find it under the
  pointer.
- **Drops are answered with `CopyAction` only**, in `dragEnter`, `dragMove` and `drop`. Even
  when the source proposes Move (Shift held in Nautilus), the source gets Copy back. A Move
  answer would let the source delete the original. A nested-X test covers this.
- **File paths are read at drag enter and reused at drop.** Reading the mime data again at
  drop is a second request to the drag source. If the source does not answer, Qt blocks the
  GUI thread for up to 5 s and returns nothing. This was observed in testing.
- The overlay closes when files are dropped on it, when Esc is pressed during the drag, or
  400 ms after the drag ends elsewhere. The grace period lets a drop on the overlay be
  processed first.
- The position comes from `QCursor.pos()` in Qt logical coordinates. The overlay is clamped to
  the screen that contains the cursor (`QGuiApplication.screenAt`).

### python-xlib quirks (worked around in `x11.py`)

- `xinput_query_version()` always sends 2.0. Raw events would then be blocked by grabs, so
  `XIQueryVersion` is sent directly with 2.2.
- Raw events are not parsed. A `RawEventData` struct is registered with `ge_add_event_data`.
- Extension sub-events are registered as **copies** of their classes, so `isinstance(event,
  xfixes.SetSelectionOwnerNotify)` is always false. Events are matched by type code and sub-code.
- `event.owner` is a `Window` object, not an int.

### Cancel with right or middle click

During a GTK3 drag, a right or middle click **drops** at the pointer (observed with
Nautilus). Quince cannot stop that drop. Instead:
1. The watcher sees the raw press of button 2 or 3 and the tracker emits `DragCancel`.
   Scroll buttons 4-7 are ignored.
2. The overlay goes into a *cancelled* state. It paints nothing, so it looks closed, but it
   stays mapped and `XdndAware` to swallow the drop GTK sends.
3. Every drop is answered with Copy at once. It is acted on only after `DROP_SETTLE_MS`
   (80 ms), because the raw click (watcher connection) and the XDND drop (Qt connection) can
   arrive in either order. A drop in the cancelled state is logged and ignored.

If the pointer is not over the wheel during the click, GTK drops wherever the pointer is, for
example into a Nautilus folder. This is Nautilus' normal behavior and Quince cannot intercept
it.

### Testing notes

- `tests/integration/test_x11_drag.py` runs the whole flow in a nested X server.
  - It runs automatically when `Xvfb` is installed. Without Xvfb it can run in a visible
    Xephyr window with `QUINCE_NESTED_X=1`.
  - The drag source is a **GTK3** window (`tests/integration/gtk_drag_source.py`, system
    Python with `python3-gi`). It uses the same drag-source code as Nautilus 42, so GTK
    behavior is covered: Enter drops, a right or middle click drops, Esc cancels, and Shift
    held at the drop makes GTK propose Move.
  - The tests assert the action the source finally gets (`DRAG END copy`). A mutation check
    (the overlay answering Move) makes them fail. The XDND `DELETE` request alone is no
    signal: a Qt target never sends it, even for Move.
- Input must be injected with XTest. `xdotool mousemove` warps the pointer, and warping
  produces no raw motion events.

### Confirmed in the real session (2026-11-02, Nautilus 42.6, two monitors)

- Taps open the right wheel: Shift → Convert, Ctrl → Tools, left Alt → Actions. Single files,
  three files and a folder are dropped with the correct paths. Originals stay in place, also
  with Shift or Ctrl held at the drop.
- Raw keys arrive while Nautilus holds its grab. The arrow keys move the pointer by 1 px (GTK),
  Enter drops onto the overlay, and Esc closes the overlay and cancels the drag.
- Long presses (> 400 ms) do not open the overlay. Normal Nautilus selection and drags are
  unaffected, and text selection or window moves with a tap open nothing.
- **Multi-monitor:** eDP-1 1920×1080 at (0, 246) and DP-1-0 2560×1440 at (1920, 0). The overlay
  opens on the screen under the cursor and stays fully inside it, also at edges and corners.
- **Scaling:** only scale 1.0 (`Xft.dpi` 96) was tested. With GNOME scaling on X11, Qt derives
  its factor from `Xft.dpi`. `QCursor.pos()` is in logical coordinates, so placement should
  hold. Raw X coordinates are used only for the 8 px threshold, in physical pixels.

### Consequences for the wheel (Phase 4)

- Keyboard choice works through GTK: Enter drops onto the wheel window under the pointer. The
  wheel must use the keyboard-highlighted slice for that drop, not the slice under the pointer.
- The arrow keys nudge the pointer by 1 px. In keyboard mode the wheel ignores the pointer
  position.
- Esc closes the wheel and cancels the drag. A right or middle click cancels as described
  above.
- Tapping Shift or Ctrl during a drag briefly changes the action Nautilus proposes. This is
  harmless because Quince always answers Copy.

### Limitations

- X11 only. Under Wayland, XWayland clients only see input aimed at X clients, so a Wayland
  Nautilus drag is invisible. `quince doctor` warns when the session is not X11.
- A drag started by any XDND source counts, not only Nautilus. This is intended: dragging from
  any file-aware app opens the wheel.
