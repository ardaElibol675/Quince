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
  sends `quit`. A socket file left by a crashed service is removed when nobody answers.
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

`Operation` has `run` (a background job), `editor` (Phase 13) or, new in this phase,
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
