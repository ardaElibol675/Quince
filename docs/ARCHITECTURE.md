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
- Phase 1 wired both to a debug overlay (`quince spike-drag`). Since Phase 4 the service
  (`quince service`) wires them to the wheel; see "Service and drag flow" below.

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

## Core model (Phase 2)

Everything in `src/quince/core/` is pure Python without Qt, so it is unit tested directly and
shared by the CLI, the background service and the UI.

| Module | Role |
|--------|------|
| `formats.py` | Format registry: id, short label, name, category, extensions, MIME types. The table order is the default slice order on the Convert wheel. |
| `filetype.py` | Detection: extension plus content signatures (see below). |
| `selection.py` | `Selection` of files and folders: category, mixed, folders, unknown types. |
| `routes.py` | Conversion routes as data: `Route(source, target, backend, options, requires)`. |
| `operations.py` | The operation contract (`Operation`, `Accepts`, `Kind`). |
| `registry.py` | Discovery, filtering by selection, availability and user preferences. |
| `deps.py` | Dependency declarations, `quince doctor` checks, `Availability`. |
| `naming.py` | Collision-safe output paths. |
| `config.py`, `paths.py` | TOML settings in XDG directories. |

### From modules to wheel items

```
operations/convert/*.py  ROUTES = (Route("png", "jpg", "imaging"), ...)
            │  RouteTable (one route per source/target pair)
            ▼
  convert_operations(): one Operation per target format, e.g. convert.image.jpg,
            │           holding the routes into that target
operations/tools/*.py, operations/actions/*.py  OPERATION = Operation(...)
            ▼
Registry.query(selection, kind, available=..., prefs=...) ──▶ the wheel's slices, in order
```

- `discover()` imports every module of `quince.operations.convert`, `.tools` and `.actions`
  (names starting with `_` are skipped). A module without `ROUTES` / `OPERATION` is an error.
  So adding a route line or an operation module needs no other code change; a test proves it
  with a temporary package.
- `query` rules, in order:
  1. Convert and Tools return nothing unless the selection is files of one known category
     (Hard Rule 9). Actions accept anything they declare.
  2. `Accepts`: formats or categories, item count, folders, mixed, an optional predicate.
  3. A Convert target is accepted when every file has a route to it or already is in that
     format, and at least one file needs converting. So JPG + HEIC still offers JPG.
  4. Availability: the dependencies of the routes the selection actually uses must be present.
     A missing LibreOffice hides DOCX → PNG but not JPG → PNG.
  5. Config: disabled ids are dropped; ids in `order` come first, the rest keep the default
     order (`Operation.order`, then label).
- `Registry.unavailable()` lists what missing dependencies hide, per route for Convert. It
  feeds `quince doctor` now and the Settings window later (Phase 16).

### File type detection

`sniff()` reads the first 4 KiB and returns every format the content is compatible with,
best guess first. Shared containers return all their formats: ISO media (MP4/M4A/MOV; HEIC
and AVIF are told apart by brand), Matroska (MKV/WebM), Ogg (OGG/Opus), ASF (WMA/WMV, video
stream GUID), plain text. ZIP files are opened to tell DOCX/XLSX/PPTX, ODT/ODS/ODP and EPUB
from plain ZIP. OLE2 files (DOC/XLS/PPT) are told apart by walking the compound file
directory (the names are often not in the first sector).

`resolve()` then decides:
- extension format among the candidates, or content inconclusive → the extension wins;
- content contradicts the extension → the content wins (a PNG renamed to `.jpg` is a PNG);
- no extension match and no content match → unknown type (only file actions).

A DOCX renamed to `.zip` stays a ZIP, because ZIP is among its candidates; archive tools apply.
Every generated fixture is detected correctly both with and without its extension.

### Output naming

`output_path(source, extension=, suffix=, directory=, reserved=)` never returns an existing
path (dangling symlinks count): `photo.jpg`, `photo (1).jpg`, ...; tools add a suffix
(`photo-cropped.jpg`). Compound extensions are kept (`backup.tar.gz`), an empty extension
names an output folder, and names are shortened to fit 255 bytes. `reserved` covers outputs
of the same batch that do not exist yet. Phase 5 adds the atomic rename into the final path.

### Config

`~/.config/quince/config.toml` (`$XDG_CONFIG_HOME` honored); `quince config` prints the
effective settings. A missing file means defaults and is not created on load. Every invalid
value falls back to its default with a logged warning; Quince never fails to start because of
the config. Saving writes a temporary file and renames it. The drag tracker now takes its
triggers and tap length from the config.

## Wheel component (Phase 3)

`src/quince/ui/wheel/` holds the wheel, independent of how it is opened.

| Module | Role |
|--------|------|
| `geometry.py` | Pure math: N equal slices in one ring, radius scaling, label boxes, hit-testing, screen clamping. |
| `state.py` | Pure logic: which slice is highlighted, by pointer or keyboard (X keysym names). |
| `model.py` | `WheelContent` / `WheelItem`, built from `Registry.query` results. |
| `render.py` | QPainter drawing: shadow, ring, highlighted sector, icons and labels, center text. |
| `window.py` | `WheelWindow`: placement, animations, mouse, keys, drag-over and drops. |
| `demo.py` | `quince wheel-demo`. |

### Geometry

- Slice 0 is centered at 12 o'clock; slices follow clockwise. Every slice spans 360/N degrees.
- The ring is always 88 px thick. The center disc is at least 64 px in radius. When N grows,
  the ring grows so each slice keeps at least 76 px of arc at the label radius. Up to N = 7
  the wheel has its base size (window 360 px); at N = 20 the window is about 630 px.
- Labels are elided to 90 % of the slice chord (max 112 px); icons shrink from 32 to 20 px,
  the font from 13 to 11 px.
- Hit-testing: the center disc and everything beyond the ring (plus 6 px of slop) are "no
  slice"; releasing or dropping there closes the wheel.
- The wheel center is clamped so the ring stays inside the available area of the screen under
  the pointer (multi-monitor aware). When the wheel switches to a larger N, the center may move.

### Pointer and keyboard

- `WheelState` holds the highlight. Arrows (Left/Up previous, Right/Down next, wrapping),
  Home/End, Enter/Space choose, Esc/Tab close.
- After an arrow key the wheel is in keyboard mode and ignores the pointer until it moves
  24 px away from where it was. GTK nudges the pointer by 1 px per arrow key during a drag
  (Phase 1 finding), so those nudges never steal the highlight.
- `WheelWindow.handle_key()` takes keysym names, so the Phase 4 controller can feed raw X11
  keys while the drag source holds the keyboard grab. Qt key events are mapped to the same
  names. The demo takes the keyboard with `grabKeyboard()` instead (XGrabKeyboard works on the
  override-redirect window when no drag is running).
- Drops are answered with Copy only (Hard Rule 5), like the Phase 1 overlay. A drop chooses the
  **highlighted** slice, not the one under the pointer, so Enter during a keyboard choice
  (GTK drops at the pointer) runs the slice picked with the arrows.

### Look and motion

- Colors come from `ui/theme/colors.py`: light and dark sets with an orange accent. "system"
  follows `QStyleHints.colorScheme()`, which Qt 6.10 reads correctly on GNOME 42 / X11, and
  repaints on `colorSchemeChanged`.
