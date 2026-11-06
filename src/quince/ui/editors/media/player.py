"""Playback for the media editors (QtMultimedia): the picture as ``QImage`` frames, the
position in seconds, and playing up to a time (Shift+Space plays the marked part)."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink

log = logging.getLogger(__name__)

TICK_MS = 30


class Player(QObject):
    frame = Signal(QImage)
    position_changed = Signal(float)  # while playing
    playing_changed = Signal(bool)
    failed = Signal(str)

    def __init__(self, path: Path, *, video: bool, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._sink: QVideoSink | None = None
        if video:
            self._sink = QVideoSink(self)
            self._player.setVideoSink(self._sink)
            self._sink.videoFrameChanged.connect(self._on_frame)
        self._stop_at: float | None = None
        self._wanted = 0.0  # where the playhead should be once the file is open
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.errorOccurred.connect(self._on_error)
        self._player.mediaStatusChanged.connect(self._on_status)
        self._player.setSource(QUrl.fromLocalFile(str(path)))

    @property
    def playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def position(self) -> float:
        return self._player.position() / 1000

    def play(self, until: float | None = None) -> None:
        self._stop_at = until
        self._player.play()

    def pause(self) -> None:
        self._stop_at = None
        self._player.pause()

    def seek(self, t: float) -> None:
        self._wanted = max(0.0, t)
        self._player.setPosition(round(self._wanted * 1000))

    def set_rate(self, rate: float) -> None:
        self._player.setPlaybackRate(rate)

    def set_muted(self, muted: bool) -> None:
        if self._audio.isMuted() != muted:
            self._audio.setMuted(muted)

    def close(self) -> None:
        self._timer.stop()
        self._player.stop()
        self._player.setSource(QUrl())

    # --- internals ----------------------------------------------------------------------

    def _on_frame(self, frame: QVideoFrame) -> None:
        if not frame.isValid():
            return
        image = frame.toImage()
        if not image.isNull():
            self.frame.emit(image)

    def _on_state(self, _state) -> None:
        playing = self.playing
        if playing:
            self._timer.start()
        else:
            self._timer.stop()
            self._stop_at = None
        self.playing_changed.emit(playing)

    def _tick(self) -> None:
        t = self.position()
        if self._stop_at is not None and t >= self._stop_at:
            stop = self._stop_at
            self.pause()
            self.seek(stop)
            t = stop
        self.position_changed.emit(t)

    def _on_status(self, status) -> None:
        # Paused (not stopped) shows the frame at every seek; a seek made before the file
        # was open is lost, so it is repeated.
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self._player.setPosition(round(self._wanted * 1000))
            if self._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState:
                self._player.pause()

    def _on_error(self, _error, message: str) -> None:
        log.warning("playback: %s", message)
        self.failed.emit(message)
