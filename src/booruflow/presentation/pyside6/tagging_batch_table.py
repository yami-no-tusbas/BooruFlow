"""Lazy Batch cells: opening a large queue never allocates seven items per row."""
from PySide6.QtCore import QAbstractTableModel, Qt, Signal
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView, QTableWidgetItem

from booruflow.presentation.pyside6.theme import style_item_view


class BatchModel(QAbstractTableModel):
    def __init__(self, parent):
        super().__init__(parent)
        self.rows = []
        self.headers = []
        self.format_row = None
        self._cached_row = None
        self._cached_values = ()

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else 7

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        if index.row() != self._cached_row:
            self._cached_values = self.format_row(self.rows[index.row()])
            self._cached_row = index.row()
        return self._cached_values[index.column()]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section] if section < len(self.headers) else ''
        return super().headerData(section, orientation, role)

    def replace(self, rows, formatter):
        self.beginResetModel()
        self.rows = rows
        self.format_row = formatter
        self._cached_row = None
        self.endResetModel()


class BatchTable(QTableView):
    itemSelectionChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.batch_model = BatchModel(self)
        self.setModel(self.batch_model)
        style_item_view(self)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.selectionModel().selectionChanged.connect(lambda *_: self.itemSelectionChanged.emit())
        self._empty_text = ''

    def setHorizontalHeaderLabels(self, labels):
        self.batch_model.headers = list(labels)
        self.batch_model.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(labels) - 1)

    def horizontalHeaderItem(self, column):
        return QTableWidgetItem(self.batch_model.headers[column])

    def rowCount(self):
        return self.batch_model.rowCount()

    def item(self, row, column):
        # Compatibility for existing row actions; no persistent per-cell objects.
        if not 0 <= row < self.rowCount():
            return None
        return QTableWidgetItem(str(self.batch_model.data(self.batch_model.index(row, column))))

    def set_empty_text(self, text):
        self._empty_text = text
        self.viewport().update()

    def empty_text(self):
        return self._empty_text

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.rowCount() or not self._empty_text:
            return
        painter = QPainter(self.viewport())
        painter.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
        painter.drawText(self.viewport().rect().adjusted(24, 24, -24, -24),
                         Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._empty_text)