- Icons are freedesktop names resolved with `QIcon.fromTheme` (Yaru on Ubuntu). A missing icon
  falls back to the label's first letter in a circle.
- The center shows the wheel name and the highlighted item's full name (`Operation.title`,
  e.g. "JPEG image" for the JPG slice), or the wheel name alone, or the empty-state message.
  Three dots show which of Convert / Tools / Actions is open.
- Animations: open 170 ms (fade + scale from 0.86), close 110 ms, switch 200 ms (old contents
  fade and turn away while the new ones turn in). They can be turned off (`animations=False`,
  `--no-animations`), which tests use.

### Testing notes

- Geometry and state are unit tested for N = 0..20. Window behavior (keys, clicks, drags with
  Move proposed, switching, animations) runs under `QT_QPA_PLATFORM=offscreen`.
- The offscreen platform has no icon theme, so a test installs a tiny theme to cover the icon
  path. The keyboard grab and real Yaru icons were checked in Xvfb with XTest keys.

## Service and drag flow (Phase 4)

`quince service` (`src/quince/app.py`) runs without a main window: the X11 watcher, the wheel,
`DragWheelController` (`src/quince/ui/wheel/controller.py`) and a single-instance socket.

```
X11InputWatcher ── DragStarted ─▶ controller: new session
       │  asks the drag source for text/uri-list (XdndSelection)
       └──────── DragFiles ────▶ worker thread: detect types, query registry, build contents
TriggerTapped ─▶ open the wheel at the pointer (pending until contents arrive), nudge pointer
DragKey / DragCancel ─▶ wheel keys / close
WheelWindow ── drag_entered(paths) ─▶ fallback when the source did not answer
            ── chosen(wheel, id, paths) ─▶ operation_chosen(op, selection) ─▶ stub: log + toast
DragEnded ─▶ after 400 ms: release the wheel
```

### Knowing the files before the wheel opens

- When a drag starts, the watcher asks for `TARGETS` of `XdndSelection` from a small unmapped
  window, then for `text/uri-list`. Drag sources answer any client, not only the drop target.
  GTK3 (the Nautilus drag code) answers in about 2 ms, so the wheel usually opens with its
  final contents. A source whose `TARGETS` lack `text/uri-list` (a text drag from VS Code,
  a browser, an editor) carries no files: no wheel opens. A source that does not answer
  `TARGETS` is asked for `text/uri-list` directly.
- No answer within 1 s, a refusal or an `INCR` transfer: the wheel opens in a pending state
  ("Reading files…") and uses the paths from its own drag-enter event instead.
- An answer without local files (web links) means no wheel for this drag. A drag-enter
  without files closes a pending wheel. (Found in manual testing: a text drag from VS Code
  left the wheel stuck in "Reading files…", because the drag-enter without files was ignored
  silently.)
- Type detection and the registry query run on a worker thread (Hard Rule: no long work on the
  GUI thread). Config and dependency availability are read again for every drag, so changes
  apply without a restart.

### The pointer nudge

A newly mapped window under a still pointer is not the drop target yet: GTK only looks for a
target when the pointer moves. Without a move, Enter drops on the window that was under the
pointer before the wheel appeared — in real use a Nautilus folder, which would copy or move
the file there. Right after opening, the controller warps the pointer 1 px and back
(`QCursor.setPos`). Warping produces core motion events (not raw ones, so the drag tracker is
unaffected), GTK re-targets the wheel at once and sends XdndEnter with the data. Measured in
Xvfb: without the nudge, Enter dropped on no target; with it, on the wheel.

### One wheel per drag, and holding

- Once the wheel closed in a drag, further taps are ignored until the next drag.
- While a drag runs, a closed wheel stays mapped, paints nothing and answers every drop with
  Copy, ignoring it. This covers GTK's drop on Enter after a keyboard choice, and its drop on a
  right or middle click. It is released 400 ms after the drag ended.
- Drops on an open wheel are acted on after 80 ms, so a cancel click that arrives just after
  the drop still wins. The drop chooses the highlighted slice.

### Single instance

- A `QLocalServer` named `quince-<uid>` (user-only access). `quince service` first sends
  `ping`; if a service answers, it prints "already running" and exits. `quince service --stop`
  sends `quit`. `quince service --restart` sends `quit`, waits until `ping` gets no answer
  (up to 10 s), then runs `quince home`, which starts a fresh service and shows Home. A socket file left by a crashed service is removed when nobody answers.
- `QUINCE_INSTANCE` overrides the name; the nested-X tests use it so they never talk to a
  service running in the real session.
- Commands are one line per request. Phase 11 adds `open` for the CLI and the Nautilus menu.

### Testing notes

- `tests/integration/test_x11_drag.py` now runs the real service against the GTK3 source:
  registry contents at open, switching, Shift held at the drop (Copy), keyboard choice without
  moving the mouse (nudge), right/middle click, Esc, mixed selections, center drop, scroll,
  non-DnD button drags and the single-instance commands.
- `tests/ui/test_wheel_controller.py` covers the controller's state machine with a synchronous
  worker and no real pointer.

## Jobs and feedback (Phase 5)

### Job engine (`src/quince/core/jobs.py`)

- `JobQueue` runs `Operation.run(ctx)` on worker threads (2 by default) and reports a
  `JobSnapshot` on every change: queued → running (progress 0..1 or unknown, message) →
  succeeded (outputs) / failed (short reason) / cancelled.
- Outputs: `ctx.new_output(source, extension=, suffix=, directory=)` reserves the final name
  (collision-safe, shared across all running jobs) and returns a temporary path in the same
  folder, `.quince-tmp-<random>-<final name>`, which keeps the extension for tools such as
  ffmpeg that look at it. When `run` returns, every output is moved into place with
  `renameat2(RENAME_NOREPLACE)`: atomic and never over an existing file. If the name was taken
  meanwhile, the next free one is used. All outputs move or none do.
- Failure, cancellation and timeouts delete the temporary outputs: no partial files.
- Cancellation is cooperative: `ctx.check()` raises, and the process wrapper kills external
  programs. Timeouts (`timeout_s`) are turned into cancellation by a watchdog thread and
  reported as a failure ("Took longer than N s").
- Progress events are throttled to about 10 per second per job.
- Writing into a folder Quince cannot write to fails with "Cannot write to <folder>".

### External programs (`src/quince/backends/process.py`, `ffmpeg.py`)

- `process.run(cmd, ctx, timeout_s=, on_stdout=, on_stderr=)`: reads output line by line,
  keeps the last 40 lines of each stream, runs the program in its own process group so
  children die with it on cancel (SIGTERM, then SIGKILL after 3 s), and never passes
  `PYTHONPATH` on. A failure becomes `BackendError` with a short reason — the program name and
  its last stderr line — while the full stderr tail goes to the log.
- ffmpeg runs with `-nostdin -n -progress pipe:1 -nostats`; `progress_parser` turns
  `out_time_us` into a fraction of the duration from `ffprobe`.

### Feedback UI (`src/quince/ui/feedback/`)

- `JobsPanel`: a frameless, always-on-top stack of cards at the bottom-right of the screen
  under the pointer. Running cards show a progress bar (busy when unknown) and Cancel; success
  cards show the output name and "Show in Files" (8 s); error cards the short reason (20 s);
  cancelled cards 3 s. Every finished card has a close button.
