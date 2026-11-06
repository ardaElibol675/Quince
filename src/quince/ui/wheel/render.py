"""Painting the wheel with QPainter. Geometry comes from ``geometry.layout``."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)

from quince.ui.theme.colors import WheelColors
from quince.ui.theme.icons import themed
from quince.ui.wheel.geometry import WheelLayout, point_on_circle
from quince.ui.wheel.model import WHEEL_TITLES, WheelContent

SHADOW_SPREAD = 18.0
HIGHLIGHT_INSET = 3.0
ICON_TEXT_GAP = 4.0


def _qt_angle(degrees: float) -> float:
    """Our angles run clockwise from 12 o'clock; Qt's counterclockwise from 3 o'clock."""
    return 90.0 - degrees


def _circle(radius: float) -> QRectF:
    return QRectF(-radius, -radius, 2 * radius, 2 * radius)


def ring_path(inner: float, outer: float) -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(_circle(outer))
    path.addEllipse(_circle(inner))
    path.setFillRule(Qt.FillRule.OddEvenFill)
    return path


def sector_path(inner: float, outer: float, start: float, span: float) -> QPainterPath:
    """An annular sector from ``start`` clockwise over ``span`` degrees."""
    if span >= 360:
        return ring_path(inner, outer)
    path = QPainterPath()
    path.moveTo(*point_on_circle(outer, start))
    path.arcTo(_circle(outer), _qt_angle(start), -span)
    path.lineTo(*point_on_circle(inner, start + span))
    path.arcTo(_circle(inner), _qt_angle(start + span), span)
    path.closeSubpath()
    return path


