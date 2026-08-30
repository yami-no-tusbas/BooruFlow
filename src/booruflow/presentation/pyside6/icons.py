"""Colored, resolution-independent navigation pictograms drawn by Qt."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

NAVIGATION_COLORS = {
    "home": "#3B82F6",
    "review": "#10B981",
    "tagging": "#F59E0B",
    "tagging_legacy": "#A16207",
    "image_analysis": "#7C3AED",
    "auto_organize": "#0891B2",
    "similar_artists": "#D946EF",
    "organization": "#8B5CF6",
    "tag_browser": "#0F766E",
    "wiki": "#EC4899",
    "cleanup": "#EF4444",
    "options": "#64748B",
    "grabber": "#06B6D4",
    "tasks": "#2563EB",
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


def _image_analysis(painter: QPainter) -> None:
    painter.drawRoundedRect(QRectF(6, 7, 18, 16), 2, 2)
    painter.drawEllipse(QPointF(19, 11), 2, 2)
    painter.drawPolyline(
        QPolygonF([QPointF(8, 20), QPointF(13, 14), QPointF(17, 18), QPointF(20, 15), QPointF(23, 20)])
    )


def _similar_artists(painter: QPainter) -> None:
    painter.drawEllipse(QPointF(10, 12), 3, 3)
    painter.drawEllipse(QPointF(20, 10), 3, 3)
    painter.drawEllipse(QPointF(17, 20), 3, 3)
    painter.drawLine(QPointF(13, 12), QPointF(17, 10))
    painter.drawLine(QPointF(12, 15), QPointF(15, 18))
    painter.drawLine(QPointF(19, 13), QPointF(18, 17))


def _tag_browser(painter: QPainter) -> None:
    painter.drawRoundedRect(QRectF(5, 7, 20, 16), 2, 2)
    painter.drawLine(QPointF(5, 12), QPointF(25, 12))
    painter.drawLine(QPointF(11, 7), QPointF(11, 23))
    painter.drawLine(QPointF(18, 7), QPointF(18, 23))
    painter.drawLine(QPointF(5, 17), QPointF(25, 17))


def _cleanup(painter: QPainter) -> None:
    painter.drawLine(QPointF(11, 8), QPointF(19, 8))
    painter.drawLine(QPointF(13, 6), QPointF(17, 6))
    painter.drawRoundedRect(QRectF(10, 10, 10, 13), 2, 2)
    painter.drawLine(QPointF(13, 13), QPointF(13, 20))
    painter.drawLine(QPointF(17, 13), QPointF(17, 20))


def _wiki(painter: QPainter) -> None:
    painter.drawRoundedRect(QRectF(8, 6, 14, 18), 2, 2)
    painter.drawLine(QPointF(11, 11), QPointF(19, 11))
    painter.drawLine(QPointF(11, 15), QPointF(19, 15))
    painter.drawLine(QPointF(11, 19), QPointF(16, 19))


def _options(painter: QPainter) -> None:
    for y, knob in ((9, 12), (15, 19), (21, 10)):
        painter.drawLine(QPointF(7, y), QPointF(23, y))
        painter.drawEllipse(QPointF(knob, y), 2, 2)


def _grabber(painter: QPainter) -> None:
    painter.drawLine(QPointF(15, 6), QPointF(15, 18))
    painter.drawPolyline(QPolygonF([QPointF(10, 14), QPointF(15, 19), QPointF(20, 14)]))
    painter.drawPolyline(QPolygonF([QPointF(8, 19), QPointF(8, 23), QPointF(22, 23), QPointF(22, 19)]))


def _tasks(painter: QPainter) -> None:
    painter.drawRoundedRect(QRectF(7, 6, 16, 18), 2, 2)
    for y in (11, 16, 21):
        painter.drawEllipse(QPointF(11, y), 1, 1)
        painter.drawLine(QPointF(14, y), QPointF(20, y))


_DRAWERS: dict[str, Callable[[QPainter], None]] = {
    "home": _home,
    "review": _review,
    "tagging": _tagging,
    "tagging_legacy": _tagging,
    "image_analysis": _image_analysis,
    "auto_organize": _organization,
    "similar_artists": _similar_artists,
    "organization": _organization,
    "tag_browser": _tag_browser,
    "wiki": _wiki,
    "cleanup": _cleanup,
    "options": _options,
    "grabber": _grabber,
    "tasks": _tasks,
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


def wiki_tool_icon(name: str, size: int = 24) -> QIcon:
    pixmap = QPixmap(size * 2, size * 2); pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap); painter.setRenderHint(QPainter.RenderHint.Antialiasing); painter.scale(2, 2)
    color = QColor("#374151"); painter.setPen(QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)); painter.setBrush(Qt.BrushStyle.NoBrush)
    if name == "bold":
        path = QPainterPath(); path.moveTo(7, 3); path.lineTo(7, 21); path.moveTo(7, 4); path.cubicTo(19, 2, 20, 11, 8, 12); path.cubicTo(21, 11, 21, 22, 7, 20); painter.drawPath(path)
    elif name == "italic":
        painter.drawLine(10, 4, 20, 4); painter.drawLine(5, 20, 15, 20); painter.drawLine(15, 4, 10, 20)
    elif name == "post":
        painter.drawRoundedRect(QRectF(4, 3, 16, 18), 2, 2); painter.drawLine(8, 9, 16, 9); painter.drawLine(8, 15, 16, 15); painter.drawLine(10, 6, 10, 18); painter.drawLine(15, 6, 15, 18)
    elif name == "quote":
        painter.drawRoundedRect(QRectF(4, 6, 6, 7), 2, 2); painter.drawLine(8, 12, 6, 18); painter.drawRoundedRect(QRectF(14, 6, 6, 7), 2, 2); painter.drawLine(18, 12, 16, 18)
    elif name == "tag_link":
        painter.drawRoundedRect(QRectF(3, 8, 10, 8), 4, 4); painter.drawRoundedRect(QRectF(11, 8, 10, 8), 4, 4); painter.drawLine(9, 12, 15, 12)
    elif name == "search_link":
        painter.drawEllipse(QRectF(4, 4, 11, 11)); painter.drawLine(14, 14, 21, 21)
    elif name == "spoiler":
        path = QPainterPath(); path.moveTo(2, 12); path.cubicTo(7, 5, 17, 5, 22, 12); path.cubicTo(17, 19, 7, 19, 2, 12); painter.drawPath(path); painter.drawEllipse(QPointF(12, 12), 2.5, 2.5)
    elif name == "external":
        painter.drawRoundedRect(QRectF(3, 7, 14, 14), 2, 2); painter.drawLine(11, 13, 21, 3); painter.drawPolyline(QPolygonF([QPointF(15, 3), QPointF(21, 3), QPointF(21, 9)]))
    elif name == "see_also":
        for y in (6, 12, 18): painter.drawEllipse(QPointF(4, y), 1, 1); painter.drawLine(8, y, 21, y)
    painter.end(); pixmap.setDevicePixelRatio(2); return QIcon(pixmap)