- Cards have a fixed width; on every update a card sets its height from
  `layout.totalHeightForWidth()`, because `sizeHint()` ignores wrapped text (a card that grew
  from one running line to a result with notes used to clip them).
- "Show in Files" calls `org.freedesktop.FileManager1.ShowItems` asynchronously and falls back
  to opening the folder (`src/quince/integrations/files.py`). Phase 6's action reuses it.
- Operations without `run` still show the Phase 4 stub toast.

### Logging and environment

- `quince service` logs to the console and to `~/.local/state/quince/quince.log` (rotated at
  1 MB, 3 backups; `$XDG_STATE_HOME` honored).
- `requirements.lock` pins every Python package; `scripts/lock_deps.py` writes or checks it.
- `quince/__init__.py` strips `PYTHONPATH` entries from `sys.path` before any dependency is
  imported, and removes the variable for child processes.
- Found while testing: Qt timers created with a lambda outlive their widget; every delayed
  callback now passes its owner as the timer context.

### Trying it

`quince service --demo-ops` adds "Slow Demo" (6 s, writes `<name>-demo.txt`) and "Fail
Demo" (breaks halfway) to the Tools wheel. A nested-X test runs both from the wheel.


## File actions and the Float shelf (Phase 6)

### Three ways to run an operation

`Operation` has `run` (a background job), `editor` (Phase 13, see below) or, new in this phase,
`start(selection, host) -> JobRequest | None`: a step on the GUI thread. The file actions use
it because they ask the user something (a folder, an application) or touch the desktop
(clipboard, launching programs).

```
wheel chosen ──▶ OperationRunner.run(op, selection)            (src/quince/ui/runner.py)
                   ├─ op.run   → JobQueue.submit (at once)
                   ├─ op.start → controller.after_drag(...)  → op.start(selection, QtHost)
                   │                                            └─ JobRequest → JobQueue.submit
                   └─ neither  → "Not implemented yet" toast
```

- `after_drag` waits until the drag is over **and** the wheel is released. While Nautilus
  holds its pointer grab, a menu or dialog could not get the pointer, and the invisible
  (holding) wheel would sit on top of a dialog. After a keyboard choice this means: when the
  mouse button is released, plus the 400 ms grace period. A choice made by a drop needs no
  grace (the drop already happened), so the wheel is released at once.
- `Host` (a Protocol in `core/operations.py`, implemented by `ui/host.py`): `choose_folder`,
  `choose` (a menu at the pointer, with an optional sub-list), `set_clipboard_text`, `notify`,
  `show_in_files`, `float_items`. Actions never import Qt, so they are tested with a fake host.
- A `JobError` raised by `start` becomes an error toast: "Open in Terminal: No terminal
  program found".

### The actions (`src/quince/operations/actions/`)

| Action | What it does |
|--------|--------------|
| Copy / Move | Qt's folder dialog (recent destinations in the sidebar, "Copy Here" / "Move Here"), then a job |
| Copy Path | Paths on the clipboard, one per line; toast "Path copied" / "3 paths copied" |
| Show in Files | `FileManager1.ShowItems`, else the folder is opened (`integrations/files.py`) |
| Open With | Menu of apps for every selected type (default first) + "Other Applications" |
| Open in Terminal | The user's terminal in the folder (a file's parent folder); one window per folder, at most 5 |
| Float | Adds the items to the Float shelf |

All of them accept files, folders and mixed selections (`Accepts.anything()`).

### Copying and moving (`src/quince/backends/files.py`)

- **Copy** reserves the final names through `ctx.new_output(..., target_dir=dest)` and fills
  temporary entries; the job engine moves them in only when all are complete. Progress is by
  bytes (plus a small weight per entry). Files are copied in 8 MB chunks with
  `copy_file_range` (read/write when unsupported), checking for cancel between chunks; mode and
  times are kept (`copystat`), folders get their mode last so read-only folders can be filled.
- Checks before anything is written: source still exists, not a folder into itself, the
  destination is a writable folder, enough free space, no special files (FIFO, socket, device).
- **Move**, per item: same file system → `renameat2(RENAME_NOREPLACE)` to a free name. Other
  file system → copy to a temporary name first (cancellable); once all are copied, every item
  is put in place; then the originals are deleted. If placing fails, everything placed is
  moved back or removed. If deleting an original fails, the copy stays (nothing is lost) and
  the error says so. A rename that fails with `EXDEV` (bind mounts) falls back to copying.
  Items already in the destination are skipped. Moves report their results with
  `ctx.add_result()`, so the engine does not treat a late cancel as "cancelled".
- `jobs.remove_tree` makes read-only folders writable to delete a failed copy.

### Desktop data (`src/quince/backends/desktop.py`)

- Applications: `applications/*.desktop` in `$XDG_DATA_HOME` and `$XDG_DATA_DIRS` (first dir
  wins per desktop file id; subfolders become `kde-foo.desktop`). Listed only when `Type=
  Application`, not `Hidden`/`NoDisplay`, `OnlyShowIn`/`NotShowIn` match `XDG_CURRENT_DESKTOP`
  and `TryExec` exists. Names are localized from `LANG`.
- Associations: `mimeapps.list` and `<desktop>-mimeapps.list` in the config dirs, then the
  data dirs, then the legacy `defaults.list`. Like GIO, the default is the first installed
  explicit default, else the first added association; then the type's parents are tried.
- MIME types: Quince's own detection when it knows the format (content checked), else
  shared-mime-info globs (`globs2`: literal names, the longest matching extension, other
  globs, case-sensitive ones honored), else a text check. Aliases resolve to canonical names;
  `subclasses` gives parent types (`text/markdown` → `text/plain`), so text editors are offered
  for Markdown. Checked against `gio mime` on the dev machine.
- `Exec`: quoting per the spec; `%F`/`%U` take all files in one command, `%f`/`%u` start the
  app once per file; `%i %c %k %%` expanded, deprecated codes dropped. `Terminal=true` apps
  run inside the user's terminal.
- Programs start detached (`start_new_session`), with a reaper thread, and an environment
  without `PYTHONPATH`, `VIRTUAL_ENV`, the venv's `bin` on `PATH` and PySide's variables, so a
  Python app started from Quince never picks up Quince's interpreter.
- Terminal: GNOME's `default-applications.terminal exec` (gsettings), then
  `x-terminal-emulator`, then a list of known terminals. Symlinks are resolved to the real
  program (on the dev machine `x-terminal-emulator` → `terminator`); a table gives each one's
  working-directory and run-command arguments, and the folder is also the working directory.
  `quince doctor` prints the terminal found.

### The Float shelf (`src/quince/ui/shelf/`)

- `model.ShelfItems`: ordered, deduplicated absolute paths; pure and unit tested.
- `window.ShelfWindow`: frameless, translucent, always on top (`Tool`, managed by the window
  manager so it can be moved with `startSystemMove` from its title bar), shown without taking
  focus. It opens at the right edge of the screen under the pointer and keeps its position
  for the session.
- Drops are accepted as Copy. Dragging out (selected rows, or "Drag all") is a `QDrag` with
  `text/uri-list` and **only `CopyAction`** offered, so Nautilus copies even on the same file
  system. A drag out of the shelf is also a normal XDND drag, so a trigger tap opens the wheel
  for the shelf's items.
