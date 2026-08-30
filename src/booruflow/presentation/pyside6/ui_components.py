"""Small, reusable PySide6 presentation components."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget


class DataTable(QTableWidget):
    """Consistent read-only row table with a lightweight empty state.

    Column resize modes and initial widths are deliberately left to each page.
    This keeps domain-specific layouts and user-resized columns intact.
    """

    def __init__(self, rows: int, columns: int, parent=None) -> None:
        super().__init__(rows, columns, parent)
        self._empty_text = ""
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

    def set_empty_text(self, text: str) -> None:
        self._empty_text = text
        self.viewport().update()

    def empty_text(self) -> str:
        return self._empty_text

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().paintEvent(event)
        if self.rowCount() or not self._empty_text:
            return
        painter = QPainter(self.viewport())
        painter.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
        painter.drawText(
            self.viewport().rect().adjusted(24, 24, -24, -24),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            self._empty_text,
        )
