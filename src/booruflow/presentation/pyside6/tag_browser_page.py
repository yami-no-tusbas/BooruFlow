"""Read-only local tag database browser."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.tag_browser import TagRow, TagSearch, search_tags
from booruflow.presentation.pyside6.ui_components import DataTable


class TagSearchWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, database: Path, request: TagSearch) -> None:
        super().__init__()
        self.database = database
        self.request = request

    def run(self) -> None:
        try:
            self.completed.emit(search_tags(self.database, self.request))
        except Exception as exc:  # noqa: BLE001 - worker boundary reports search failures
            self.failed.emit(str(exc))


class TagBrowserPage(QWidget):
    def __init__(self, catalog: LanguageCatalog, databases: dict[str, Path | None] | None = None) -> None:
        super().__init__()
        self.catalog = catalog
        self.databases = databases or {}
        self.worker: TagSearchWorker | None = None
        self.rows: list[TagRow] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 22)
        self.title = QLabel(); self.title.setStyleSheet("font-size:22px;font-weight:600;")
        layout.addWidget(self.title)

        self.filters = QGroupBox()
        form = QFormLayout(self.filters)
        self.site = QComboBox()
        self.site.addItem("Gelbooru", "gelbooru"); self.site.addItem("e621", "e621")
        self.site_label = QLabel(); form.addRow(self.site_label, self.site)
        query_row = QHBoxLayout()
        self.query = QLineEdit(); self.query.returnPressed.connect(self.search)
        self.mode = QComboBox()
        for key in ("auto", "contains", "glob", "regex", "exact"):
            self.mode.addItem(key, key)
        query_row.addWidget(self.query, 1); query_row.addWidget(self.mode)
        self.query_label = QLabel(); form.addRow(self.query_label, query_row)

        quick_row = QHBoxLayout()
        self.category = QComboBox(); self.category.addItem("All", None)
        for value in (0, 1, 3, 4, 5, 6): self.category.addItem(str(value), value)
        self.minimum = QSpinBox(); self.minimum.setRange(0, 100_000_000)
        self.maximum = QSpinBox(); self.maximum.setRange(0, 100_000_000); self.maximum.setSpecialValueText("∞")
        self.ambiguous = QComboBox(); self.ambiguous.addItem("All", None); self.ambiguous.addItem("0", 0); self.ambiguous.addItem("1", 1)
        self.limit = QSpinBox(); self.limit.setRange(1, 25_000); self.limit.setValue(1_000)
        for label, widget in (("ttype", self.category), ("min", self.minimum), ("max", self.maximum), ("amb.", self.ambiguous), ("limit", self.limit)):
            quick_row.addWidget(QLabel(label)); quick_row.addWidget(widget)
        quick_row.addStretch(1)
        self.search_button = QPushButton(); self.search_button.clicked.connect(self.search)
        quick_row.addWidget(self.search_button)
        form.addRow("", quick_row); layout.addWidget(self.filters)

        self.table = DataTable(0, 5)
        self.table.setHorizontalHeaderLabels(("id", "name", "post_count", "ttype", "ambiguous"))
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(1, self.table.horizontalHeader().ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout(); self.status = QLabel(); bottom.addWidget(self.status, 1)
        self.copy_selected = QPushButton(); self.copy_selected.clicked.connect(self.copy_selection)
        self.copy_results = QPushButton(); self.copy_results.clicked.connect(self.copy_all)
        bottom.addWidget(self.copy_selected); bottom.addWidget(self.copy_results); layout.addLayout(bottom)
        self.copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.table)
        self.copy_shortcut.activated.connect(self.copy_selection)
        self.retranslate()

    def set_databases(self, databases: dict[str, Path | None]) -> None:
        self.databases = databases

    def current_database(self) -> Path | None:
        return self.databases.get(str(self.site.currentData()))

    def _request(self) -> TagSearch:
        return TagSearch(
            text=self.query.text().strip(), mode=str(self.mode.currentData()),
            category=self.category.currentData(), minimum_count=self.minimum.value(),
            maximum_count=self.maximum.value() or None,
            ambiguous=self.ambiguous.currentData(), limit=self.limit.value(),
        )

    def search(self) -> None:
        if self.worker and self.worker.isRunning(): return
        database = self.current_database()
        if database is None or not database.is_file():
            self.status.setText(self.catalog.text("tag_browser.database_missing")); return
        self.search_button.setEnabled(False); self.status.setText(self.catalog.text("tag_browser.searching"))
        self.worker = TagSearchWorker(database, self._request())
        self.worker.completed.connect(self._show_rows); self.worker.failed.connect(self._show_error)
        self.worker.finished.connect(lambda: self.search_button.setEnabled(True)); self.worker.start()

    def _show_rows(self, rows: object) -> None:
        self.rows = list(rows) if isinstance(rows, list) else []
        self.table.setSortingEnabled(False); self.table.setRowCount(len(self.rows))
        for row_index, row in enumerate(self.rows):
            values = (row.id, row.name, row.post_count, row.category, row.ambiguous)
            for column, value in enumerate(values):
                item = QTableWidgetItem()
                item.setData(Qt.ItemDataRole.DisplayRole, value)
                item.setData(Qt.ItemDataRole.UserRole, value)
                if column != 1: item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row_index, column, item)
        self.table.setSortingEnabled(True)
        self.status.setText(self.catalog.text("tag_browser.results", count=len(self.rows)))

    def _show_error(self, error: str) -> None:
        self.status.setText(self.catalog.text("tag_browser.error", error=error))

    def _copy_names(self, names: list[str]) -> None:
        if names:
            QApplication.clipboard().setText("\n".join(dict.fromkeys(names)))
            self.status.setText(self.catalog.text("tag_browser.copied", count=len(dict.fromkeys(names))))

    def copy_selection(self) -> None:
        indexes = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        self._copy_names([self.table.item(row, 1).text() for row in indexes])

    def copy_all(self) -> None:
        self._copy_names([self.table.item(row, 1).text() for row in range(self.table.rowCount())])

    def retranslate(self) -> None:
        text = self.catalog.text
        self.title.setText(text("nav.tag_browser")); self.filters.setTitle(text("tag_browser.filters"))
        self.site_label.setText(text("tag_browser.database"))
        self.query_label.setText(text("tag_browser.query")); self.query.setPlaceholderText(text("tag_browser.placeholder"))
        for index, key in enumerate(("auto", "contains", "glob", "regex", "exact")):
            self.mode.setItemText(index, text(f"tag_browser.mode_{key}"))
        self.search_button.setText(text("tag_browser.search")); self.copy_selected.setText(text("tag_browser.copy_selected"))
        self.copy_results.setText(text("tag_browser.copy_results"))
        self.table.set_empty_text(text("table.empty_search_results"))
        if not self.status.text(): self.status.setText(text("tag_browser.ready"))