- Delete/Backspace removes, Clear empties, the close button empties and hides, double-click
  opens with `xdg-open`, the context menu has Open / Show in Files / Remove. Every 2 s items
  that no longer exist are dropped.

## Image conversion (Phase 7)

### From a Convert slice to a file

```
Registry (discover) ── convert package's make_run(target, routes) ──▶ Operation.run
run(ctx): for each selected file not already in the target format
            with ctx.item(file):                      # a failure here skips only this file
                route = routes[file.format]
                out = ctx.new_output(file, extension=target)
                BACKEND_MODULES[route.backend].convert(ctx, file, out, route)
```

- `src/quince/operations/convert/__init__.py` maps backend names to modules
  (`imaging` → `quince.backends.imaging`). A Convert operation whose routes all use backends
  that do not exist yet stays a stub; a single route without a backend fails with
  "DOCX → PDF is not available yet".
- Batches: `ctx.item()` drops the failed file's temporary outputs and records the reason. The
  job succeeds with the other outputs (`JobSnapshot.failures`, amber "!" card that lingers like
  an error card) and fails only when every file failed. `ctx.note()` adds remarks to the
  success card; files already in the target format are skipped with a note.

### The route table (`operations/convert/image.py`)

`RASTER = (jpg, png, webp, heic, tiff, avif, bmp, gif)`: every ordered pair (56 routes), each
→ PDF (8), SVG → each raster and PDF (9). HEIC routes also need `pillow-heif`, PDF routes
`pymupdf`, SVG routes `rsvg-convert`. A PNG offers 8 targets on the Convert wheel.

### The imaging backend (`backends/imaging.py`)

A `Target` table says what each format can store: alpha, grayscale, CMYK, 16-bit, color
profile, EXIF, DPI, maximum side, and the save options (JPG q90 optimized, WebP q90, HEIC q85,
AVIF q80, TIFF deflate). Per file:

1. Open (HEIC opener registered on first use; SVG rendered to PNG by `rsvg-convert` first).
   Unreadable, truncated or decompression-bomb images fail with a short reason.
2. Several frames: GIF/WebP targets keep the animation (frame durations, loop; GIF frames
   with transparency use disposal 2). Otherwise the first frame is used and a note says so;
   a multi-page TIFF into PDF keeps every page.
3. `ImageOps.exif_transpose`, then `prepare()` brings the pixels into a mode the target
   stores: palette/LA/1-bit/YCbCr → RGB(A); 16-bit scaled to 8 bits unless the target keeps
   16 bits (PNG, TIFF); CMYK → RGB through the embedded profile unless the target keeps CMYK
   (JPG, TIFF); fully opaque alpha dropped; real transparency kept or composited on white;
   for targets without a profile (GIF, BMP) the pixels are converted to sRGB.
4. Size limits (WebP 16383, JPG 65500, GIF 65535) are checked and explained.
5. Save with the profile, EXIF without the orientation tag (GPS kept), and DPI.

PDF output uses PyMuPDF: one page per image, page size from the DPI (96 when missing or
absurd). An upright JPEG is embedded byte for byte; lossless sources and anything with alpha
go in as PNG (alpha as a soft mask); other sources as JPEG q92. SVG → PDF is
`rsvg-convert -f pdf` and stays vector.

### Tests

- `tests/integration/test_image_routes.py`: every route on the generated fixtures (format
  detected, decodes, 64×48 kept, transparency kept or white with the note), a three-file
  batch, the animated GIF, and a decode of each target with gdk-pixbuf (Eye of GNOME's
  library) under the system Python for the loaders installed.
- `tests/unit/test_imaging.py`: orientation, EXIF/GPS, ICC, CMYK, 16-bit, palettes,
  multi-page TIFF, JPEG passthrough into PDF, size limits, unreadable files, partial batches.
- The nested-X wheel test drops two PNGs on the WebP slice and checks the `.webp` files.

## Audio and video conversion (Phase 8)

### Routes

- `operations/convert/audio.py`: every ordered pair of `AUDIO` (MP3, M4A, WAV, FLAC, OGG,
  Opus, AIFF, WMA): 56 routes.
- `operations/convert/video.py`: every pair of `VIDEO` (MP4, MOV, MKV, WebM, AVI, WMV), each
  video → GIF and → `SOUND` (MP3, M4A, WAV), animated GIF → MP4/MOV/MKV/WebM: 58 routes.
- All use backend `ffmpeg`, implemented by `backends/media.py` (registered in the convert
  package's `BACKEND_MODULES`).
- `Registry.query` lists Convert targets of the selection's own category first, so a video
  wheel reads MOV, MKV, WebM, AVI, WMV, GIF, MP3, M4A, WAV.

### Probe, plan, run

```
ffmpeg.probe(src)  → MediaInfo(streams, duration)      ffprobe -show_streams -show_format -of json
media.plan(info, source, target) → Plan(args, notes)   pure; unit tested on synthetic streams
ffmpeg -i src -map_metadata 0 <args> -f <muxer> <temporary output>
```

- `plan` decides per stream. Video targets: first real video track (cover art is not a video
  track), every audio track, subtitles (`copy` in MKV, `mov_text` in MP4/MOV, WebVTT in
  WebM, image-based subtitles only in MKV, none in AVI/WMV), MKV attachments. A stream whose
  codec is in the container's copy set is copied; the others are encoded with the
  container's preset (`VIDEO_TARGETS`, `AUDIO_CODECS`). HEVC copied into MP4/MOV gets the
  `hvc1` tag. Encoded video is rounded to even dimensions.
- Audio targets: the first audio track, copied when the codec matches (AAC from an MP4 into
  M4A is instant and lossless), else encoded; channel and sample-rate limits (MP3, WMA)
  downmix/resample with a note; WAV/AIFF are 24-bit only for sources with a stored depth over
  16 bits (lossy codecs decode to float and report no depth). Cover art (MJPEG/PNG) is kept
  in MP3, M4A and FLAC, otherwise a note says it was dropped. Tags are copied.
- GIF: `fps=min(15, source)`, longer side at most 480, `palettegen`/`paletteuse` in one
  filter graph. GIF → video: Pillow first checks that the GIF is animated; frames are put on
  white (`drawbox` fill + `overlay`) and encoded without sound.
- Notes on the card: "Converted without re-encoding (no quality loss)", "Downmixed to stereo",
  "Only the first audio track was kept", "Cover art was not kept", "Subtitles were not kept
  (…)".
- Errors carry no file name (the batch adds it): "clip.mp4: No sound to convert",
  "still.gif: Not animated; convert it to an image format instead", "x.mp3: Not a readable
  audio or video file". Phase 7's image errors were changed the same way.

### Progress in batches

`ctx.item(path, span)` maps a backend's 0..1 to that file's share of the job, so ffmpeg's
`-progress` output moves one bar from the first file to the last instead of restarting.

### Tests

- `tests/unit/test_media.py`: the planner's rules (copy vs encode, presets, limits, bit
  depth, cover art, tracks, subtitles, attachments, GIF filters).
