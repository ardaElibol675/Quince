"""Job feedback: a stack of borderless cards in the corner of the screen.

A running job shows its progress and a Cancel button. A finished one turns into a success
card with "Show in Files", an error card with the short reason, or a short "Cancelled"
note; each disappears by itself after a while or with its close button.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from quince.core.jobs import JobSnapshot, JobState
from quince.integrations.files import show_in_files
from quince.ui.theme.colors import WheelColors, wheel_colors

CARD_WIDTH = 340
SCREEN_MARGIN = 24
SUCCESS_MS = 8000
ERROR_MS = 20000
CANCELLED_MS = 3000
ERROR_COLOR = "#d64545"
SUCCESS_COLOR = "#3a9b5c"
PARTIAL_COLOR = "#d9822b"
MAX_FAILURE_LINES = 3


class JobBridge(QObject):
    """Carries job events from worker threads to the GUI thread (queued)."""

    event = Signal(object)


def _rgba(color) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"


def _names(paths: Sequence[Path], limit: int = 2) -> str:
    names = [p.name for p in paths]
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f" +{len(names) - limit} more"


def _remarks(s: JobSnapshot) -> list[str]:
    lines = list(s.notes)
    if s.failures:
        count = len(s.failures)
        lines.append(f"{count} file{'s' if count != 1 else ''} failed:")
        lines += s.failures[:MAX_FAILURE_LINES]
        if len(s.failures) > MAX_FAILURE_LINES:
            lines.append(f"+{len(s.failures) - MAX_FAILURE_LINES} more (see the log)")
    return lines


class JobCard(QFrame):
    cancel_clicked = Signal(int)
    dismissed = Signal(int)

    def __init__(
        self,
        snapshot: JobSnapshot,
        colors: WheelColors,
        show_files: Callable[[Sequence[Path]], None],
    ) -> None:
        super().__init__()
        self.job_id = snapshot.id
        self.snapshot = snapshot
        self._show_files = show_files
        self.setObjectName("card")
        self.setFixedWidth(CARD_WIDTH)
        self.setStyleSheet(
            f"""
            #card {{ background: {_rgba(colors.ring)};
                     border: 1px solid {_rgba(colors.ring_border)}; border-radius: 12px; }}
            QLabel {{ color: {_rgba(colors.text)}; background: transparent; }}
            QLabel#detail {{ color: {_rgba(colors.text_dim)}; }}
            QPushButton {{ color: {_rgba(colors.text)}; background: transparent;
                           border: 1px solid {_rgba(colors.ring_border)}; border-radius: 6px;
                           padding: 3px 10px; }}
            QPushButton:hover {{ background: {_rgba(colors.separator)}; }}
            QPushButton#close {{ border: none; padding: 0 4px; font-size: 15px; }}
            QProgressBar {{ background: {_rgba(colors.separator)}; border: none; border-radius: 2px;
                            max-height: 4px; }}
            QProgressBar::chunk {{ background: {_rgba(colors.accent)}; border-radius: 2px; }}
            """
        )

        self.status = QLabel()
        self.title = QLabel(snapshot.title)
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.close_button = QPushButton("\u00d7")  # multiplication sign as a close glyph
        self.close_button.setObjectName("close")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(lambda: self.dismissed.emit(self.job_id))
        top = QHBoxLayout()
        top.addWidget(self.status)
        top.addWidget(self.title, 1)
        top.addWidget(self.close_button)

        self.detail = QLabel()
        self.detail.setObjectName("detail")
        self.detail.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(lambda: self.cancel_clicked.emit(self.job_id))
        self.files_button = QPushButton("Show in Files")
        self.files_button.clicked.connect(lambda: self._show_files(self.snapshot.outputs))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.files_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(6)
        layout.addLayout(top)
        layout.addWidget(self.detail)
        layout.addWidget(self.bar)
        layout.addLayout(buttons)
        self.update_snapshot(snapshot)

    def update_snapshot(self, s: JobSnapshot) -> None:
        self.snapshot = s
        running = not s.state.finished
        self.bar.setVisible(running)
        self.cancel_button.setVisible(running)
        self.close_button.setVisible(not running)
        self.files_button.setVisible(s.state is JobState.SUCCEEDED and bool(s.outputs))
        if running:
            self.status.setText("…")
            if s.progress is None:
                self.bar.setRange(0, 0)  # busy indicator
            else:
                self.bar.setRange(0, 1000)
                self.bar.setValue(round(s.progress * 1000))
            detail = s.message or _names(s.sources)
        elif s.state is JobState.SUCCEEDED:
            color = PARTIAL_COLOR if s.partial else SUCCESS_COLOR
            self.status.setText(f"<span style='color:{color}'>{'!' if s.partial else '✓'}</span>")
            detail = "\n".join([f"→ {_names(s.outputs)}" if s.outputs else "Done", *_remarks(s)])
        elif s.state is JobState.FAILED:
            self.status.setText(f"<span style='color:{ERROR_COLOR}'>✕</span>")
            detail = s.error or "Failed"
        else:
            self.status.setText("\u2013")
            detail = "Cancelled"
        self.detail.setText(detail)


class JobsPanel(QWidget):
    """The card stack; anchored to the bottom-right corner of the screen under the pointer."""

    cancel_requested = Signal(int)

    def __init__(
        self,
        *,
        scheme: str = "system",
        success_ms: int = SUCCESS_MS,
        error_ms: int = ERROR_MS,
        cancelled_ms: int = CANCELLED_MS,
        show_files: Callable[[Sequence[Path]], None] = show_in_files,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.X11BypassWindowManagerHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.scheme = scheme
        self._show_files = show_files
        self._linger = {
            JobState.SUCCEEDED: success_ms,
            JobState.FAILED: error_ms,
            JobState.CANCELLED: cancelled_ms,
        }
        self.cards: dict[int, JobCard] = {}
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self._corner: QPoint | None = None

    def on_event(self, snapshot: JobSnapshot) -> None:
        card = self.cards.get(snapshot.id)
        if card is None:
            card = JobCard(snapshot, wheel_colors(self.scheme), self._show_files)
            card.cancel_clicked.connect(self.cancel_requested)
            card.dismissed.connect(self.remove)
            self.cards[snapshot.id] = card
            self._layout.addWidget(card)
            card.show()  # Qt would show it only on the next event loop pass
        else:
            card.update_snapshot(snapshot)
        if snapshot.state.finished:
            # The panel is the timer's context: no callback after the panel is gone.
            job_id = snapshot.id
            linger = self._linger[JobState.FAILED if snapshot.partial else snapshot.state]
            QTimer.singleShot(linger, self, lambda: self.remove(job_id))
        self._relayout()

    def remove(self, job_id: int) -> None:
        card = self.cards.pop(job_id, None)
        if card is None:
            return
        self._layout.removeWidget(card)
        card.deleteLater()
        self._relayout()

    def _relayout(self) -> None:
        if not self.cards:
            self.hide()
            self._corner = None
            return
        if self._corner is None or not self.isVisible():
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            area = screen.availableGeometry()
            self._corner = QPoint(area.right() - SCREEN_MARGIN, area.bottom() - SCREEN_MARGIN)
        self._layout.activate()  # apply added/removed cards now, not on the next event
        size = self._layout.sizeHint()
        self.setFixedSize(size)
        self.move(self._corner.x() - size.width(), self._corner.y() - size.height())
        self.show()
        self.raise_()
