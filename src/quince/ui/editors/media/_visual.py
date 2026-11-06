"""Video, in the Sound Editor: the picture of the sound as an MP4 video (H.264 + AAC) — a
moving waveform, bars, a spectrum, or a still picture (the file's cover art or a chosen one).

Saving as MP4 makes the video with these settings (from the edited sound); saving as a sound
format ignores them. The preview shows the picture at the playhead; it is made by ffmpeg in
the background. Style, size and colors are remembered for next time.
"""

from __future__ import annotations

import contextlib
import tempfile
import threading
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QSizeF, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QButtonGroup, QFileDialog, QHBoxLayout, QLabel, QRadioButton

from quince.backends import mediaedit
from quince.backends.mediapipeline import Visual
from quince.core import forms
from quince.core.jobs import JobError
from quince.ui.editors.base import RGBA, Mode, Swatches, debounced, heading, side_panel, tool_button

REMEMBER_KEY = "audio.to_video"
BACKGROUNDS: list[tuple[str, RGBA]] = [
    ("Black", (0, 0, 0, 255)),
    ("Charcoal", (24, 24, 27, 255)),
    ("Navy", (16, 30, 58, 255)),
    ("White", (255, 255, 255, 255)),
]
COLORS: list[tuple[str, RGBA]] = [
    ("Orange", (240, 151, 60, 255)),
    ("White", (255, 255, 255, 255)),
    ("Sky", (90, 180, 255, 255)),
    ("Green", (90, 210, 130, 255)),
    ("Pink", (240, 100, 160, 255)),
]
DEFAULTS = {"style": "waveform", "size": "wide", "background": BACKGROUNDS[1][1],
            "color": COLORS[0][1]}  # fmt: skip
PICTURE_FILTER = "Pictures (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)"


def saved_settings() -> dict:
    data = {**DEFAULTS, **forms.load_section(REMEMBER_KEY)}
    if data["style"] not in mediaedit.VISUALS:
        data["style"] = DEFAULTS["style"]
    if data["size"] not in mediaedit.VIDEO_SIZES:
        data["size"] = DEFAULTS["size"]
    for key in ("background", "color"):
        try:
            color = tuple(int(v) for v in data[key])
            if len(color) != 4 or not all(0 <= v <= 255 for v in color):
                raise ValueError
            data[key] = color
        except (TypeError, ValueError):
            data[key] = DEFAULTS[key]
    return data


def saved_visual() -> Visual:
    data = saved_settings()
    return Visual(
        style=data["style"],
        size=mediaedit.VIDEO_SIZES[data["size"]][1],
        background=tuple(data["background"][:3]),
        color=tuple(data["color"][:3]),
    )


def _size_key(size: tuple[int, int]) -> str:
    return next((k for k, (_l, s) in mediaedit.VIDEO_SIZES.items() if s == tuple(size)), "wide")


class _Rendered(QObject):
    done = Signal(int, QImage, str)  # generation, picture, error


