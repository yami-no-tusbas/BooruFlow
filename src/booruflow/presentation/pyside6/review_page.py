"""PySide6 category review page."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from booruflow.application.review import ReviewRequest
from booruflow.infrastructure.localization import LanguageCatalog


class ReviewPage(QWidget):
    start_requested = Signal(object)
    stop_requested = Signal()
    count_requested = Signal(tuple)

    def __init__(
        self,
        catalog: LanguageCatalog,
        settings: dict[str, object],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.settings = settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 24)
        layout.setSpacing(12)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(self.title)

        self.search_group = QGroupBox()
        search_layout = QVBoxLayout(self.search_group)
        selectors = QHBoxLayout()
        self.site_label = QLabel()
        self.site = QComboBox()
        self.site.addItem("Gelbooru", ("gelbooru",))
        self.site.addItem("e621", ("e621",))
        self.site.addItem("Gelbooru + e621", ("gelbooru", "e621"))
        self.entity_label = QLabel()
        self.entity = QComboBox()
        for key in ("artists", "copyrights", "characters", "species"):
            self.entity.addItem(key, key)
        selectors.addWidget(self.site_label)
        selectors.addWidget(self.site)
        selectors.addSpacing(18)
        selectors.addWidget(self.entity_label)
        selectors.addWidget(self.entity)
        selectors.addStretch(1)
        search_layout.addLayout(selectors)
        self.query_label = QLabel()
        search_layout.addWidget(self.query_label)
        self.queries = QPlainTextEdit()
        self.queries.setMaximumHeight(105)
        self.queries.setPlaceholderText("rating:general\nblue_hair solo")
        search_layout.addWidget(self.queries)
        layout.addWidget(self.search_group)

        self.criteria_group = QGroupBox()
        criteria = QGridLayout(self.criteria_group)
        self.pages = self._spin(1, 100, 10)
        self.start_page = self._spin(1, 1_000_000, 1)
        self.minimum_results = self._spin(0, 100_000_000, 0)
        self.maximum_results = self._spin(0, 100_000_000, 0)
        self.match_percent = self._spin(0, 100, 0)
        self.criteria_labels = [QLabel() for _ in range(5)]
        widgets = [self.pages, self.start_page, self.minimum_results, self.maximum_results, self.match_percent]
        for index, (label, widget) in enumerate(zip(self.criteria_labels, widgets, strict=True)):
            row, column = divmod(index, 3)
            cell = QHBoxLayout()
            cell.addWidget(label)
            cell.addWidget(widget)
            if index == 4:
                cell.addWidget(QLabel("%"))
            criteria.addLayout(cell, row, column)
        self.remember = QCheckBox()
        criteria.addWidget(self.remember, 2, 0, 1, 3)
        self.example = QLabel()
        self.example.setWordWrap(True)
        criteria.addWidget(self.example, 3, 0, 1, 3)
        self.minimum_results.valueChanged.connect(self._update_example)
        self.match_percent.valueChanged.connect(self._update_example)
        layout.addWidget(self.criteria_group)

        actions = QHBoxLayout()
        self.count_button = QPushButton()
        self.start_button = QPushButton()
        self.stop_button = QPushButton()
        self.stop_button.setEnabled(False)
        self.count_button.clicked.connect(self._count)
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        actions.addWidget(self.count_button)
        actions.addStretch(1)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        layout.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        layout.addWidget(self.progress)
        self.state = QLabel()
        self.state.setContentsMargins(2, 4, 2, 4)
        layout.addWidget(self.state)
        layout.addStretch(1)
        self.retranslate()

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(value)
        widget.setAccelerated(True)
        return widget

    def query_values(self) -> tuple[str, ...]:
        values: list[str] = []
        seen: set[str] = set()
        for line in self.queries.toPlainText().replace(";", "\n").splitlines():
            value = line.strip()
            if value and value not in seen:
                seen.add(value)
                values.append(value)
        return tuple(values)

    def build_request(self) -> ReviewRequest:
        sites = tuple(self.site.currentData())
        entity = str(self.entity.currentData())
        gel_db = Path(str(self.settings.get("gelbooru_database", "")))
        e621_db = Path(str(self.settings.get("e621_database", "")))
        grabber_value = str(self.settings.get("grabber_directory", "")).strip()
        return ReviewRequest(
            queries=self.query_values(),
            sites=sites,
            entity_type=entity,
            pages=self.pages.value(),
            start_page=self.start_page.value(),
            minimum_results=self.minimum_results.value(),
            maximum_results=self.maximum_results.value(),
            match_percent=self.match_percent.value(),
            remember_queries=self.remember.isChecked(),
            gelbooru_database=gel_db,
            e621_database=e621_db,
            output_root=Path(str(self.settings.get("output_root", "var/results"))),
            grabber_directory=Path(grabber_value) if grabber_value else None,
        )

    def _start(self) -> None:
        try:
            request = self.build_request()
        except ValueError as exc:
            self.state.setText(self.catalog.text("review.invalid", error=exc))
            return
        missing = [
            str(path)
            for site, path in (("gelbooru", request.gelbooru_database), ("e621", request.e621_database))
            if site in request.sites and not path.is_file()
        ]
        if missing:
            self.state.setText(self.catalog.text("review.database_missing", path=missing[0]))
            return
        self.start_requested.emit(request)

    def _count(self) -> None:
        queries = self.query_values()
        if not queries:
            self.state.setText(self.catalog.text("review.invalid", error=self.catalog.text("review.no_query")))
            return
        if "gelbooru" not in tuple(self.site.currentData()):
            self.state.setText(self.catalog.text("review.count_gelbooru_only"))
            return
        self.count_requested.emit(queries)

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.count_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        if running:
            self.state.setText(self.catalog.text("review.running"))

    def set_progress(self, page: int, block: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(block)
        self.progress.setFormat(self.catalog.text("review.progress", page=page, current=block, total=total))

    def set_count_progress(self, current: int, total: int, query: str) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(current)
        self.progress.setFormat(f"{current}/{total}")
        self.state.setText(self.catalog.text("review.counting", current=current, total=total, query=query))

    def _update_example(self) -> None:
        total = self.minimum_results.value() or 100
        percent = self.match_percent.value()
        required = math.ceil(total * percent / 100)
        self.example.setText(self.catalog.text("review.example", total=total, percent=percent, required=required))

    def retranslate(self) -> None:
        text = self.catalog.text
        self.title.setText(text("nav.review"))
        self.search_group.setTitle(text("review.search_group"))
        self.site_label.setText(text("review.site"))
        self.entity_label.setText(text("review.entity"))
        entity_index = self.entity.currentIndex()
        for index, key in enumerate(("artists", "copyrights", "characters", "species")):
            self.entity.setItemText(index, text(f"entity.{key}"))
        self.entity.setCurrentIndex(entity_index)
        self.query_label.setText(text("review.queries"))
        self.criteria_group.setTitle(text("review.criteria"))
        for label, key in zip(self.criteria_labels, ("pages", "start_page", "minimum", "maximum", "match"), strict=True):
            label.setText(text(f"review.{key}"))
        self.remember.setText(text("review.remember"))
        self.count_button.setText(text("review.count"))
        self.start_button.setText(text("review.start"))
        self.stop_button.setText(text("review.stop"))
        if self.start_button.isEnabled():
            self.state.setText(text("review.ready"))
        self._update_example()