- `tests/integration/test_media_routes.py`: all 114 routes on the fixtures (codec, track
  count, duration, even size, format detection, originals untouched, no temporary files),
  plus 24-bit/5.1 audio, cover art and tags, subtitles, odd sizes, a portrait video into GIF,
  transparent GIF, still GIF, a silent video, progress and cancel on a 40 s file, batch
  progress, and decoding every output with the installed GStreamer plugins (Totem's engine).
- The whole suite now takes about 100 s, half of it ffmpeg.

## Documents, slides and spreadsheets (Phase 9)

### Routes and backends

| Module | Routes | Backend |
|--------|--------|---------|
| `convert/document.py` | DOCX/DOC/ODT/RTF/TXT → DOCX, ODT, PDF; office → TXT, PNG, JPG; HTML → PDF | `libreoffice` (`backends/office.py`) |
| | DOCX/ODT/DOC/RTF → MD, HTML, EPUB; MD, HTML, EPUB → each other, DOCX, ODT, TXT, PDF | `pandoc` (`backends/pandoc.py`) |
| `convert/slides.py` | PPTX/PPT/ODP → PDF, PNG, JPG; PPTX ⇄ ODP; PPT → both | `libreoffice` |
| `convert/spreadsheet.py` | XLSX/XLS/ODS/CSV/TSV → XLSX, ODS, CSV, TSV, PDF | `libreoffice` |
| | CSV ⇄ TSV | `table` (`backends/table.py`, Python) |

A backend may now define `convert_into(ctx, src, route, outputs)` instead of `convert(...)`:
`outputs.file()` or `outputs.folder()` allocates the result, so a backend can decide after
converting whether it needs a folder (pages, sheets, Markdown with images).

### LibreOffice (`backends/office.py`)

- `soffice -env:UserInstallation=<Quince profile> --headless --norestore --nolockcheck
  --nodefault --nologo [--infilter=…] --convert-to <ext>:<filter> --outdir <tmp> input.<ext>`.
  LibreOffice exits 0 even when it fails, so the result is "files in the out folder"; none
  means an error with its last "Error" line.
- Profiles: `~/.cache/quince/libreoffice/profile-0..3`, each guarded by an `flock` on
  `profile-N.lock`, so two jobs (or two Quince processes) never share one; the user's own
  profile is never touched, so their LibreOffice can stay open.
- The input is linked as `input.<ext>` in a private folder (odd names cannot become options);
  TXT/CSV/TSV are rewritten as UTF-8 first (`backends/textfiles.py`: UTF-8, else Windows-1254),
  CSV separators are sniffed, and semicolon files are read with Turkish number rules.
- PNG/JPG: the document becomes a PDF, PyMuPDF renders each page at 150 DPI.
- CSV/TSV from workbooks: the "all sheets" export option; one sheet → one file, more →
  `name/name-<Sheet>.csv`.

### pandoc (`backends/pandoc.py`)

- `pandoc -f <reader> -t <writer> … -o <out> <src>`, offline, with `--resource-path` at the
  source's folder for relative images. DOC/RTF are first converted to DOCX by LibreOffice;
  PDF goes Markdown/EPUB → ODT (pandoc) → PDF (LibreOffice).
- Markdown: `-t gfm-raw_html --wrap=none --lua-filter=clean_markdown.lua --extract-media=.`
  in a private folder, then `tidy_markdown` (drops `<!-- end list -->`) and `gather_media`
  (moves DOCX `media/`, ODT `Pictures/` and HTML data-URI images into `media/` and fixes the
  links). With images the result is the folder `name/` (`name.md`, `media/`), else `name.md`.
- HTML: `--standalone --self-contained` (one file). EPUB: `default_title.lua` uses the
  document's title or the file name. Other writers: `offline_images.lua` turns remote images
  into links and the card notes it.

### Offline (`backends/process.py`)

`run(..., offline=True)` prefixes `unshare -rn` (checked once): an unprivileged user and
network namespace with only a loopback device, so nothing can be fetched, whatever the
document links to. Proxy variables pointing at the discard port are set as well, and are
the only guard where namespaces are unavailable.

### Tests

- `tests/integration/test_markdown_golden.py`: `tests/fixtures/golden/rich.md` →
  (make_fixtures) DOCX by pandoc, DOCX/ODT/RTF/DOC by LibreOffice, HTML, EPUB → Markdown must
  equal the reviewed files in `tests/fixtures/golden/expected/`, plus explicit checks (no raw
  HTML, three list levels, one numbered list, table header, relative image, Turkish letters).
  `QUINCE_UPDATE_GOLDEN=1` rewrites them. Documents in `tests/fixtures/private/` are checked
  for the basics.
- `tests/integration/test_document_routes.py`: all 89 routes (format, text content, page
  images ≥ 300 px), slides → numbered images, sheets → one CSV each, Turkish Excel CSV,
  values kept CSV → TSV, Windows-1254 text, remote images never fetched, no network inside
  the namespace, a running LibreOffice, two conversions at once, broken files.
- `tests/unit/test_documents.py`: decoding, separators, CSV ⇄ TSV quoting, infilters, sheet
  names, the profile pool, Markdown tidying, media gathering, doctor's self-test rows.
- `quince doctor` converts a small DOCX (Turkish, nested list, table) to Markdown and reports
  the result.

## PDF, subtitles and archives (Phase 10)

### Routes

| Module | Routes | Backend |
|--------|--------|---------|
| `convert/pdf.py` | PDF → MD, TXT, PNG, JPG, DOCX, ODT, EPUB, HTML, PPTX, XLSX | `pdf` (`backends/pdf.py`) |
| `convert/subtitle.py` | SRT ⇄ VTT, both → TXT | `subtitles` (`backends/subtitles.py`) |
| `convert/archive.py` | ZIP, TAR, TAR.GZ, 7Z into each other; RAR → all four | `archive` (`backends/archive.py`) |

### PDF → Markdown (`backends/pdf.py`)

```
open (password / broken / empty → clear error)
 ├─ scanned pages (< 10 characters, images covering ≥ 50 % of the page)
 │    tessdata with tur + eng? no → stop: "Page 2 is scanned and needs OCR … sudo apt install …"
 │    yes → page.get_textpage_ocr(tur+eng, 300 DPI) → paragraphs from text blocks
 └─ text pages: pymupdf4llm.to_markdown(page, hdr_info=IdentifyHeaders(whole document),
      write_images → media/) → fix_links (the words under each link rectangle)
running_lines(doc) → strip_running_lines → join_pages → tidy_markdown → name.md [+ media/]
```

- **Running headers and footers** are found from positions, not from the Markdown text: a
  line in the top or bottom 12 % of the page that is a page number, or is set smaller than
  the body text (the most common size) and repeats on at least half of the pages. Only the
  first and last three lines of a page's Markdown may be removed, and a Markdown line is
  removed only when it consists of such pieces ("CH2 Basic Physics … 10"). Body-sized text
  is never taken: an earlier version lost repeated body paragraphs, and a two-page document
  whose pages start alike — the tests keep both cases.
- **tidy_markdown**: drops empty headings, `<br>` in table cells, emphasis inside headings;
  renumbers heading levels so the top one is `#` (slides came out as `#####`); indents lists
  by four spaces per level and removes blank lines inside a list (a bullet list followed by a
  numbered list stays two lists); joins the lines of a paragraph and mends hyphenated words;
  puts blank lines around headings.
- **join_pages**: a page that ends without a sentence end, followed by one that starts in
  lower case, continues the paragraph.
- pymupdf4llm prints an advertisement on import. It is imported once under a lock with
  stdout redirected; `redirect_stdout` swaps `sys.stdout` for the whole process, so it is
  never used around conversions (it swallowed other threads' output during development).
- Checked on the user's private PDFs (`tests/fixtures/private/`): a course syllabus (tables,
  lists, bold), 38 lecture slides (images, running footers) and a 32-slide deck with formulas
  (sub/superscripts are a known limit, see `docs/FORMATS.md`).

### Other PDF targets

- TXT: page text (`sort=True`), OCR for scans, running lines removed, pages joined.
- PNG/JPG: `office.render_pages(dpi=300)`, a folder `name/name-01.png` for several pages.
- DOCX/ODT/EPUB: the Markdown (with `media/`) through pandoc (`-f gfm`), offline.
- PPTX: every page rendered to JPEG and placed full-size on a slide of a generated flat ODP
  (`office:binary-data`), which LibreOffice saves as PPTX.
- XLSX: `page.find_tables()` (ruled tables) → a generated flat ODS, one sheet per table
  ("Page 3", "Page 3 (2)"), plain numbers as numbers → LibreOffice → XLSX.

### Subtitles (`backends/subtitles.py`)

pysubs2 reads the text decoded by `textfiles` (UTF-8 or Windows-1254) and writes SRT or VTT in
UTF-8; TXT is the cues' plain text, one paragraph each. An empty file is an error.

### Archives (`backends/archive.py`)

Unpack into a private temporary folder, check, pack in the target format:
- ZIP: every name checked first (`check_name`: no absolute paths, drive letters or `..`),
  encrypted entries refused, size checked against free space; symlinks restored, modes and
  times kept. Packing uses `strict_timestamps=False` (ZIP cannot store times before 1980).
- TAR/TAR.GZ: every member through `tarfile.data_filter` before extraction (absolute paths,
  `..`, links outside, device files are refused), then `extractall(filter="data")`.
- 7Z/RAR: the listing (`7z l -slt`, `unrar lta`) is checked first, then extracted with
  `7z x` / `unrar x`.
- Afterwards `check_tree` refuses any symlink resolving outside the folder (a second line of
  defence: with the name checks disabled, it still caught the link cases in a mutation test).
- An unsafe archive fails as a whole ("The archive contains an unsafe path (../evil.txt);
  nothing was extracted"); nothing is written next to it.

### Tests

- `tests/integration/test_pdf_golden.py`: `rich.pdf` (two pages, header, footer "Sayfa N / M")
  and `columns.pdf` against reviewed Markdown; a scanned page without OCR is explained and
  leaves no file; the OCR test runs once Tesseract is installed; private PDFs are checked for
  the basics.
- `tests/integration/test_pdf_subtitle_archive_routes.py`: every route, page images at
  300 DPI, PPTX slide count, XLSX table values, locked PDFs, Windows-1254 subtitles, seven
  malicious archives, links and modes kept, encrypted ZIP, broken archives, old timestamps.
- `tests/unit/test_pdf_logic.py`: the clean-up rules, running-line detection on generated
  PDFs, link cutting, scan detection, OCR messages, flat ODS/ODP, archive name checks.
- `quince doctor` converts a generated PDF (heading, bold, list, Turkish) and reports whether
  OCR is ready.

## Home, Library and Nautilus integration (Phase 11)

### Entry points

Everything runs in the service process (`quince service`); other `quince` commands and the
Nautilus extension talk to it over the single-instance socket and start it first when it is
not running (`app.ensure_service`: spawn `python -m quince service` in a new session without
`PYTHONPATH`, then poll `ping` for up to 15 s; an early exit is reported with the log path).

| Entry | Command on the socket | Result |
|-------|-----------------------|--------|
| App menu "Quince", `quince home`, tray "Open Quince" | `home` | the window on Home |
| `quince library`, desktop action, tray "Library", Ctrl+F | `library` | the window on Library |
| Nautilus "Open in Quince", `quince open [--wheel W] PATH...` | `open {"paths": [...], "wheel": W}` | a wheel at the pointer |
| desktop action / tray "Quit Quince", `quince service --stop` | `quit` | the service stops |

The socket protocol is one line per request, `<command>[ <argument>]`; a handler gets the
argument and returns a one-line answer (`ok`, `error: ...`). A handler that raises is
answered with `error: ...` instead of breaking the service.

### Wheels without a drag (`ui/wheel/direct.py`)

The drag controller and `DirectWheel` share the single `WheelWindow`. `open_at(owner=...)`
records who opened it; each side ignores the wheel's `chosen` / `closed` / drag-enter signals
while it is not the owner. A drag starting closes a direct wheel, and a direct wheel is not
opened while a drag session owns the wheel (`controller.in_drag_session`).

- Types are detected and the registry queried on a worker thread, like for drags.
- The wheel opens with the requested wheel, else the first with items: Convert, Tools,
  Actions (folders and mixed selections therefore open on Actions).
- `open_at(grab=True)` takes the keyboard and the pointer (and repeats the grab after 60 ms,
  in case the window was not viewable yet). Keys arrive as Qt events: arrows / Enter / Esc as
  in the drag flow, a click on a slice chooses, a click anywhere else closes.
- Trigger taps come from `input/taps.py` (`TapDetector`): the same rules as during a drag
  (no other key or button in between, at most `max_tap_ms`), fed with keysym names from
  `ui/keys.py` (`QKeyEvent.nativeVirtualKey()` is the X keysym on xcb).
- The chosen operation is emitted only after the wheel closed and released its grabs, so a
  `start` step can open a dialog right away (`OperationRunner.after_drag` runs at once when no
  drag session is active).

### Home and Library (`ui/home/`, `ui/library/`)

One window (`MainWindow`) with two tabs. Esc / Ctrl+W hide it; the service keeps running.

- Home: a drop area (drops answered as Copy; click = Open Files), "Files" for this session
  (Enter or a trigger tap opens a wheel for the selected files at the current row, Delete
  removes from the list) and "Freshly made" (the 8 newest Library outputs that still exist).
  Ctrl+O opens files, Ctrl+V pastes (`paste.py`): file references from a file manager
  (`x-special/gnome-copied-files`, `text/uri-list`, or text that is only existing paths) are
  used in place; an image becomes PNG and other text UTF-8 `.txt` in
  `~/.local/share/quince/pasted/` (`pasted-2026-11-05-143210.png`, never over a file).
- Library: search as you type (every word must occur in the name, folder, "Made with" or a
  source name), Enter / "Use in Quince" opens a wheel for the selected outputs, Show in Files,
  Remove from Library (forgets, never deletes), Clear Library (asks first). Missing files are
  shown dimmed and italic. Both lists drag out with Copy only.

### History (`core/history.py`)

SQLite in `~/.local/share/quince/history.sqlite3`, one row per output: path, operation id,
job title ("Convert to JPG"), sources (JSON), time. `LibraryModel` records each successful
job of a Convert or Tool operation from the job events (file actions make nothing new) and
signals the pages. Only the newest 5000 rows are kept. A file that is not a database (or one
from a newer schema) is moved aside as `history.sqlite3.broken-<time>` and a new one started;
an unwritable location means an empty Library, never a crash. Outputs made before Phase 11
were not recorded and cannot appear.

### Desktop integration (`integrations/install.py`, `quince integrate`)

Writes into the user's `~/.local/share` (no root; the `.deb` of Phase 17 installs the same
files system-wide):
- `applications/quince.desktop`: "Quince" (`Exec=<venv python> -m quince home`) with the
  actions Library and Quit Quince; `StartupWMClass=quince` matches the window.
- `icons/hicolor/scalable/apps/quince.svg`: a provisional icon (Phase 17 finalizes it); the
  same file is the window and tray icon.
- `nautilus-python/extensions/quince_nautilus.py`: a `MenuProvider` with "Open in Quince" for
  local files and folders. It runs inside Nautilus (system Python, `python3-nautilus`), never
  imports Quince, and starts `quince open -- PATH...` with the command written in at install
  time, without `PYTHONPATH`. It supports the nautilus-python API 3.0 (Nautilus 42) and 4.0.
  Non-local items (trash, network locations without a path) get no menu entry.

`quince integrate --remove` deletes the three files.

### Tray (`ui/tray.py`)

`QSystemTrayIcon` with Open Quince, Library and Quit Quince. Qt 6 shows it as a
StatusNotifierItem, which Ubuntu's AppIndicator extension (on by default) displays. Without a
tray the service logs it and runs on; the app menu entry covers the same commands.

## Direct tools (Phase 12)

### Tools on the wheels

| Wheel item | Module | Accepts | How |
|------------|--------|---------|-----|
| Compress | `tools/compress.py` → `backends/compress.py` | JPG, PNG, WebP, HEIC, AVIF, TIFF, GIF; MP3, M4A, OGG, Opus, WMA; videos; PDF | options dialog, then a job |
| Metadata | `tools/metadata.py` → `backends/metadata.py` | the images above, audio, video, PDF | report window; "Save copy without metadata" → job |
| Remove Audio | `tools/remove_audio.py` → `backends/mediatools.py` | videos | job (stream copy) |
| Normalize | `tools/normalize_audio.py` → `backends/mediatools.py` | audio, videos | job (two-pass loudnorm) |
| Extract | `tools/extract_archive.py` → `backends/archive.py` | archives (7Z needs `7z`, RAR `unrar`) | job |
| Join | `tools/join_videos.py` → `backends/mediatools.py` | ≥ 2 videos | order dialog, then a job |
| Merge PDFs | `tools/merge_pdfs.py` → `backends/pdftools.py` | ≥ 2 PDFs | order dialog, then a job |
| To One PDF | `tools/images_to_pdf.py` → `backends/pdftools.py` | ≥ 2 raster images | order dialog, then a job |
| Read QR | `tools/read_qr.py` → `backends/qr.py` | raster images (needs `zbarimg`) | report window, nothing written |
| Archive (Actions wheel) | `actions/create_archive.py` → `backends/archive.py` | anything, folders too | options dialog, then a job |

Create archive is an action so that folders and mixed selections, which only get the Actions
wheel (Hard Rule 9), can be archived (Decision Log 2026-11-05).

### Dialogs through `Host`

`Operation.start` steps use four new `Host` methods (`core/operations.py`); `QtHost`
implements them with `ui/dialogs/`:

- `ask_options(form)`: a `core/forms.Form` (fields `Choice`, `Number`, `Text`; `shown_when`
  hides fields that do not apply) as a small dialog, prefilled with the values used last time
  for that form id. They are state in `~/.local/state/quince/tool-options.json`; `remember`
  limits which fields are kept (an archive name is not). Enter accepts, Esc cancels.
- `ask_order(title, paths)`: the files in natural name order (2 before 10); drag, Alt+↑/↓,
  "Sort by Name", "Leave Out" (at least two stay).
- `compute(message, work)`: runs `work` on a thread while a local event loop keeps the UI
  painting (user input is held back); the message shows only if it takes over 0.6 s.
  Metadata and QR reading use it, so a slow ffprobe never freezes the GUI thread.
- `show_report(report)`: sections of (name, value) rows, Ctrl+C / Copy All, extra buttons
  whose key is returned.

### Dependencies per format

`Operation.requires_by_format` (e.g. `{"pdf": ("gs",)}`) adds dependencies only for those
source formats: without Ghostscript, Compress is hidden for PDFs but offered for images.
`Registry.unavailable()` lists such gaps as "Compress (PDF)".

### Compress (`backends/compress.py`)

- A goal is a quality level (high / medium / low), a percentage of the original, or a size per
  file. A result that is not smaller than the original is never kept ("Already well
  compressed"); an unreachable size fails with the smallest size Quince could make.
- Images: settings are tried from the best down a ladder (JPEG/WebP/HEIC/AVIF quality 92 → 25,
  then 85 % → 20 % of the pixels; PNG lossless, then 256 → 16 colors; GIF colors; TIFF
  deflate, then JPEG-in-TIFF), with a binary search over the ladder (about five encodes).
  Multi-frame images are refused for now. EXIF, ICC and DPI are kept as in conversions.
- PDF: Ghostscript `pdfwrite` with `/printer`, `/ebook`, `/screen`, then 50 dpi images.
- Audio: the same codec at a lower bit rate; a size goal computes the bit rate from the
  duration and retries (at most three encodes) when the container overhead overshoots.
- Video: H.264 CRF 23/27/31 (VP9 33/38/44 for WebM); a size goal encodes two passes at the
  computed bit rate (audio 64-128 kbps), scaled to at most 480/720/1080 lines when the bit rate
  is low. Both passes use `-vsync vfr`: otherwise the null muxer of pass 1 and the MP4 muxer of
  pass 2 keep different frames and x264 fails with "Incomplete MB-tree stats file" (seen with
  an AVI source). AVI and WMV are written as MP4.

### Metadata (`backends/metadata.py`)

- Read: EXIF (names from Pillow, GPS as decimal degrees), XMP, comments, PNG text, IPTC
  presence; ffprobe container, stream and chapter tags; PDF document information and XMP. The
  window separates what the file is (format, size) from its metadata.
- Strip without re-encoding: JPEG segments are copied except APP1 (EXIF/XMP), APP13 (IPTC),
  comments and APPn other than JFIF, ICC and Adobe; a lone EXIF orientation is written back so
  the photo does not turn; data after the end-of-image marker (MPF extra images, which carry
  their own EXIF) is dropped. PNG drops `tEXt`, `zTXt`, `iTXt`, `eXIf`, `tIME`; WebP drops the
  `EXIF` and `XMP ` chunks and clears their flags. TIFF/GIF are saved again (lossless),
  HEIC/AVIF re-encoded at quality 95 (noted). Media: `-map_metadata -1 -map_metadata:s -1
  -map_chapters -1 -c copy` with bit-exact flags. PDF: `set_metadata({})` and
  `del_xml_metadata()`.

### Audio and video tools (`backends/mediatools.py`)

- Normalize: one `loudnorm` measuring pass per audio track, then one pass with the measured
  values (`linear=true`) to -16 LUFS / -1.5 dBTP / LRA 11, every track with its own filter, at
  the original sample rate, encoded with the format's conversion preset (lossless stays
  lossless); a silent track is an error.
- Join: the concat demuxer with stream copy when container, codecs, size, frame rate and audio
  layout match; otherwise one `filter_complex` that scales and pads every video to the first
  one's size and frame rate, resamples sound to 48 kHz stereo and fills videos without sound
  with `aevalsrc` silence, encoded as MP4 (H.264 CRF 20, AAC 192k).

### Archives

- Extract unpacks into a temporary output folder next to the archive with the conversion
  checks; a single top-level item is moved out and takes its own name (the folder and its
  reserved name are given up first, `JobContext.discard_output`).
- Create archive stores each selected item under its own name (ZIP, TAR.GZ, 7Z with absolute
  paths, `-snl`); two items with the same name are refused.

### QR codes (`backends/qr.py`)

Pillow decodes the image (any raster format), flattens it on white and writes a grayscale PNG
for `zbarimg --quiet --xml`; the XML keeps multi-line text and base64 data. Exit status 4 is
"nothing found". The test fixture `sample-qr.png` comes from a small QR encoder in
`scripts/make_fixtures.py` (byte mode, level L, versions 1-5, mask 0); its Reed-Solomon and
format-bit parts are unit tested against published reference values.

## Route coverage, text and code (Phase 12)

### The audit (`core/coverage.py`, `scripts/audit_routes.py`)

Routes are a hand-written table, so the audit checks them against what the backends can do:

- `CAPABILITIES`: per backend (Pillow, ffmpeg, LibreOffice Writer/Calc/Impress, pandoc,
  PyMuPDF, pymupdf4llm, pysubs2, archives), the Quince formats it reads and writes in one step.
- `candidates(routes)`: the transitive closure of those one-step conversions plus the routes
  (`txt → docx → md` makes `txt → md` a candidate).
- `EXCLUSIONS`: conversions left out on purpose, as `sources × targets` with a reason (whole
  categories where possible). They are listed in `docs/FORMATS.md`.
- `tests/unit/test_coverage.py` fails when a candidate is neither a route nor excluded, when an
  exclusion covers a route, or when an exclusion covers nothing any more. A new format with
  any route therefore forces a decision about everything it can reach.

### Routes

| Module | Routes | Backend |
|--------|--------|---------|
| `convert/code.py` | every code format → MD, TXT, HTML, PDF | `text` (`backends/text.py`) |
| `convert/document.py` | TXT → MD, HTML, EPUB | `text` |
| `convert/spreadsheet.py` | XLSX/XLS/ODS/CSV/TSV → MD, HTML | `text` |
| `convert/slides.py` | PPTX/ODP/PPT → MD, TXT | `text` |

Source code and configuration files are formats of their own (category `code`, `Format.syntax`
is the fence/highlighter language), instead of falling back to TXT. A text extension is not
believed for binary content (`filetype.detect`: an MPEG-TS `clip.ts` is not TypeScript).

### The `text` backend

Quince writes the Markdown itself (`backends/markdown.py`) because pandoc 2.9's GFM writer
does not escape `|` in table cells: `escape_inline` / `escape_line` escape only what Markdown
would read as markup (round-trip tested through pandoc), `pipe_table` writes GFM tables
(`<br>` for line breaks in cells), `fence` picks a fence longer than any backtick run.
HTML (`--self-contained`), EPUB and PDF are made from that Markdown with pandoc; PDF goes
through DOCX and LibreOffice, since pandoc's ODT writer does not highlight code.

- Plain text: paragraphs at blank lines, every line break kept as a backslash hard break.
- Sheets: CSV/TSV are read in Python (encoding, separator); workbooks through LibreOffice's
  CSV export (values as shown). LibreOffice writes every sheet at once, so the order comes from
  `xl/workbook.xml` / `content.xml` (XLS is converted to XLSX first).
- Slides (`backends/slidetext.py`): PPTX and ODP are read with `zipfile` + ElementTree: slide
  order from `presentation.xml` and its relationships, title placeholders, body placeholders
  and lists as bullets with their level, tables, speaker notes. PPT goes through PPTX.

---

## Editors and image editors (Phase 13)

### Flow

```
wheel chosen ──▶ OperationRunner.run(op, selection)
                   └─ op.editor → controller.after_drag(...) → EditorManager.open(op, selection)
                        ├─ Editor.load(selection)      off the GUI thread (decoding)
                        ├─ window.present()            non-modal; several may be open
                        └─ Save → window.saved(JobRequest) → runner.submit_request → JobQueue
```

- `ui/editors/manager.py`: `discover()` finds every module in `quince.ui.editors.image` (more
  packages later) that defines `EDITOR`, keyed by its `name`; `Operation.editor` names it. A
  load failure (`JobError`) is an error toast; a missing editor name too.
- `ui/editors/base.py` `EditorWindow`: toolbar row (hidden when empty), canvas, optional side
  panel, bottom bar (hint, info, Original / Preview toggle, Cancel, Save). Shared keys: Enter /
  Ctrl+S save (Enter in a number field only applies it), Esc closes (asks "Discard your
  changes?" when something changed), Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y, `\` compare.
  `History` keeps immutable editor states; pushes with the same `merge` key within 0.8 s (a
  slider drag, arrow-key nudges) are one undo step. "Changed?" is `state != initial state`.
- An editor implements `load`, `state` / `set_state`, `build_job`, optionally `can_save`,
  `compare_image`, `remember` (settings kept via `forms.save_section` in
  `~/.local/state/quince/tool-options.json`).
- `ui/editors/canvas.py` `ImageCanvas`: shows a preview `QImage` stretched over the result's
  *logical* size (original pixels), fitted with a margin (at most 4x for tiny images), and hands
  mouse / key events to a `CanvasTool` in original-pixel coordinates. `rects.py` (pure box
  geometry: handles, move, resize with ratio, fit) and `boxes.py` (box gestures and drawing)
  are shared by Crop and Redact.

### Same function for preview and result

Editors describe the result as data and apply one function both to a preview (longer side
1600 px) and, in the job, to the full image:

| Editor | State | Function (backend) | Output |
|--------|-------|--------------------|--------|
| Crop | box (original px), ratio | `imageedit.crop` | `name-cropped` |
| Edit | `Adjust` (quarter turns, mirrors, size, brightness / contrast / saturation) | `imageedit.adjust` | `name-edited` |
| Frame | `FrameStyle` (padding, background, radius, shadow as fractions of the shorter side) | `imageedit.frame` | `name-framed` |
| Annotate | `Shape` list (pen, highlighter, line, arrow, box, ellipse, text) | `painting.annotate` (QPainter) | `name-annotated` |
| Redact | `Redaction` list (box + solid / blur / pixelate) | `imageedit.redact` | `name-redacted` |
| Collage | order, `CollageStyle`, width | `collage.render` | `first-collage` |

Sizes that must look alike at every scale are fractions (frame padding, blur strength, pixel
blocks per area), so the preview matches. Annotations are painted with QPainter on a `QImage`
in the job too (antialiased, same font as on screen); painting on a QImage is allowed off the
GUI thread.

### Saving (`imageedit.save_edited`)

Same format as the source; SVG → PNG (rendered with `rsvg-convert`); a transparent result whose
format has no alpha (a JPG framed on a transparent background) → PNG with a note. Animated GIF
/ WebP: every frame goes through the function. EXIF orientation is applied first; EXIF (without
orientation; Pillow never writes the IFD1 thumbnail) and the color profile are kept. Redact
drops EXIF (maker notes may hold a preview). Pixels are edited as 8-bit RGB(A).

