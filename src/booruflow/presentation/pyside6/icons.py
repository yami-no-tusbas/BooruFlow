"""Small, resolution-independent navigation icons drawn by Qt."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap


NAVIGATION_ICONS: dict[str, tuple[str, str]] = {
    "Home": ("H", "#3B82F6"),
    "Review": ("R", "#10B981"),
    "Tagging": ("T", "#F59E0B"),
    "Organization": ("O", "#8B5CF6"),
    "Cleanup": ("C", "#EF4444"),
    "Options": ("S", "#64748B"),
    "Grabber": ("G", "#06B6D4"),
}


def navigation_icon(name: str, size: int = 28) -> QIcon:
    glyph, color = NAVIGATION_ICONS[name]
    ratio = 2
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(ratio, ratio)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(1, 1, size - 2, size - 2, 7, 7)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(16)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(0, 0, size, size, Qt.AlignmentFlag.AlignCenter, glyph)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return QIcon(pixmap)
