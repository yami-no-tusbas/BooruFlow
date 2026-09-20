"""Responsive, read-only Gelbooru wiki audit page."""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from booruflow.domain.booru_sites import site_definition
from booruflow.infrastructure.gelbooru_client import (
    GelbooruAuthenticationError,
    GelbooruDapiClient,
)
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.wiki_audit import (
    SHORT_WIKI_MAX_WORDS,
    AuditResult,
    AuditTag,
    GelbooruWikiAuditService,
    parse_post_reference,
    random_post_id,
    summarize,
)
from booruflow.infrastructure.wiki_html import sanitize_wiki_html
from booruflow.infrastructure.wiki_page_cache import WikiPageCache
from booruflow.presentation.pyside6.ui_components import DataTable

ROW_ROLE = Qt.ItemDataRole.UserRole + 20
WORDS_SORT_ROLE = Qt.ItemDataRole.UserRole + 21
OLD_WIKI_AGE = timedelta(days=365 * 5)


class WordCountItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = self.data(WORDS_SORT_ROLE)
        right = other.data(WORDS_SORT_ROLE)
        left_key = (left is None, left if left is not None else 0)
        right_key = (right is None, right if right is not None else 0)
        return left_key < right_key


class AuditWorker(QThread):
    completed = Signal(object)
    failed = Signal(object)
    progress = Signal(int, int, str)

    def __init__(self, service: GelbooruWikiAuditService, post_id: int, force: bool) -> None:
        super().__init__()
        self.service, self.post_id, self.force = service, post_id, force

    def run(self) -> None:
        try:
            result = self.service.audit_post(
                self.post_id, force=self.force, progress=self.progress.emit
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.failed.emit(exc)
        else:
            self.completed.emit(result)


class RandomPostWorker(QThread):
    completed = Signal(int)
    failed = Signal(object)

    def __init__(self, client: GelbooruDapiClient) -> None:
        super().__init__()
        self.client = client

    def run(self) -> None:
        try:
            self.completed.emit(random_post_id(self.client))
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.failed.emit(exc)


class WikiAuditPage(QWidget):
    def __init__(
        self,
        catalog: LanguageCatalog,
        tag_database: Path | None,
        alias_database: Path | None,
        cache_database: Path,
        browser_launcher=None,
        credentials_provider=None,
        log=None,
    ) -> None:
        super().__init__()
        self.catalog, self.tag_database = catalog, tag_database
        self.alias_database, self.cache_database = alias_database, cache_database
        self.browser_launcher = browser_launcher
        self.credentials_provider = credentials_provider or dict
        self.log = log or (lambda _message: None)
        self._dapi_operation = ""
        self.result: AuditResult | None = None
        self.worker: AuditWorker | RandomPostWorker | None = None
        self._reader_url = ""
        self._scan_generation = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 22)
        self.title = QLabel()
        self.title.setStyleSheet("font-size:22px;font-weight:600;")
        layout.addWidget(self.title)
        source = QGroupBox()
        source_layout = QHBoxLayout(source)
        self.reference = QLineEdit()
        self.reference.returnPressed.connect(self.load_reference)
        self.load_button = QPushButton()
        self.load_button.clicked.connect(self.load_reference)
        self.random_button = QPushButton()
        self.random_button.clicked.connect(self.load_random)
        self.refresh_button = QPushButton()
        self.refresh_button.clicked.connect(lambda: self._start_scan(force=True))
        source_layout.addWidget(self.reference, 1)
        source_layout.addWidget(self.load_button)
        source_layout.addWidget(self.random_button)
        source_layout.addWidget(self.refresh_button)
        layout.addWidget(source)

        filters = QGridLayout()
        self.state_filter = QComboBox()
        for key in ("all", "present", "missing", "old"):
            self.state_filter.addItem("", key)
        self.category_filter = QComboBox()
        self.category_filter.addItem("", None)
        for category, name in site_definition("gelbooru").categories.items():
            self.category_filter.addItem(name.title(), category)
        self.minimum = QSpinBox()
        self.minimum.setRange(0, 100_000_000)
        self.length_filter = QComboBox()
        for key in ("all", "empty", "short", "non_empty"):
            self.length_filter.addItem("", key)
        self.search = QLineEdit()
        self.ignore_metadata = QCheckBox()
        self.ignore_deprecated = QCheckBox()
        self.state_filter.currentIndexChanged.connect(self.apply_filters)
        self.category_filter.currentIndexChanged.connect(self.apply_filters)
        self.minimum.valueChanged.connect(self.apply_filters)
        self.length_filter.currentIndexChanged.connect(self.apply_filters)
        self.search.textChanged.connect(self.apply_filters)
        self.ignore_metadata.toggled.connect(self.apply_filters)
        self.ignore_deprecated.toggled.connect(self.apply_filters)
        self.state_label = QLabel()
        self.category_label = QLabel()
        self.minimum_label = QLabel()
        self.length_label = QLabel()
        self.search_label = QLabel()
        controls = (
            (self.state_label, self.state_filter),
            (self.category_label, self.category_filter),
            (self.minimum_label, self.minimum),
            (self.search_label, self.search),
        )
        for column, (label, widget) in enumerate(controls):
            filters.addWidget(label, 0, column * 2)
            filters.addWidget(widget, 0, column * 2 + 1)
        filters.addWidget(self.ignore_metadata, 1, 0, 1, 2)
        filters.addWidget(self.ignore_deprecated, 1, 2, 1, 2)
        filters.addWidget(self.length_label, 1, 4)
        filters.addWidget(self.length_filter, 1, 5)
        filters.setColumnStretch(7, 1)
        layout.addLayout(filters)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = DataTable(0, 9)
        self.table.setSortingEnabled(True)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self.read_selected())
        splitter.addWidget(self.table)
        reader = QWidget()
        reader_layout = QVBoxLayout(reader)
        reader_layout.setContentsMargins(0, 6, 0, 0)
        actions = QHBoxLayout()
        self.read_button = QPushButton()
        self.read_button.clicked.connect(self.read_selected)
        self.open_button = QPushButton()
        self.open_button.clicked.connect(self.open_selected)
        self.reader_refresh = QPushButton()
        self.reader_refresh.clicked.connect(self.read_selected)
        actions.addWidget(self.read_button)
        actions.addWidget(self.open_button)
        actions.addWidget(self.reader_refresh)
        actions.addStretch(1)
        reader_layout.addLayout(actions)
        self.reader = QTextBrowser()
        reader_layout.addWidget(self.reader)
        splitter.addWidget(reader)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        bottom = QHBoxLayout()
        self.status = QLabel()
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.progress_bar)
        layout.addLayout(bottom)
        self.retranslate()

    def _service(self) -> GelbooruWikiAuditService:
        if self.tag_database is None or not self.tag_database.is_file():
            raise FileNotFoundError(self.catalog.text("wiki_audit.database_missing"))
        return GelbooruWikiAuditService(
            self.tag_database, self.alias_database, WikiPageCache(self.cache_database),
            dapi_client=self._dapi_client(),
        )

    def _dapi_client(self) -> GelbooruDapiClient:
        credentials = self.credentials_provider()
        gelbooru = credentials.get("gelbooru", {}) if isinstance(credentials, dict) else {}
        gelbooru = gelbooru if isinstance(gelbooru, dict) else {}
        return GelbooruDapiClient(
            str(gelbooru.get("user_id", "")), str(gelbooru.get("api_key", ""))
        )

    def _log_dapi(self, message: str, operation: str, client: GelbooruDapiClient) -> None:
        self.log(
            f"[INFO] [Wiki Audit] {message} operation={operation} "
            f"authenticated={str(client.authenticated).lower()}"
        )

    def load_random(self) -> None:
        if self._busy():
            return
        self._set_busy(True)
        client = self._dapi_client()
        self._dapi_operation = "random_post"
        self._log_dapi("DAPI request", self._dapi_operation, client)
        worker = RandomPostWorker(client)
        worker.completed.connect(self._random_ready)
        worker.failed.connect(self._failed)
        worker.finished.connect(lambda: self._random_finished(worker))
        self.worker = worker
        worker.start()

    def _random_ready(self, post_id: int) -> None:
        self.reference.setText(str(post_id))
        self.worker = None
        self._start_scan(force=False)

    def _random_finished(self, worker: RandomPostWorker) -> None:
        if self.worker is worker:
            self.worker = None
            self._set_busy(False)

    def load_reference(self) -> None:
        try:
            post_id = parse_post_reference(self.reference.text())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.reference.setText(str(post_id))
        self._start_scan(force=False)

    def _start_scan(self, *, force: bool) -> None:
        if self._busy():
            return
        try:
            post_id, service = parse_post_reference(self.reference.text()), self._service()
        except (ValueError, FileNotFoundError) as exc:
            self.status.setText(str(exc))
            return
        self._dapi_operation = "load_post"
        self._log_dapi("DAPI request", self._dapi_operation, service.dapi_client)
        self._scan_generation += 1
        generation = self._scan_generation
        worker = AuditWorker(service, post_id, force)
        worker.progress.connect(
            lambda completed, total, tag, value=generation: self._progress(
                completed, total, tag, value
            )
        )
        worker.completed.connect(
            lambda result, value=generation: self._completed(result, value)
        )
        worker.failed.connect(
            lambda error, value=generation: self._failed(error, value)
        )
        worker.finished.connect(lambda: self._scan_finished(worker))
        self.worker = worker
        self._set_busy(True)
        worker.start()

    def _busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def _set_busy(self, busy: bool) -> None:
        self.load_button.setEnabled(not busy)
        self.random_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy and self.result is not None)
        self.progress_bar.setVisible(busy)
        if busy:
            self.status.setText(self.catalog.text("wiki_audit.loading"))

    def _progress(
        self, completed: int, total: int, tag: str, generation: int | None = None
    ) -> None:
        if generation is not None and generation != self._scan_generation:
            return
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(completed)
        self.status.setText(
            self.catalog.text("wiki_audit.progress", completed=completed, total=total, tag=tag)
        )

    def _completed(self, result: object, generation: int | None = None) -> None:
        if generation is not None and generation != self._scan_generation:
            return
        if isinstance(result, AuditResult):
            self.result = result
            self.reference.setText(str(result.post_id))
            self.apply_filters()

    def _failed(self, error, generation: int | None = None) -> None:
        if generation is not None and generation != self._scan_generation:
            return
        client = self._dapi_client()
        if isinstance(error, GelbooruAuthenticationError):
            message = self.catalog.text("wiki_audit.authentication_error")
            status = 401
        else:
            message = str(error)
            status = getattr(error, "code", "unknown")
        self.log(
            f"[WARNING] [Wiki Audit] DAPI failed status={status} "
            f"operation={self._dapi_operation or 'unknown'} "
            f"authenticated={str(client.authenticated).lower()}"
        )
        self.status.setText(self.catalog.text("wiki_audit.error", error=message))

    def _scan_finished(self, worker: AuditWorker) -> None:
        if self.worker is worker:
            self.worker = None
        self._set_busy(False)

    def _visible_rows(self) -> list[AuditTag]:
        if self.result is None:
            return []
        now, state = datetime.now(UTC), str(self.state_filter.currentData())
        length = str(self.length_filter.currentData())
        category, query = self.category_filter.currentData(), self.search.text().strip().casefold()
        result = []
        for row in self.result.tags:
            if category is not None and row.category != category:
                continue
            if row.post_count < self.minimum.value() or query not in row.name.casefold():
                continue
            if self.ignore_metadata.isChecked() and row.category == 5:
                continue
            if self.ignore_deprecated.isChecked() and row.category == 6:
                continue
            is_old = bool(row.wiki.updated_at and now - row.wiki.updated_at > OLD_WIKI_AGE)
            if state == "present" and not row.wiki.exists:
                continue
            if state == "missing" and (row.wiki.exists or row.wiki.error):
                continue
            if state == "old" and not is_old:
                continue
            words = row.wiki.word_count
            if length == "empty" and not (row.wiki.exists and words == 0):
                continue
            if length == "short" and not (
                row.wiki.exists and words is not None and 1 <= words <= SHORT_WIKI_MAX_WORDS
            ):
                continue
            if length == "non_empty" and not (
                row.wiki.exists and words is not None and words > 0
            ):
                continue
            result.append(row)
        return result

    def apply_filters(self, *_args) -> None:
        rows = self._visible_rows()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        definition = site_definition("gelbooru")
        headers = (
            "tag", "category", "posts", "alias", "wiki", "words", "date", "version", "author"
        )
        for row_index, row in enumerate(rows):
            alias = row.canonical_name or row.direct_alias or ""
            wiki = self.catalog.text(
                "wiki_audit.has_wiki" if row.wiki.exists else "wiki_audit.no_wiki"
            )
            if row.wiki.error:
                wiki = self.catalog.text(
                    "wiki_audit.parse_error"
                    if row.wiki.error_kind == "parse"
                    else "wiki_audit.network_error"
                )
            values = (
                row.name,
                definition.categories.get(row.category, "Unknown").title(),
                row.post_count,
                alias,
                wiki,
                row.wiki.word_count if row.wiki.word_count is not None else "—",
                row.wiki.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
                if row.wiki.updated_at
                else "",
                row.wiki.version if row.wiki.version is not None else "",
                row.wiki.updated_by or "",
            )
            for column, value in enumerate(values):
                item = WordCountItem() if column == 5 else QTableWidgetItem()
                item.setData(Qt.ItemDataRole.DisplayRole, value)
                item.setData(ROW_ROLE, row)
                if column == 5:
                    item.setData(WORDS_SORT_ROLE, row.wiki.word_count)
                    if row.wiki.exists and row.wiki.word_count == 0:
                        item.setToolTip(
                            self.catalog.text("wiki_audit.empty_wiki_tooltip")
                        )
                if row.wiki.error:
                    item.setToolTip(row.wiki.error)
                self.table.setItem(row_index, column, item)
        self.table.setHorizontalHeaderLabels(
            [self.catalog.text(f"wiki_audit.column_{key}") for key in headers]
        )
        self.table.setSortingEnabled(True)
        if self.result is not None:
            total, present, missing, errors, suspicious = summarize(self.result.tags)
            self.status.setText(
                self.catalog.text(
                    "wiki_audit.summary",
                    total=total,
                    present=present,
                    missing=missing,
                    errors=errors,
                    suspicious=suspicious,
                    visible=len(rows),
                )
            )

    def _selected(self) -> AuditTag | None:
        item = self.table.item(self.table.currentRow(), 0) if self.table.currentRow() >= 0 else None
        value = item.data(ROW_ROLE) if item else None
        return value if isinstance(value, AuditTag) else None

    def read_selected(self) -> None:
        row = self._selected()
        if row is None:
            return
        if not row.wiki.exists:
            self.reader.setPlainText(self.catalog.text("wiki_audit.no_wiki"))
            self._reader_url = ""
            return
        content = row.wiki.content or self.catalog.text("wiki_audit.empty_wiki")
        if row.wiki.content and row.wiki.content_format == "html":
            self.reader.setHtml(sanitize_wiki_html(row.wiki.content))
        else:
            self.reader.setPlainText(content)
        self._reader_url = row.wiki.source_url

    def open_selected(self) -> None:
        row = self._selected()
        if row is None:
            return
        if row.wiki.wiki_id:
            url = f"https://gelbooru.com/index.php?page=wiki&s=view&id={row.wiki.wiki_id}"
        else:
            query = urllib.parse.urlencode({"page": "wiki", "s": "list", "search": row.name})
            url = f"https://gelbooru.com/index.php?{query}"
        if self.browser_launcher:
            self.browser_launcher.open(url)
        else:
            QDesktopServices.openUrl(QUrl(url))

    def retranslate(self) -> None:
        text = self.catalog.text
        self.title.setText(text("nav.wiki_audit"))
        self.reference.setPlaceholderText(text("wiki_audit.reference_placeholder"))
        self.load_button.setText(text("wiki_audit.load"))
        self.random_button.setText(text("wiki_audit.random"))
        self.refresh_button.setText(text("wiki_audit.refresh"))
        self.state_label.setText(text("wiki_audit.state"))
        self.category_label.setText(text("wiki_audit.category"))
        self.minimum_label.setText(text("wiki_audit.minimum"))
        self.length_label.setText(text("wiki_audit.length"))
        self.search_label.setText(text("wiki_audit.search"))
        self.minimum.setToolTip(text("wiki_audit.minimum_tip"))
        self.search.setPlaceholderText(text("wiki_audit.search_placeholder"))
        for index, key in enumerate(("all", "present", "missing", "old")):
            self.state_filter.setItemText(index, text(f"wiki_audit.filter_{key}"))
        for index, key in enumerate(("all", "empty", "short", "non_empty")):
            self.length_filter.setItemText(index, text(f"wiki_audit.length_{key}"))
        self.category_filter.setItemText(0, text("wiki_audit.filter_all"))
        self.ignore_metadata.setText(text("wiki_audit.ignore_metadata"))
        self.ignore_deprecated.setText(text("wiki_audit.ignore_deprecated"))
        self.read_button.setText(text("wiki_audit.read"))
        self.open_button.setText(text("wiki_audit.open"))
        self.reader_refresh.setText(text("wiki_audit.refresh_wiki"))
        self.table.set_empty_text(text("wiki_audit.empty"))