class WheelPainter:
    """Paints a wheel; caches icon pixmaps between frames."""

    def __init__(self) -> None:
        self._icons: dict[tuple[str, int, float], QPixmap | None] = {}

    def _icon(self, name: str, size: int, dpr: float) -> QPixmap | None:
        key = (name, size, dpr)
        if key not in self._icons:
            icon = themed(name)
            if icon.isNull():
                self._icons[key] = None
            else:
                pixmap = icon.pixmap(QSize(size, size), dpr)
                self._icons[key] = None if pixmap.isNull() else pixmap
        return self._icons[key]

    def paint(
        self,
        p: QPainter,
        center: QPointF,
        wheel: WheelLayout,
        content: WheelContent,
        highlighted: int | None,
        colors: WheelColors,
        *,
        opacity: float = 1.0,
        scale: float = 1.0,
        rotation: float = 0.0,
    ) -> None:
        if opacity <= 0:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setOpacity(opacity)
        p.translate(center)
        p.scale(scale, scale)

        self._shadow(p, wheel, colors)
        p.save()
        p.rotate(rotation)
        self._ring(p, wheel, highlighted, colors)
        self._labels(p, wheel, content, highlighted, colors)
        p.restore()
        self._center(p, wheel, content, highlighted, colors)
        p.restore()

    # --- parts --------------------------------------------------------------

    def _shadow(self, p: QPainter, wheel: WheelLayout, colors: WheelColors) -> None:
        radius = wheel.outer_radius + SHADOW_SPREAD
        gradient = QRadialGradient(QPointF(0, 4), radius)
        transparent = QColor(colors.shadow)
        transparent.setAlpha(0)
        gradient.setColorAt(0.0, colors.shadow)
        gradient.setColorAt((wheel.outer_radius - 6) / radius, colors.shadow)
        gradient.setColorAt(1.0, transparent)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(gradient)
        p.drawEllipse(_circle(radius).translated(0, 4))

    def _ring(
        self, p: QPainter, wheel: WheelLayout, highlighted: int | None, colors: WheelColors
    ) -> None:
        inner, outer = wheel.inner_radius, wheel.outer_radius
        p.setPen(QPen(colors.ring_border, 1.0))
        p.setBrush(colors.ring)
        p.drawPath(ring_path(inner, outer))

        if wheel.count >= 2:
            p.setPen(QPen(colors.separator, 1.0))
            for s in wheel.slices:
                p.drawLine(
                    QPointF(*point_on_circle(inner + 6, s.start)),
                    QPointF(*point_on_circle(outer - 6, s.start)),
                )

        if highlighted is not None and 0 <= highlighted < wheel.count:
            s = wheel.slices[highlighted]
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colors.accent)
            p.drawPath(
                sector_path(inner + HIGHLIGHT_INSET, outer - HIGHLIGHT_INSET, s.start, s.span)
            )

    def _labels(
        self,
        p: QPainter,
        wheel: WheelLayout,
        content: WheelContent,
        highlighted: int | None,
        colors: WheelColors,
    ) -> None:
        font = QFont(p.font())
        font.setPixelSize(wheel.font_px)
        p.setFont(font)
        metrics = QFontMetricsF(font)
        dpr = p.device().devicePixelRatioF() if p.device() else 1.0
        size = wheel.icon_size
        block = size + ICON_TEXT_GAP + metrics.height()

        for s, item in zip(wheel.slices, content.items, strict=False):
            active = s.index == highlighted
            top = s.label_y - block / 2
            icon_rect = QRectF(s.label_x - size / 2, top, size, size)
            pixmap = self._icon(item.icon, size, dpr)
            if pixmap is not None:
                p.drawPixmap(icon_rect.toRect(), pixmap)
            else:
                self._monogram(p, icon_rect, item.label, colors, active)

            text = metrics.elidedText(item.label, Qt.TextElideMode.ElideRight, s.label_width)
            text_rect = QRectF(
                s.label_x - s.label_width / 2,
                top + size + ICON_TEXT_GAP,
                s.label_width,
                metrics.height(),
            )
            p.setPen(colors.accent_text if active else colors.text)
            p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)

    def _monogram(
        self, p: QPainter, rect: QRectF, label: str, colors: WheelColors, active: bool
    ) -> None:
        """Fallback when the icon theme has no icon: the label's first letter in a circle."""
        fg = colors.accent_text if active else colors.text_dim
        p.save()
        p.setPen(QPen(fg, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect.adjusted(2, 2, -2, -2))
        font = QFont(p.font())
        font.setPixelSize(max(9, int(rect.height() * 0.45)))
        font.setBold(True)
        p.setFont(font)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label[:1].upper())
        p.restore()

    def _center(
        self,
        p: QPainter,
        wheel: WheelLayout,
        content: WheelContent,
        highlighted: int | None,
        colors: WheelColors,
    ) -> None:
        inner = wheel.inner_radius
        p.setPen(QPen(colors.ring_border, 1.0))
        p.setBrush(colors.center)
        p.drawEllipse(_circle(inner))

        width = inner * 1.55
        base = QFont(p.font())
        small = QFont(base)
        small.setPixelSize(11)
        big = QFont(base)
        big.setPixelSize(14 if inner < 90 else 16)
        big.setBold(True)
        flags = Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap

        item = None
        if highlighted is not None and 0 <= highlighted < len(content.items):
            item = content.items[highlighted]

        if item is not None:
            heading, heading_font, heading_color = content.title, small, colors.text_dim
            body, body_font, body_color = item.title, big, colors.text
        elif not content.items:
            heading, heading_font, heading_color = content.title, big, colors.text
            body, body_font, body_color = content.empty_message, small, colors.text_dim
        else:
            heading, heading_font, heading_color = "", small, colors.text_dim
            body, body_font, body_color = content.title, big, colors.text

        heading_h = QFontMetricsF(heading_font).height() if heading else 0.0
        body_rect = QFontMetricsF(body_font).boundingRect(
            QRectF(0, 0, width, inner * 2), int(flags), body
        )
        body_h = min(body_rect.height(), inner * 1.1)
        total = heading_h + (4 if heading else 0) + body_h
        y = -total / 2 - 4

        if heading:
            p.setFont(heading_font)
            p.setPen(heading_color)
            p.drawText(QRectF(-width / 2, y, width, heading_h), flags, heading)
            y += heading_h + 4
        p.setFont(body_font)
        p.setPen(body_color)
        p.drawText(QRectF(-width / 2, y, width, body_h), flags, body)

        self._wheel_dots(p, inner, content.wheel, colors)

    def _wheel_dots(self, p: QPainter, inner: float, active: str, colors: WheelColors) -> None:
        """Three dots at the bottom of the center: which of the wheels is shown."""
        p.setPen(Qt.PenStyle.NoPen)
        y = inner * 0.62
        inactive = QColor(colors.text_dim)
        inactive.setAlpha(90)
        for i, wheel in enumerate(WHEEL_TITLES):
            p.setBrush(colors.accent if wheel == active else inactive)
            p.drawEllipse(QPointF((i - 1) * 10.0, y), 2.6, 2.6)
