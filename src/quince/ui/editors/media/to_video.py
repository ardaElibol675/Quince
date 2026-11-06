"""Audio to video: turn a sound into an MP4 video (H.264 + AAC) with a moving waveform, bars,
a spectrum, or a still picture (the file's cover art or a chosen picture).

The preview shows the picture at the playhead; it is made by ffmpeg in the background with
the same settings as the video. Style, size and colors are remembered for next time.
"""

from __future__ import annotations

import contextlib
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSizeF, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QButtonGroup, QFileDialog, QHBoxLayout, QLabel, QRadioButton

from quince.backends import mediaedit
from quince.core import forms
from quince.core.formats import Category
from quince.core.jobs import JobError
from quince.core.operations import JobRequest
from quince.ui.editors.base import RGBA, Swatches, debounced, heading, tool_button
from quince.ui.editors.media._common import MediaEditor

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


class _Rendered(QObject):
    done = Signal(int, QImage, str)  # generation, picture, error


class AudioToVideoEditor(MediaEditor):
    name = "audio.to_video"
    title = "Audio to Video"
    save_label = "Make Video"
    categories = frozenset({Category.AUDIO})
    needs_audio = True
    wants_wave = True
    picture_canvas = True
    side_panel = True

    def __init__(self, selection, data, parent=None) -> None:
        self.settings = saved_settings()
        self.picture: Path | None = None
        super().__init__(selection, data, parent)
        self._tmp = tempfile.TemporaryDirectory(prefix="quince-preview-")
        self._generation = 0
        self._busy = False
        self._pending = False
        self._rendered = _Rendered(self)
        self._rendered.done.connect(self._on_rendered)
        self._render_timer = debounced(self, self._render, 200)

        self.side.addWidget(heading("Style"))
        self.style_group = self._radios(
            {k: v for k, v in mediaedit.VISUALS.items()}, self.settings["style"]
        )
        self.side.addSpacing(6)
        self.side.addWidget(heading("Size"))
        self.size_group = self._radios(
            {k: label for k, (label, _size) in mediaedit.VIDEO_SIZES.items()},
            self.settings["size"],
        )
        self.side.addSpacing(6)
        self.side.addWidget(heading("Background"))
        self.background = Swatches(BACKGROUNDS, self.settings["background"])
        self.background.picked.connect(lambda _c: self._changed())
        self.side.addWidget(self.background)
        self.color_heading = heading("Color")
        self.side.addWidget(self.color_heading)
        self.color = Swatches(COLORS, self.settings["color"])
        self.color.picked.connect(lambda _c: self._changed())
        self.side.addWidget(self.color)
        self.side.addSpacing(6)
        self.picture_heading = heading("Picture")
        self.side.addWidget(self.picture_heading)
        self.picture_label = QLabel()
        self.picture_label.setWordWrap(True)
        self.side.addWidget(self.picture_label)
        buttons = QHBoxLayout()
        self.choose_button = tool_button("Choose…", raised=True, tip="Choose a picture")
        self.choose_button.clicked.connect(self.choose_picture)
        buttons.addWidget(self.choose_button)
        self.cover_button = tool_button("Cover Art", raised=True, tip="Use the file's cover art")
        self.cover_button.clicked.connect(lambda: self.set_picture(None))
        self.cover_button.setEnabled(self.source.info.cover is not None)
        buttons.addWidget(self.cover_button)
        self.side.addLayout(buttons)
        self.side.addStretch(1)

        self.media_hint("The preview shows the picture at the playhead")
        self._refresh()
        self.start_history()

    def _radios(self, choices: dict[str, str], current: str) -> QButtonGroup:
        group = QButtonGroup(self)
        for key, label in choices.items():
            button = QRadioButton(label)
            button.setProperty("quince_key", key)
            button.setChecked(key == current)
            group.addButton(button)
            self.side.addWidget(button)
        group.buttonClicked.connect(lambda _b: self._changed())
        return group

    @staticmethod
    def _checked(group: QButtonGroup) -> str:
        button = group.checkedButton()
        return button.property("quince_key") if button is not None else ""

    # --- settings -----------------------------------------------------------------------

    @property
    def visual(self) -> str:
        return self._checked(self.style_group) or DEFAULTS["style"]

    @property
    def size_key(self) -> str:
        if not hasattr(self, "size_group"):
            return self.settings["size"]
        return self._checked(self.size_group) or DEFAULTS["size"]

    def picture_size(self) -> tuple[int, int]:
        return mediaedit.VIDEO_SIZES[self.size_key][1]

    def state(self) -> tuple:
        return (
            self.visual,
            self.size_key,
            self.background.color(),
            self.color.color(),
            str(self.picture) if self.picture else None,
        )

    def set_state(self, state: tuple) -> None:
        visual, size, background, color, picture = state
        for group, key in ((self.style_group, visual), (self.size_group, size)):
            for button in group.buttons():
                button.setChecked(button.property("quince_key") == key)
        self.background.set_color(background)
        self.color.set_color(color)
        self.picture = Path(picture) if picture else None
        self._refresh()

    def can_save(self) -> bool:
        return self.visual != "still" or self.picture is not None or self.has_cover

    @property
    def has_cover(self) -> bool:
        return self.source.info.cover is not None

    def remember(self) -> None:
        forms.save_section(
            REMEMBER_KEY,
            {"style": self.visual, "size": self.size_key,
             "background": list(self.background.color()), "color": list(self.color.color())},
        )  # fmt: skip

    def choose_picture(self) -> None:
        start = str(self.picture.parent if self.picture else self.source.path.parent)
        path, _filter = QFileDialog.getOpenFileName(
            self, "Choose a Picture", start, PICTURE_FILTER,
            options=QFileDialog.Option.DontUseNativeDialog,
        )  # fmt: skip
        if path:
            self.set_picture(Path(path))

    def set_picture(self, path: Path | None) -> None:
        self.picture = path
        if path is not None:
            for button in self.style_group.buttons():
                button.setChecked(button.property("quince_key") == "still")
        self._changed()

    def _changed(self) -> None:
        self._refresh()
        self.commit()

    def _refresh(self) -> None:
        visual = self.visual
        colored = visual in ("waveform", "bars")
        self.color.setEnabled(colored)
        self.color_heading.setEnabled(colored)
        self.background.setEnabled(visual != "spectrum")
        if self.picture is not None:
            self.picture_label.setText(self.picture.name)
        elif self.has_cover:
            self.picture_label.setText("The file's cover art")
        else:
            self.picture_label.setText("No cover art in this file; choose a picture")
        w, h = self.picture_size()
        self.info.setText(f"MP4 · {w} × {h}")
        self.canvas.set_image(self.canvas.image, QSizeF(w, h))
        self.update_buttons()
        self._render_timer.start()

    # --- preview ------------------------------------------------------------------------

    def on_position(self, t: float) -> None:
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _render(self) -> None:
        if self._busy:
            self._pending = True
            return
        if self.visual == "still" and self.picture is None and not self.has_cover:
            return
        self._busy = True
        self._generation += 1
        generation = self._generation
        args = (self.source, self.visual, self.picture_size(), self.background.color(),
                self.color.color(), self.picture, self.position)  # fmt: skip
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
            self.info.setText(f"No preview: {error}")
        elif not image.isNull() and generation == self._generation:
            self.canvas.set_image(image, QSizeF(*self.picture_size()))
        if self._pending:
            self._pending = False
            self._render()

    def closeEvent(self, event) -> None:
        super().closeEvent(event)
        if event.isAccepted():
            self._tmp.cleanup()

    def build_job(self) -> JobRequest:
        source, visual, size = self.source, self.visual, self.picture_size()
        background, color, picture = self.background.color(), self.color.color(), self.picture
        return self.job(
            lambda ctx, out: mediaedit.audio_to_video(
                ctx, source, out, visual, size, background, color, picture
            ),
            None,
            extension="mp4",
        )


EDITOR = AudioToVideoEditor
