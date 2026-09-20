"""Read-only local tag database browser."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from booruflow.domain.booru_sites import site_definition
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.tag_browser import TagRow, TagSearch, search_tags
from booruflow.presentation.pyside6.status_bar import PageStatus
from booruflow.presentation.pyside6.ui_components import DataTable

RAW_NAME_ROLE = Qt.ItemDataRole.UserRole + 1
CANONICAL_NAME_ROLE = Qt.ItemDataRole.UserRole + 2


class FilterFlowLayout(QLayout):
    """Keep compact filter groups together and wrap them only when needed."""

    def __init__(self, parent: QWidget | None = None, *, spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, max(0, width), 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x, y = effective.x(), effective.y()
        line_height = 0
        spacing = max(0, self.spacing())
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width()
            if line_height and next_x > effective.right() + 1:
                x = effective.x()
                y += line_height + spacing
                next_x = x + hint.width()
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x + spacing
            line_height = max(line_height, hint.height())
        return max(0, y + line_height - rect.y() + margins.bottom())


class TagSearchWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self, database: Path, request: TagSearch, site: str, alias_database: Path | None
    ) -> None:
        super().__init__()
        self.database = database
        self.request = request
        self.site = site
        self.alias_database = alias_database

    def run(self) -> None:
        try:
            self.completed.emit(search_tags(
                self.database, self.request, site=self.site, alias_database=self.alias_database
            ))
        except Exception as exc:  # noqa: BLE001 - worker boundary reports search failures
            self.failed.emit(str(exc))


class TagBrowserPage(QWidget):
    def __init__(
        self,
        catalog: LanguageCatalog,
        databases: dict[str, Path | None] | None = None,
        alias_databases: dict[str, Path | None] | None = None,
    ) -> None:
        super().__init__()
        self.catalog = catalog
        self.page_status = PageStatus("tag_browser", self)
        self.databases = databases or {}
        self.alias_databases = alias_databases or {}
        self.worker: TagSearchWorker | None = None
        self.rows: list[TagRow] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 22)
        self.title = QLabel(); self.title.setStyleSheet("font-size:22px;font-weight:600;")
        layout.addWidget(self.title)

        self.filters = QGroupBox()
        self.filters_layout = QVBoxLayout(self.filters)
        self.filters_layout.setContentsMargins(12, 12, 12, 12)
        self.filters_layout.setSpacing(8)

        self.search_row = QHBoxLayout()
        self.search_row.setSpacing(8)
        self.site = QComboBox()
        self.site.addItem("Gelbooru", "gelbooru"); self.site.addItem("e621", "e621")
        self.site.currentIndexChanged.connect(self._site_changed)
        self.site_label = QLabel()
        self.query = QLineEdit(); self.query.returnPressed.connect(self.search)
        self.mode = QComboBox()
        for key in ("auto", "contains", "glob", "regex", "exact"):
            self.mode.addItem(key, key)
        self.query_label = QLabel()
        self.mode_label = QLabel()
        self.search_button = QPushButton(); self.search_button.clicked.connect(self.search)
        self.search_row.addWidget(self.site_label)
        self.search_row.addWidget(self.site)
        self.search_row.addWidget(self.query_label)
        self.search_row.addWidget(self.query, 1)
        self.search_row.addWidget(self.mode_label)
        self.search_row.addWidget(self.mode)
        self.search_row.addWidget(self.search_button)
        self.filters_layout.addLayout(self.search_row)

        self.filter_row = QWidget()
        self.filter_row_layout = FilterFlowLayout(self.filter_row, spacing=8)
        self.category = QComboBox()
        self.minimum = QSpinBox(); self.minimum.setRange(0, 100_000_000)
        self.maximum = QSpinBox(); self.maximum.setRange(0, 100_000_000); self.maximum.setSpecialValueText("∞")
        self.ambiguous = QComboBox()
        self.state = QComboBox()
        self.alias = QComboBox()
        self.limit = QSpinBox(); self.limit.setRange(1, 25_000); self.limit.setValue(1_000)
        self.category_label = QLabel(); self.posts_label = QLabel(); self.range_separator = QLabel("–")
        self.ambiguous_label = QLabel(); self.state_label = QLabel(); self.alias_label = QLabel()
        self.limit_label = QLabel()
        controls: tuple[tuple[QLabel, QWidget], ...] = (
            (self.category_label, self.category), (self.ambiguous_label, self.ambiguous),
            (self.state_label, self.state), (self.alias_label, self.alias),
        )
        for label, widget in controls:
            self.filter_row_layout.addWidget(self._filter_group(label, widget))
        self.filter_row_layout.addWidget(self._filter_group(
            self.posts_label, self.minimum, self.range_separator, self.maximum
        ))
        self.filter_row_layout.addWidget(self._filter_group(self.limit_label, self.limit))
        self.filters_layout.addWidget(self.filter_row)
        layout.addWidget(self.filters)

        labels = (
            self.site_label, self.query_label, self.mode_label, self.category_label,
            self.ambiguous_label, self.state_label, self.alias_label, self.posts_label,
            self.limit_label, self.range_separator,
        )
        for label in labels:
            label.setProperty("preserveHorizontalSize", True)
            label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        for label, widget in (
            (self.site_label, self.site), (self.query_label, self.query),
            (self.mode_label, self.mode), (self.category_label, self.category),
            (self.ambiguous_label, self.ambiguous), (self.state_label, self.state),
            (self.alias_label, self.alias), (self.posts_label, self.minimum),
            (self.limit_label, self.limit),
        ):
            label.setBuddy(widget)
        self.query.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for combo in (self.site, self.mode, self.category, self.ambiguous, self.state, self.alias):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            combo.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        for spinbox in (self.minimum, self.maximum, self.limit):
            spinbox.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        self.table = DataTable(0, 6)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(1, self.table.horizontalHeader().ResizeMode.Stretch)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.cellDoubleClicked.connect(lambda row, _column: self._open_row(row, canonical=False))
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout(); bottom.addStretch(1)
        self.copy_selected = QPushButton(); self.copy_selected.clicked.connect(self.copy_selection)
        self.copy_results = QPushButton(); self.copy_results.clicked.connect(self.copy_all)
        bottom.addWidget(self.copy_selected); bottom.addWidget(self.copy_results); layout.addLayout(bottom)
        self.copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.table)
        self.copy_shortcut.activated.connect(self.copy_selection)
        self._site_changed()
        self.retranslate()

    @staticmethod
    def _filter_group(*widgets: QWidget) -> QWidget:
        group = QWidget()
        row = QHBoxLayout(group)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for widget in widgets:
            row.addWidget(widget)
        return group

    def set_databases(self, databases: dict[str, Path | None]) -> None:
        self.databases = databases

    def set_alias_databases(self, databases: dict[str, Path | None]) -> None:
        self.alias_databases = databases

    def current_site(self) -> str:
        return str(self.site.currentData())

    def _site_changed(self) -> None:
        definition = site_definition(self.current_site())
        selected = self.category.currentData()
        self.category.clear(); self.category.addItem(self.catalog.text("tag_browser.all"), None)
        for raw_type, name in definition.categories.items():
            self.category.addItem(name.title(), raw_type)
        index = self.category.findData(selected)
        self.category.setCurrentIndex(max(0, index))
        self.alias.setEnabled(definition.supports_aliases)
        if not definition.supports_aliases:
            self.alias.setCurrentIndex(0)

    def current_database(self) -> Path | None:
        return self.databases.get(str(self.site.currentData()))

    def _request(self) -> TagSearch:
        return TagSearch(
            text=self.query.text().strip(), mode=str(self.mode.currentData()),
            category=self.category.currentData(), minimum_count=self.minimum.value(),
            maximum_count=self.maximum.value() or None,
            ambiguous=self.ambiguous.currentData(), limit=self.limit.value(),
            state=str(self.state.currentData()), alias=str(self.alias.currentData()),
        )

    def search(self) -> None:
        if self.worker and self.worker.isRunning(): return
        database = self.current_database()
        if database is None or not database.is_file():
            self.page_status.show_message(
                self.catalog.text("tag_browser.database_missing"), timeout_ms=6_000, log=True
            )
            return
        self.search_button.setEnabled(False)
        self.page_status.clear_message()
        self.page_status.set_state("searching")
        site = self.current_site()
        self.worker = TagSearchWorker(
            database, self._request(), site, self.alias_databases.get(site)
        )
        self.worker.completed.connect(self._show_rows); self.worker.failed.connect(self._show_error)
        self.worker.finished.connect(lambda: self.search_button.setEnabled(True)); self.worker.start()

    def _show_rows(self, rows: object) -> None:
        self.rows = list(rows) if isinstance(rows, list) else []
        self.table.setSortingEnabled(False); self.table.setRowCount(len(self.rows))
        for row_index, row in enumerate(self.rows):
            definition = site_definition(self.current_site())
            deprecated = definition.deprecated_category == row.category
            decorated = row.name + (" (deprecated)" if deprecated else "")
            if row.canonical_name:
                decorated += f" → {row.canonical_name}"
            category = definition.categories.get(row.category, "unknown").title()
            if deprecated:
                state_key = "deprecated"
            elif row.ambiguous:
                state_key = "ambiguous_state"
            elif row.canonical_name:
                state_key = "alias_state"
            else:
                state_key = "canonical"
            state = self.catalog.text(f"tag_browser.{state_key}")
            boolean = self.catalog.text("tag_browser.yes" if row.ambiguous else "tag_browser.no")
            values = (row.id, decorated, row.post_count, category, boolean, state)
            for column, value in enumerate(values):
                item = QTableWidgetItem()
                item.setData(Qt.ItemDataRole.DisplayRole, value)
                item.setData(Qt.ItemDataRole.UserRole, row.category if column == 3 else value)
                item.setData(RAW_NAME_ROLE, row.name)
                item.setData(CANONICAL_NAME_ROLE, row.canonical_name)
                if column == 1:
                    tooltip = [f"Tag: {row.name}"]
                    if row.direct_alias: tooltip.append(f"Alias direct: {row.direct_alias}")
                    if row.canonical_name: tooltip.append(f"Canonical: {row.canonical_name}")
                    tooltip.append(f"Category: {category}")
                    if deprecated: tooltip.append("Deprecated: yes")
                    if row.ambiguous: tooltip.append("Ambiguous: yes")
                    item.setToolTip("\n".join(tooltip))
                if column != 1: item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row_index, column, item)
        self.table.setSortingEnabled(True)
        self.page_status.set_state("ready")
        self.page_status.show_message(
            self.catalog.text("tag_browser.results", count=len(self.rows)), timeout_ms=0
        )

    def _show_error(self, error: str) -> None:
        self.page_status.set_state("ready")
        self.page_status.show_message(
            self.catalog.text("tag_browser.error", error=error), timeout_ms=0, log=True
        )
        self.page_status.show_message(
            self.catalog.text("status.message.failed_see_log"), timeout_ms=6_000
        )

    def _copy_names(self, names: list[str]) -> None:
        if names:
            unique_names = list(dict.fromkeys(names))
            QApplication.clipboard().setText("\n".join(unique_names))
            key = "tag_browser.copied.one" if len(unique_names) == 1 else "tag_browser.copied.many"
            self.page_status.show_message(
                self.catalog.text(key, count=len(unique_names)), timeout_ms=5_000, log=True
            )

    def copy_selection(self) -> None:
        indexes = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        self._copy_names([str(self.table.item(row, 1).data(RAW_NAME_ROLE)) for row in indexes])

    def copy_all(self) -> None:
        self._copy_names([
            str(self.table.item(row, 1).data(RAW_NAME_ROLE)) for row in range(self.table.rowCount())
        ])

    def _row_names(self, row: int) -> tuple[str, str | None]:
        item = self.table.item(row, 1)
        return str(item.data(RAW_NAME_ROLE)), item.data(CANONICAL_NAME_ROLE)

    def _open_row(self, row: int, *, canonical: bool) -> None:
        raw_name, canonical_name = self._row_names(row)
        name = canonical_name if canonical else raw_name
        if name:
            QDesktopServices.openUrl(QUrl(site_definition(self.current_site()).search_url(str(name))))

    def _show_context_menu(self, position) -> None:
        row = self.table.rowAt(position.y())
        if row < 0:
            return
        self.table.selectRow(row)
        raw_name, canonical_name = self._row_names(row)
        site_name = site_definition(self.current_site()).display_name
        menu = QMenu(self)
        copy_raw = menu.addAction(self.catalog.text("tag_browser.copy_tag"))
        copy_canonical = menu.addAction(self.catalog.text("tag_browser.copy_canonical"))
        menu.addSeparator()
        open_raw = menu.addAction(self.catalog.text("tag_browser.open_site", site=site_name))
        open_canonical = menu.addAction(
            self.catalog.text("tag_browser.open_canonical_site", site=site_name)
        )
        has_distinct_canonical = bool(canonical_name and canonical_name != raw_name)
        copy_canonical.setEnabled(has_distinct_canonical)
        open_canonical.setEnabled(has_distinct_canonical)
        chosen = menu.exec(self.table.viewport().mapToGlobal(position))
        if chosen == copy_raw: self._copy_names([raw_name])
        elif chosen == copy_canonical and canonical_name: self._copy_names([str(canonical_name)])
        elif chosen == open_raw: self._open_row(row, canonical=False)
        elif chosen == open_canonical: self._open_row(row, canonical=True)

    def retranslate(self) -> None:
        text = self.catalog.text
        self.title.setText(text("nav.tag_browser")); self.filters.setTitle(text("tag_browser.filters"))
        self.site_label.setText(text("tag_browser.database"))
        self.query_label.setText(text("tag_browser.query")); self.query.setPlaceholderText(text("tag_browser.placeholder"))
        self.mode_label.setText(text("tag_browser.mode"))
        for index, key in enumerate(("auto", "contains", "glob", "regex", "exact")):
            self.mode.setItemText(index, text(f"tag_browser.mode_{key}"))
        self.search_button.setText(text("tag_browser.search")); self.copy_selected.setText(text("tag_browser.copy_selected"))
        self.copy_results.setText(text("tag_browser.copy_results"))
        self.category_label.setText(text("tag_browser.category"))
        self.posts_label.setText(text("tag_browser.posts"))
        self.minimum.setAccessibleName(text("tag_browser.minimum"))
        self.maximum.setAccessibleName(text("tag_browser.maximum"))
        self.ambiguous_label.setText(text("tag_browser.ambiguous")); self.state_label.setText(text("tag_browser.state"))
        self.alias_label.setText(text("tag_browser.alias")); self.limit_label.setText(text("tag_browser.limit"))
        ambiguity_tooltip = text("tag_browser.ambiguous_tooltip")
        self.ambiguous_label.setToolTip(ambiguity_tooltip)
        self.ambiguous.setToolTip(ambiguity_tooltip)
        alias_tooltip = text("tag_browser.alias_tooltip")
        self.alias_label.setToolTip(alias_tooltip)
        self.alias.setToolTip(alias_tooltip)
        self.ambiguous.clear()
        for key, value in (("all", None), ("ambiguous_state", 1), ("unambiguous", 0)):
            self.ambiguous.addItem(text(f"tag_browser.{key}"), value)
        self.state.clear()
        for key in ("all", "canonical", "alias_state", "deprecated", "ambiguous_state"):
            self.state.addItem(text(f"tag_browser.{key}"), key.replace("_state", ""))
        self.alias.clear()
        for key, value in (("all", "all"), ("with_alias", "with"), ("without_alias", "without")):
            self.alias.addItem(text(f"tag_browser.{key}"), value)
        self._site_changed()
        self.table.setHorizontalHeaderLabels(tuple(
            text(f"tag_browser.column_{key}") for key in ("id", "name", "posts", "category", "ambiguous", "state")
        ))
        self.table.set_empty_text(text("table.empty_search_results"))
