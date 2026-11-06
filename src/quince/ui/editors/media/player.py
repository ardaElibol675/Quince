"""Playback for the media editors (QtMultimedia): the picture as ``QImage`` frames, the
position in seconds, and playing up to a time (Shift+Space plays the marked part)."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink

log = logging.getLogger(__name__)

TICK_MS = 30
MUTE_MS = 60  # muting waits this long after a seek (and checks again)
SEEK_QUIET_S = 0.15  # QtMultimedia's FFmpeg backend stalls when muted right after a seek


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
        self._muted = False
        self._last_seek = 0.0
        self._mute_timer = QTimer(self)
        self._mute_timer.setSingleShot(True)
        self._mute_timer.setInterval(MUTE_MS)
        self._mute_timer.timeout.connect(self._apply_mute)
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
        self._last_seek = time.monotonic()
        self._player.setPosition(round(self._wanted * 1000))

    def set_rate(self, rate: float) -> None:
        # Only on a real change: setting it again on a paused file can stall the next seek.
        if abs(self._player.playbackRate() - rate) > 1e-6:
            self._player.setPlaybackRate(rate)

    def set_muted(self, muted: bool) -> None:
        """Mute or unmute soon: never in the same moment as a seek (that can stall)."""
        self._muted = muted
        if self._audio.isMuted() != muted and not self._mute_timer.isActive():
            self._mute_timer.start()

    @property
    def muted(self) -> bool:
        return self._muted

    def _apply_mute(self) -> None:
        if time.monotonic() - self._last_seek < SEEK_QUIET_S:
            self._mute_timer.start()
            return
        if self._audio.isMuted() != self._muted:
            self._audio.setMuted(self._muted)

    def close(self) -> None:
        self._timer.stop()
        self._mute_timer.stop()
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
        # Not from inside the signal: it can come from within setPosition(), and calling it
        # again there can stall QtMultimedia's FFmpeg backend.
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            QTimer.singleShot(0, self, self._after_load)

    def _after_load(self) -> None:
        self._last_seek = time.monotonic()
        self._player.setPosition(round(self._wanted * 1000))
        if self._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState:
            self._player.pause()

    def _on_error(self, _error, message: str) -> None:
        log.warning("playback: %s", message)
        self.failed.emit(message)