class VisualPart:
    def __init__(self, editor) -> None:
        self.editor = editor
        self._tmp = tempfile.TemporaryDirectory(prefix="quince-preview-")
        self._generation = 0
        self._busy = False
        self._pending = False
        self._rendered = _Rendered(editor)  # gone with the window
        self._rendered.done.connect(self._on_rendered)
        self._render_timer = debounced(editor, self._render, 200)
        panel, side = side_panel()
        self._side = side
        side.addWidget(heading("Style"))
        self.style_group = self._radios(dict(mediaedit.VISUALS))
        side.addSpacing(6)
        side.addWidget(heading("Size"))
        self.size_group = self._radios(
            {k: label for k, (label, _size) in mediaedit.VIDEO_SIZES.items()}
        )
        side.addSpacing(6)
        side.addWidget(heading("Background"))
        visual = editor.edit.visual
        self.background = Swatches(BACKGROUNDS, (*visual.background, 255))
        self.background.picked.connect(lambda c: self._change(background=tuple(c[:3])))
        side.addWidget(self.background)
        self.color_heading = heading("Color")
        side.addWidget(self.color_heading)
        self.color = Swatches(COLORS, (*visual.color, 255))
        self.color.picked.connect(lambda c: self._change(color=tuple(c[:3])))
        side.addWidget(self.color)
        side.addSpacing(6)
        side.addWidget(heading("Picture"))
        self.picture_label = QLabel()
        self.picture_label.setWordWrap(True)
        side.addWidget(self.picture_label)
        buttons = QHBoxLayout()
        self.choose_button = tool_button("Choose…", raised=True, tip="Choose a picture")
        self.choose_button.clicked.connect(self.choose_picture)
        buttons.addWidget(self.choose_button)
        self.cover_button = tool_button("Cover Art", raised=True, tip="Use the file's cover art")
        self.cover_button.clicked.connect(lambda: self._change(picture=None))
        self.cover_button.setEnabled(self.has_cover)
        buttons.addWidget(self.cover_button)
        side.addLayout(buttons)
        side.addSpacing(6)
        note = QLabel("Save as MP4 to make the video.")
        note.setWordWrap(True)
        note.setEnabled(False)
        side.addWidget(note)
        side.addStretch(1)
        self.mode = Mode(
            "video", "Video", icon="video-x-generic", tip="Turn the sound into a video",
            panel=panel, hint="The preview shows the picture at the playhead · Save as MP4",
        )  # fmt: skip
        self.sync()

    def _radios(self, choices: dict[str, str]) -> QButtonGroup:
        group = QButtonGroup(self._side.parentWidget())
        for key, label in choices.items():
            button = QRadioButton(label)
            button.setProperty("quince_key", key)
            group.addButton(button)
            self._side.addWidget(button)
        group.buttonClicked.connect(lambda _b: self._on_radio())
        return group

    @staticmethod
    def _checked(group: QButtonGroup) -> str:
        button = group.checkedButton()
        return button.property("quince_key") if button is not None else ""

    @property
    def has_cover(self) -> bool:
        return self.editor.source.info.cover is not None

    @property
    def visual(self) -> Visual:
        return self.editor.edit.visual

    def ready(self) -> bool:
        """A video can be made (a still picture needs a picture)."""
        visual = self.visual
        return visual.style != "still" or visual.picture is not None or self.has_cover

    def _change(self, **changes) -> None:
        self.editor.change(replace(self.editor.edit, visual=replace(self.visual, **changes)))

    def _on_radio(self) -> None:
        style = self._checked(self.style_group) or "waveform"
        size = mediaedit.VIDEO_SIZES[self._checked(self.size_group) or "wide"][1]
        self._change(style=style, size=size)

    def choose_picture(self) -> None:
        picture = self.visual.picture
        start = str(picture.parent if picture else self.editor.source.path.parent)
        path, _filter = QFileDialog.getOpenFileName(
            self.editor, "Choose a Picture", start, PICTURE_FILTER,
            options=QFileDialog.Option.DontUseNativeDialog,
        )  # fmt: skip
        if path:
            self._change(picture=Path(path), style="still")

    def remember(self) -> None:
        visual = self.visual
        forms.save_section(
            REMEMBER_KEY,
            {"style": visual.style, "size": _size_key(visual.size),
             "background": [*visual.background, 255], "color": [*visual.color, 255]},
        )  # fmt: skip

    def sync(self) -> None:
        visual = self.visual
        for group, key in (
            (self.style_group, visual.style),
            (self.size_group, _size_key(visual.size)),
        ):
            for button in group.buttons():
                button.setChecked(button.property("quince_key") == key)
        self.background.set_color((*visual.background, 255))
        self.color.set_color((*visual.color, 255))
        colored = visual.style in ("waveform", "bars")
        self.color.setEnabled(colored)
        self.color_heading.setEnabled(colored)
        self.background.setEnabled(visual.style != "spectrum")
        if visual.picture is not None:
            self.picture_label.setText(visual.picture.name)
        elif self.has_cover:
            self.picture_label.setText("The file's cover art")
        else:
            self.picture_label.setText("No cover art in this file; choose a picture")
        if self.editor.mode is self.mode:
            self.schedule()

    # --- preview ------------------------------------------------------------------------

    @property
    def view(self):
        return self.editor.canvas.picture

    def schedule(self) -> None:
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _render(self) -> None:
        if self._busy:
            self._pending = True
            return
        if not self.ready():
            self.view.set_image(QImage(), QSizeF(*self.visual.size))
            return
        self._busy = True
        self._generation += 1
        generation = self._generation
        v = self.visual
        args = (self.editor.source, v.style, v.size, v.background, v.color, v.picture,
                self.editor.position)  # fmt: skip
        out = Path(self._tmp.name) / f"preview-{generation}.png"
        signal = self._rendered.done

        def work() -> None:
            image, error = QImage(), ""
            try:
                mediaedit.visual_preview(*args, out)
                image = QImage(str(out))
            except JobError as exc:
                error = exc.reason
            except Exception as exc:
                error = str(exc) or type(exc).__name__
            finally:
                out.unlink(missing_ok=True)
            with contextlib.suppress(RuntimeError):  # the editor is gone
                signal.emit(generation, image, error)

        threading.Thread(target=work, name="quince-preview", daemon=True).start()

    def _on_rendered(self, generation: int, image: QImage, error: str) -> None:
        self._busy = False
        if error:
            self.editor.info.setText(f"No preview: {error}")
        elif not image.isNull() and generation == self._generation:
            self.view.set_image(image, QSizeF(*self.visual.size))
        if self._pending:
            self._pending = False
            self._render()

    def close(self) -> None:
        self._render_timer.stop()
        self._tmp.cleanup()
