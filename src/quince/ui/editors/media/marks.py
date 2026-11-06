"""The marks of the media timeline (in / out points and markers) and moving the playhead.

Pure data and functions, no Qt, so the keyboard rules are unit tested.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

SEEK_S = 1.0  # ← / →
FINE_AUDIO_S = 0.1  # Shift+← / → in audio
DEFAULT_FPS = 30.0


@dataclass(frozen=True)
class Marks:
    """In and out points (None: the start / end of the file) and markers, in seconds."""

    in_s: float | None = None
    out_s: float | None = None
    markers: tuple[float, ...] = ()

    def span(self, duration: float) -> tuple[float, float]:
        """The marked part; the whole file without marks."""
        start = self.in_s if self.in_s is not None else 0.0
        end = self.out_s if self.out_s is not None else duration
        return start, end

    @property
    def has_range(self) -> bool:
        return self.in_s is not None or self.out_s is not None


def clamp(t: float, duration: float) -> float:
    return max(0.0, min(duration, t))


def fine_step(fps: float, video: bool) -> float:
    """Shift+← / →: one frame of a video, 0.1 s of a sound."""
    if not video:
        return FINE_AUDIO_S
    return 1.0 / (fps if fps > 0 else DEFAULT_FPS)


def set_in(marks: Marks, t: float) -> Marks:
    """In at ``t``; an out point at or before it is dropped."""
    out = marks.out_s if marks.out_s is not None and marks.out_s > t + 1e-6 else None
    return replace(marks, in_s=t if t > 1e-6 else None, out_s=out)


def set_out(marks: Marks, t: float, duration: float) -> Marks:
    """Out at ``t``; an in point at or after it is dropped."""
    start = marks.in_s if marks.in_s is not None and marks.in_s < t - 1e-6 else None
    return replace(marks, in_s=start, out_s=t if t < duration - 1e-6 else None)


def clear_range(marks: Marks) -> Marks:
    return replace(marks, in_s=None, out_s=None)


def toggle_marker(marks: Marks, t: float, tolerance: float) -> Marks:
    """Add a marker at ``t``, or remove the one already there (within ``tolerance``)."""
    near = [m for m in marks.markers if abs(m - t) <= tolerance]
    if near:
        return replace(marks, markers=tuple(m for m in marks.markers if m not in near))
    return replace(marks, markers=tuple(sorted((*marks.markers, t))))


def clock(t: float, *, precise: bool = True) -> str:
    """``1:02.35`` (``1:02:03.40`` past an hour); without ``precise``, whole seconds."""
    t = round(max(0.0, t), 2 if precise else 0)  # 59.999 shows as 1:00.00, not 0:60.00
    minutes, seconds = divmod(t, 60)
    hours, minutes = divmod(int(minutes), 60)
    sec = f"{seconds:05.2f}" if precise else f"{int(seconds):02d}"
    return f"{hours:d}:{minutes:02d}:{sec}" if hours else f"{minutes:d}:{sec}"


def length_text(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} s"
    return clock(seconds, precise=False)
