"""Colored, resolution-independent navigation pictograms drawn by Qt."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF


NAVIGATION_COLORS = {
    "home": "#3B82F6",
    "review": "#10B981",
    "tagging": "#F59E0B",
    "organization": "#8B5CF6",
    "cleanup": "#EF4444",
    "options": "#64748B",
    "grabber": "#06B6D4",
}


def _home(painter: QPainter) -> None:
    painter.drawPolyline(QPolygonF([QPointF(7, 14), QPointF(15, 7), QPointF(23, 14)]))
    painter.drawRect(QRectF(9, 13, 12, 10))
    painter.drawLine(QPointF(15, 18), QPointF(15, 23))


def _review(painter: QPainter) -> None:
    painter.drawRoundedRect(QRectF(7, 7, 16, 16), 3, 3)
    painter.drawPolyline(QPolygonF([QPointF(10, 15), QPointF(14, 19), QPointF(21, 11)]))


def _tagging(painter: QPainter) -> None:
    path = QPainterPath()
    path.moveTo(7, 8)
    path.lineTo(17, 8)
    path.lineTo(23, 14)
    path.lineTo(14, 23)
    path.lineTo(7, 16)
    path.closeSubpath()
    painter.drawPath(path)
    painter.drawEllipse(QPointF(11, 12), 1.5, 1.5)


def _organization(painter: QPainter) -> None:
    painter.drawLine(QPointF(15, 8), QPointF(15, 14))
    painter.drawLine(QPointF(9, 14), QPointF(21, 14))
    painter.drawLine(QPointF(9, 14), QPointF(9, 21))
    painter.drawLine(QPointF(21, 14), QPointF(21, 21))
    for point in (QPointF(15, 7), QPointF(9, 22), QPointF(21, 22)):
        painter.drawEllipse(point, 2.5, 2.5)


def _cleanup(painter: QPainter) -> None:
    painter.drawLine(QPointF(11, 8), QPointF(19, 8))
    painter.drawLine(QPointF(13, 6), QPointF(17, 6))
    painter.drawRoundedRect(QRectF(10, 10, 10, 13), 2, 2)
    painter.drawLine(QPointF(13, 13), QPointF(13, 20))
    painter.drawLine(QPointF(17, 13), QPointF(17, 20))


def _options(painter: QPainter) -> None:
    for y, knob in ((9, 12), (15, 19), (21, 10)):
        painter.drawLine(QPointF(7, y), QPointF(23, y))
        painter.drawEllipse(QPointF(knob, y), 2, 2)


def _grabber(painter: QPainter) -> None:
    painter.drawLine(QPointF(15, 6), QPointF(15, 18))
    painter.drawPolyline(QPolygonF([QPointF(10, 14), QPointF(15, 19), QPointF(20, 14)]))
    painter.drawPolyline(QPolygonF([QPointF(8, 19), QPointF(8, 23), QPointF(22, 23), QPointF(22, 19)]))


_DRAWERS: dict[str, Callable[[QPainter], None]] = {
    "home": _home,
    "review": _review,
    "tagging": _tagging,
    "organization": _organization,
    "cleanup": _cleanup,
    "options": _options,
    "grabber": _grabber,
}


def navigation_icon(name: str, size: int = 30) -> QIcon:
    ratio = 2
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(ratio, ratio)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(NAVIGATION_COLORS[name]))
    painter.drawRoundedRect(1, 1, size - 2, size - 2, 7, 7)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor("white"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    _DRAWERS[name](painter)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return QIcon(pixmap)
