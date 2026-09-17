"""Image Analysis source queue and human-review page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QEvent, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QMovie, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from booruflow.domain.image_analysis import DecisionState, TagObservation
from booruflow.infrastructure.localization import LanguageCatalog


class ScaledImageLabel(QScrollArea):
    def __init__(self) -> None:
        super().__init__()
        self.label = QLabel(); self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWidget(self.label); self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(260, 220)
        self._source = QPixmap()
        self._source_path: Path | None = None
        self._movie: QMovie | None = None
        self._zoom = 0

    def set_image(self, path: Path | None) -> None:
        if self._movie is not None:
            self._movie.stop(); self._movie = None; self.label.setMovie(None)
        if path is not None and path == self._source_path and not self._source.isNull():
            return
        self._source_path = path
        self._source = QPixmap(str(path)) if path else QPixmap()
        self._render()

    def set_animated_image(self, path: Path | None) -> bool:
        if path is not None and path == self._source_path and self._movie is not None:
            return True
        self.set_image(None)
        if path is None or path.suffix.casefold() != ".gif": return False
        movie = QMovie(str(path))
        if not movie.isValid(): return False
        self._movie = movie; self.label.setMovie(movie); movie.start(); self._source_path = path
        return True

    def set_zoom(self, percent: int) -> None:
        self._zoom = max(0, int(percent)); self._render()

    def zoom(self) -> int:
        return self._zoom

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render()

    def _render(self) -> None:
        if self._source.isNull():
            self.label.clear(); self.label.resize(self.viewport().size())
            return
        if self._zoom:
            target = self._source.size() * (self._zoom / 100.0)
            pixmap = self._source.scaled(
                target, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.label.resize(pixmap.size())
        else:
            pixmap = self._source.scaled(
                self.viewport().size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.label.resize(self.viewport().size())
        self.label.setPixmap(pixmap)


class CompactActionButton(QPushButton):
    """Keep a readable non-zero minimum while allowing the persistent bar to shrink."""

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(max(54, round(super().sizeHint().width() * 0.65)))
        return hint


class ObservationTableModel(QAbstractTableModel):
    decision_requested = Signal(int, object, object)

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[int, TagObservation]] = []
        self.all_rows: list[tuple[int, TagObservation]] = []
        self.headers = ("Tag", "Category", "Source", "Confidence", "Decision")
        self.category = "all"; self.decision = "unreviewed"
        self.threshold = 0.30; self.show_source_present = False
        self.sort_column = 3; self.sort_order = Qt.SortOrder.DescendingOrder

    def set_headers(self, headers: tuple[str, str, str, str, str]) -> None:
        self.headers = headers
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, 4)

    def set_rows(self, rows: list[tuple[int, TagObservation]]) -> None:
        self.all_rows = list(rows); self.apply_filters()

    def set_filters(
        self, category: str, decision: str, threshold: float, show_source_present: bool
    ) -> None:
        self.category = category; self.decision = decision; self.threshold = threshold
        self.show_source_present = show_source_present; self.apply_filters()

    def apply_filters(self) -> None:
        rows = []
        for row in self.all_rows:
            observation = row[1]
            if observation.category == "rating":
                continue
            if self.category != "all" and observation.category != self.category:
                continue
            if self.decision != "all" and observation.decision.value != self.decision:
                continue
            if observation.confidence is not None and observation.confidence < self.threshold:
                continue
            if observation.source_present and not self.show_source_present:
                continue
            rows.append(row)
        def sort_key(value):
            observation = value[1]
            values = (
                (observation.reviewed_name or observation.name).casefold(),
                (observation.category or "").casefold(),
                observation.source.value,
                observation.confidence if observation.confidence is not None else -1.0,
                observation.decision.value,
            )
            return values[self.sort_column]
        rows.sort(key=sort_key, reverse=self.sort_order is Qt.SortOrder.DescendingOrder)
        self.beginResetModel(); self.rows = rows; self.endResetModel()

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder) -> None:
        self.sort_column = column; self.sort_order = order; self.apply_filters()

    def rowCount(self, _parent=None) -> int:
        return len(self.rows)

    def columnCount(self, _parent=None) -> int:
        return 5

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in {
            Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole,
        }:
            return None
        _id, observation = self.rows[index.row()]
        values = (
            observation.reviewed_name or observation.name,
            observation.category or "—",
            observation.source.value,
            "" if observation.confidence is None else f"{observation.confidence:.3f}",
            observation.decision.value,
        )
        return values[index.column()]

    def flags(self, index):
        flags = super().flags(index)
        return flags | Qt.ItemFlag.ItemIsEditable if index.column() == 0 else flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or index.column() != 0 or not str(value).strip():
            return False
        observation_id, observation = self.rows[index.row()]
        self.decision_requested.emit(observation_id, observation.decision, str(value).strip())
        return True


class ImageAnalysisPage(QWidget):
    local_files_requested = Signal(list)
    local_sources_dropped = Signal(list)
    remote_ids_requested = Signal(object, list)
    complete_requested = Signal()
    skip_requested = Signal()
    retry_requested = Signal(int)
    manual_tag_requested = Signal(str)
    observation_decision_requested = Signal(int, object, object)
    bulk_observation_decision_requested = Signal(list, object)
    wd14_install_requested = Signal()
    gpu_runtime_install_requested = Signal()
    item_open_requested = Signal(int)
    item_requeue_requested = Signal(int)
    queue_navigation_requested = Signal(int)
    queue_filter_changed = Signal(str)
    queue_cleanup_requested = Signal(str)

    def __init__(self, catalog: LanguageCatalog) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.catalog = catalog
        self.current_item_id: int | None = None
        self._decision_anchor: int | None = None
        self._source_rows = []
        self._visible_source_ids: list[int] = []
        self._current_state = ""
        root = QVBoxLayout(self); root.setContentsMargins(20, 16, 20, 20)
        self.title = QLabel(); self.title.setStyleSheet("font-size:22px;font-weight:600")
        root.addWidget(self.title)
        self.drop_banner = QLabel("Déposer les images pour les ajouter")
        self.drop_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_banner.setStyleSheet(
            "padding:12px;border:2px dashed #55aaff;background:#20364a;color:white;"
            "font-size:16px;font-weight:600"
        )
        self.drop_banner.hide(); root.addWidget(self.drop_banner)

        source_group = QGroupBox(); source_layout = QVBoxLayout(source_group)
        source_group.setMaximumHeight(180)
        queue_controls = QHBoxLayout()
        self.queue_filter = QComboBox()
        for label, value in (
            ("À traiter", "active"), ("Prêtes à revoir", "ready"),
            ("Révisées", "reviewed"), ("Ignorées", "skipped"),
            ("Erreurs", "failed"), ("Toutes", "all"),
        ):
            self.queue_filter.addItem(label, value)
        self.queue_empty = QLabel(); self.queue_empty.setWordWrap(True)
        self.queue_cleanup = QPushButton("Nettoyer la file…")
        cleanup_menu = QMenu(self.queue_cleanup)
        for label, mode in (
            ("Retirer les révisées de la liste", "reviewed"),
            ("Retirer les ignorées de la liste", "skipped"),
            ("Retirer les terminées", "finished"),
            ("Vider la file active…", "active"),
        ):
            action = QAction(label, cleanup_menu)
            action.triggered.connect(
                lambda _checked=False, value=mode: self.queue_cleanup_requested.emit(value)
            )
            cleanup_menu.addAction(action)
        self.queue_cleanup.setMenu(cleanup_menu)
        queue_controls.addWidget(QLabel("File :")); queue_controls.addWidget(self.queue_filter)
        queue_controls.addWidget(self.queue_empty, 1); queue_controls.addWidget(self.queue_cleanup)
        source_layout.addLayout(queue_controls)
        source_actions = QHBoxLayout()
        self.local_button = QPushButton()
        self.gelbooru_ids = QLineEdit(); self.gelbooru_ids.setPlaceholderText("Gelbooru IDs")
        self.gelbooru_button = QPushButton("+")
        self.e621_ids = QLineEdit(); self.e621_ids.setPlaceholderText("e621 IDs")
        self.e621_button = QPushButton("+")
        for widget in (self.local_button, self.gelbooru_ids, self.gelbooru_button,
                       self.e621_ids, self.e621_button):
            source_actions.addWidget(widget)
        source_layout.addLayout(source_actions)
        self.source_table = QTableWidget(0, 5)
        self.source_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.source_table.setMaximumHeight(90)
        source_layout.addWidget(self.source_table)
        root.addWidget(source_group)

        splitter = QSplitter(Qt.Orientation.Horizontal); self.central_splitter = splitter
        splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        preview = QWidget(); preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self.current_banner = QLabel("En attente de la prochaine analyse…")
        self.current_banner.setWordWrap(True)
        self.current_banner.setStyleSheet("font-size:15px;font-weight:600;padding:6px")
        preview_layout.addWidget(self.current_banner)
        self.image = ScaledImageLabel(); preview_layout.addWidget(self.image, 1)
        navigation = QHBoxLayout()
        self.previous_item = QPushButton("← Précédente")
        self.next_item = QPushButton("Suivante →")
        self.requeue_item = QPushButton("Remettre dans la file")
        self.zoom = QComboBox()
        for label, value in (("Ajuster", 0), ("100 %", 100), ("200 %", 200), ("400 %", 400)):
            self.zoom.addItem(label, value)
        for button in (self.previous_item, self.next_item, self.requeue_item):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        navigation.addWidget(self.previous_item); navigation.addWidget(self.next_item)
        navigation.addWidget(self.zoom)
        navigation.addStretch(1); navigation.addWidget(self.requeue_item)
        preview_layout.addLayout(navigation); splitter.addWidget(preview)
        side = QWidget(); side_layout = QVBoxLayout(side); side_layout.setContentsMargins(8, 0, 0, 0)
        right_splitter = QSplitter(Qt.Orientation.Vertical); self.right_splitter = right_splitter
        tags_panel = QWidget(); tags_layout = QVBoxLayout(tags_panel); tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_filter = QLineEdit(); self.tags_filter.setPlaceholderText("Filtrer les tags source")
        self.source_tags = QListWidget()
        self.source_tags.setMinimumHeight(80)
        self.source_tags.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        tags_layout.addWidget(self.tags_filter); tags_layout.addWidget(self.source_tags, 1)
        right_splitter.addWidget(tags_panel)
        suggestions_panel = QWidget(); suggestions_layout = QVBoxLayout(suggestions_panel)
        suggestions_layout.setContentsMargins(0, 0, 0, 0)
        self.observation_model = ObservationTableModel()
        self.observations = QTableView(); self.observations.setModel(self.observation_model)
        self.observations.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.observations.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.observations.setSortingEnabled(True)
        self.observations.sortByColumn(3, Qt.SortOrder.DescendingOrder)
        filters = QGridLayout()
        self.category_filter = QComboBox()
        for label, value in (("Toutes catégories", "all"), ("Général", "general"),
                             ("Personnage", "character")):
            self.category_filter.addItem(label, value)
        self.decision_filter = QComboBox()
        for label, value in (("À examiner", "unreviewed"), ("Acceptés", "accepted"),
                             ("Rejetés", "rejected"), ("Tous", "all")):
            self.decision_filter.addItem(label, value)
        self.display_threshold = QDoubleSpinBox(); self.display_threshold.setRange(0, 1)
        self.display_threshold.setDecimals(2); self.display_threshold.setSingleStep(0.05)
        self.show_source_present = QCheckBox("Déjà dans la source")
        for index, widget in enumerate((
            self.category_filter, self.decision_filter, self.display_threshold,
            self.show_source_present,
        )):
            filters.addWidget(widget, index // 2, index % 2)
        suggestions_layout.addLayout(filters)
        self.observations.setMinimumHeight(120)
        self.observations.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        suggestions_layout.addWidget(self.observations, 1)
        right_splitter.addWidget(suggestions_panel)
        right_splitter.setStretchFactor(0, 1); right_splitter.setStretchFactor(1, 2)
        side_layout.addWidget(right_splitter, 1)
        self.manual_tag = QLineEdit(); self.manual_add = CompactActionButton()
        self.accept = CompactActionButton(); self.reject = CompactActionButton()
        self.accept_above = CompactActionButton("Accepter ≥ seuil")
        splitter.addWidget(side); splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2); root.addWidget(splitter, 1)

        self.statistics = QLabel(); self.statistics.setWordWrap(True)
        self.statistics.setMaximumHeight(28); root.addWidget(self.statistics)
        self.pipeline = QLabel(); self.pipeline.setWordWrap(True)
        self.pipeline.setMaximumHeight(28); root.addWidget(self.pipeline)
        self.worker_state = QLabel(); root.addWidget(self.worker_state)
        self.drop_status = QLabel(); self.drop_status.setWordWrap(True); root.addWidget(self.drop_status)
        wd14_row = QHBoxLayout()
        self.wd14_state = QLabel("WD14 : diagnostic en attente")
        self.wd14_install = QPushButton("Installer / réinstaller WD14…")
        self.gpu_runtime_install = QPushButton("Installer le runtime GPU…")
        wd14_row.addWidget(self.wd14_state, 1)
        wd14_row.addWidget(self.gpu_runtime_install); wd14_row.addWidget(self.wd14_install)
        root.addLayout(wd14_row)
        self.action_bar = QFrame(); self.action_bar.setFrameShape(QFrame.Shape.StyledPanel)
        self.action_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        actions = QHBoxLayout(self.action_bar); actions.setContentsMargins(8, 6, 8, 6)
        self.retry_button = CompactActionButton(); self.skip_button = CompactActionButton()
        self.complete_button = CompactActionButton()
        for button in (self.manual_add, self.accept, self.reject, self.accept_above,
                       self.retry_button, self.skip_button, self.complete_button):
            button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        for widget in (self.manual_tag, self.manual_add, self.accept, self.reject,
                       self.accept_above): actions.addWidget(widget)
        actions.addStretch(1)
        actions.addWidget(self.retry_button); actions.addWidget(self.skip_button)
        actions.addWidget(self.complete_button); root.addWidget(self.action_bar, 0)

        self.local_button.clicked.connect(self._choose_files)
        self.gelbooru_button.clicked.connect(lambda: self._emit_ids("gelbooru"))
        self.e621_button.clicked.connect(lambda: self._emit_ids("e621"))
        self.manual_add.clicked.connect(lambda: self.manual_tag_requested.emit(self.manual_tag.text()))
        self.complete_button.clicked.connect(self.complete_requested.emit)
        self.skip_button.clicked.connect(self.skip_requested.emit)
        self.retry_button.clicked.connect(self._retry_selected)
        self.source_table.cellDoubleClicked.connect(self._open_source_row)
        self.queue_filter.currentIndexChanged.connect(self._filter_source_rows)
        self.queue_filter.currentIndexChanged.connect(
            lambda: self.queue_filter_changed.emit(str(self.queue_filter.currentData()))
        )
        self.previous_item.clicked.connect(lambda: self._navigate_source(-1))
        self.next_item.clicked.connect(lambda: self._navigate_source(1))
        self.zoom.currentIndexChanged.connect(
            lambda: self.image.set_zoom(int(self.zoom.currentData()))
        )
        self.requeue_item.clicked.connect(self._requeue_current)
        self.accept.clicked.connect(lambda: self._decide_selected(DecisionState.ACCEPTED))
        self.reject.clicked.connect(lambda: self._decide_selected(DecisionState.REJECTED))
        self.accept_shortcut = QShortcut(QKeySequence("A"), self.observations)
        self.reject_shortcut = QShortcut(QKeySequence("R"), self.observations)
        for shortcut in (self.accept_shortcut, self.reject_shortcut):
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.accept_shortcut.activated.connect(
            lambda: self._decide_selected(DecisionState.ACCEPTED)
        )
        self.reject_shortcut.activated.connect(
            lambda: self._decide_selected(DecisionState.REJECTED)
        )
        self.accept_above.clicked.connect(self._accept_above_threshold)
        self.observation_model.decision_requested.connect(self.observation_decision_requested)
        self.observations.selectionModel().selectionChanged.connect(self._update_action_states)
        self.tags_filter.textChanged.connect(self._filter_tags)
        self.category_filter.currentIndexChanged.connect(self._apply_observation_filters)
        self.decision_filter.currentIndexChanged.connect(self._apply_observation_filters)
        self.display_threshold.valueChanged.connect(self._apply_observation_filters)
        self.show_source_present.toggled.connect(self._apply_observation_filters)
        self.wd14_install.clicked.connect(self.wd14_install_requested.emit)
        self.gpu_runtime_install.clicked.connect(self.gpu_runtime_install_requested.emit)
        self.retranslate()
        button_height = max(
            button.sizeHint().height() for button in (
                self.manual_add, self.accept, self.reject, self.accept_above,
                self.retry_button, self.skip_button, self.complete_button,
            )
        )
        required_bar_height = button_height + actions.contentsMargins().top() \
            + actions.contentsMargins().bottom()
        self.action_bar.setMinimumHeight(max(required_bar_height, self.action_bar.sizeHint().height()))
        self.installEventFilter(self)
        for widget in self.findChildren(QWidget):
            widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() != QEvent.Type.KeyPress or event.key() != Qt.Key.Key_Space:
            return super().eventFilter(watched, event)
        widget = watched if isinstance(watched, QWidget) else None
        while widget is not None and widget is not self:
            if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
                return super().eventFilter(watched, event)
            widget = widget.parentWidget()
        if event.modifiers() == Qt.KeyboardModifier.NoModifier:
            if not event.isAutoRepeat() and self.complete_button.isEnabled():
                self.complete_button.click()
            return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _local_drop_paths(mime_data) -> list[str]:
        if not mime_data.hasUrls(): return []
        result = []
        for url in mime_data.urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if path: result.append(path)
        return result

    def dragEnterEvent(self, event) -> None:
        if self._local_drop_paths(event.mimeData()):
            self.drop_banner.show(); event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._local_drop_paths(event.mimeData()): event.acceptProposedAction()
        else: event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self.drop_banner.hide(); event.accept()

    def dropEvent(self, event) -> None:
        paths = self._local_drop_paths(event.mimeData()); self.drop_banner.hide()
        if not paths:
            event.ignore(); return
        self.local_sources_dropped.emit(paths); event.acceptProposedAction()

    def _choose_files(self) -> None:
        paths, _selected = QFileDialog.getOpenFileNames(
            self, self.catalog.text("image_analysis.choose_images"), "",
            self.catalog.text("image_analysis.image_filter"),
        )
        if paths: self.local_files_requested.emit(paths)

    def _emit_ids(self, site: str) -> None:
        edit = self.gelbooru_ids if site == "gelbooru" else self.e621_ids
        values = [value for value in edit.text().replace(",", " ").split() if value]
        if values: self.remote_ids_requested.emit(site, values); edit.clear()

    def _retry_selected(self) -> None:
        if self.current_item_id is not None and self._current_state == "failed":
            self.retry_requested.emit(self.current_item_id); return
        row = self.source_table.currentRow()
        if row >= 0: self.retry_requested.emit(int(self.source_table.item(row, 0).text()))

    def _update_action_states(self, *_args) -> None:
        reviewable = self._current_state == "ready_for_review"
        editable = reviewable or self._current_state == "reviewed"
        selected = bool(self.observations.selectionModel().selectedRows())
        self.accept.setEnabled(editable and selected)
        self.reject.setEnabled(editable and selected)
        self.accept_above.setEnabled(reviewable)
        self.manual_tag.setEnabled(editable)
        self.manual_add.setEnabled(editable)
        self.retry_button.setEnabled(self._current_state == "failed")
        self.skip_button.setEnabled(reviewable)
        self.complete_button.setEnabled(reviewable)
        self.requeue_item.setEnabled(self._current_state == "skipped")

    def _open_source_row(self, row: int, _column: int) -> None:
        item = self.source_table.item(row, 0)
        if item: self.item_open_requested.emit(int(item.text()))

    def _navigate_source(self, offset: int) -> None:
        if not self._visible_source_ids: return
        try: position = self._visible_source_ids.index(self.current_item_id)
        except ValueError: position = -1 if offset > 0 else len(self._visible_source_ids)
        target = position + offset
        if 0 <= target < len(self._visible_source_ids):
            self.queue_navigation_requested.emit(self._visible_source_ids[target])

    def _requeue_current(self) -> None:
        if self.current_item_id is not None:
            self.item_requeue_requested.emit(self.current_item_id)

    def _decide_selected(self, decision: DecisionState) -> None:
        rows = sorted({index.row() for index in self.observations.selectionModel().selectedRows()})
        ids = [self.observation_model.rows[row][0] for row in rows]
        if ids:
            self._decision_anchor = (
                rows[0]
                if self.observation_model.decision == DecisionState.UNREVIEWED.value
                else rows[-1] + 1
            )
            self.bulk_observation_decision_requested.emit(ids, decision)

    def _accept_above_threshold(self) -> None:
        ids = [row_id for row_id, observation in self.observation_model.all_rows
               if observation.category != "rating"
               and observation.decision is DecisionState.UNREVIEWED
               and not observation.source_present
               and (observation.confidence or 0) >= self.display_threshold.value()]
        if ids:
            self.bulk_observation_decision_requested.emit(ids, DecisionState.ACCEPTED)

    def set_display_threshold(self, value: float) -> None:
        self.display_threshold.setValue(value)

    def _apply_observation_filters(self) -> None:
        self.observation_model.set_filters(
            str(self.category_filter.currentData()), str(self.decision_filter.currentData()),
            self.display_threshold.value(), self.show_source_present.isChecked(),
        )

    def _filter_tags(self, value: str) -> None:
        folded = value.casefold()
        for index in range(self.source_tags.count()):
            item = self.source_tags.item(index); item.setHidden(folded not in item.text().casefold())

    def show_sources(self, rows) -> None:
        self._source_rows = list(rows); self._filter_source_rows()

    def _filter_source_rows(self) -> None:
        mode = str(self.queue_filter.currentData())
        def included(row) -> bool:
            state = str(row["state"])
            if mode == "active": return state not in {"reviewed", "skipped"}
            if mode == "ready": return state == "ready_for_review"
            if mode == "reviewed": return state == "reviewed"
            if mode == "skipped": return state == "skipped"
            if mode == "failed": return state == "failed" or row["source_state"] == "failed"
            return True
        rows = [row for row in self._source_rows if included(row)]
        self._visible_source_ids = [int(row["id"]) for row in rows]
        self.source_table.setRowCount(len(rows))
        current_row = -1
        for row_index, row in enumerate(rows):
            source = row["original_path"] or f'{row["source_site"]}:{row["source_post_id"]}'
            if row["linked_site"]:
                state_label = {
                    "resolved": "métadonnées chargées",
                    "failed": "métadonnées indisponibles",
                    "pending": "métadonnées en attente",
                }.get(row["enrichment_state"], row["enrichment_state"])
                source = f'{source} · {row["linked_site"]} #{row["linked_post_id"]} — {state_label}'
            state = row["source_state"] if row["source_state"] != "resolved" else row["state"]
            if row["review_active"]: state = "review_active"
            post = (f'{row["linked_site"]} #{row["linked_post_id"]}' if row["linked_site"]
                    else (f'{row["source_site"]} #{row["source_post_id"]}'
                          if row["source_site"] else "—"))
            values = (row["id"], row["artist_tags"] or "—", post, state, row["last_error"] or "")
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value)); cell.setToolTip(str(source))
                self.source_table.setItem(row_index, column, cell)
            if int(row["id"]) == self.current_item_id: current_row = row_index
        if current_row >= 0:
            self.source_table.selectRow(current_row)
            self.source_table.scrollToItem(self.source_table.item(current_row, 0))
        counts = {state: sum(str(row["state"]) == state for row in self._source_rows)
                  for state in ("reviewed", "skipped", "failed")}
        remaining = sum(str(row["state"]) not in {"reviewed", "skipped"}
                        for row in self._source_rows)
        if not rows:
            self.queue_empty.setText(
                f"Aucun élément · {remaining} restante(s) · {counts['reviewed']} révisée(s) · "
                f"{counts['skipped']} ignorée(s) · {counts['failed']} erreur(s)"
            )
        else: self.queue_empty.clear()
        self._update_current_banner()

    def show_review(self, item, tags, observations, statistics) -> None:
        self.current_item_id = item.id if item else None
        self.image.set_image(item.cached_path if item else None)
        self.source_tags.clear()
        for tag in tags:
            self.source_tags.addItem(f"{tag.source.value} · {tag.category or '—'} · {tag.name}")
        self.observation_model.set_rows(observations)
        if self._decision_anchor is not None:
            if self.observation_model.rowCount():
                row = min(self._decision_anchor, self.observation_model.rowCount() - 1)
                self.observations.selectRow(row)
                self.observations.setCurrentIndex(self.observation_model.index(row, 0))
            else:
                self.observations.clearSelection()
            self.observations.setFocus(Qt.FocusReason.OtherFocusReason)
            self._decision_anchor = None
        if statistics:
            self.statistics.setText(
                f"L={statistics.mean_luminance:.3f} · S={statistics.mean_saturation:.3f} · "
                f"Contraste={statistics.contrast:.3f} · Pastel={statistics.pastel_score:.3f} · "
                f"Palette: {', '.join(statistics.dominant_colors)}"
            )
        else: self.statistics.clear()
        item_state = getattr(getattr(item, "state", None), "value", "ready_for_review")
        self._current_state = item_state if item else ""
        self.requeue_item.setVisible(True)
        self._update_action_states()
        self._filter_source_rows(); self._update_current_banner(item)

    def _update_current_banner(self, item=None) -> None:
        if item is None and self.current_item_id is None:
            self.current_banner.setText("En attente de la prochaine analyse…")
            return
        row = next((row for row in self._source_rows
                    if int(row["id"]) == self.current_item_id), None)
        if row is None: return
        title = str(row["artist_tags"] or "").split(",", 1)[0].strip()
        if not title:
            title = Path(str(row["original_path"] or row["cached_path"] or f'Image {row["id"]}')).name
        post = (f'{row["linked_site"].title()} #{row["linked_post_id"]}' if row["linked_site"]
                else (f'{row["source_site"].title()} #{row["source_post_id"]}'
                      if row["source_site"] else "Source locale"))
        try: position = self._visible_source_ids.index(self.current_item_id) + 1
        except ValueError: position = 0
        remaining = sum(str(value["state"]) not in {"reviewed", "skipped"}
                        for value in self._source_rows)
        reviewed = sum(str(value["state"]) == "reviewed" for value in self._source_rows)
        ignored = sum(str(value["state"]) == "skipped" for value in self._source_rows)
        visible = f"{position} / {len(self._visible_source_ids)} • " if position else ""
        self.current_banner.setText(
            f"{title} — {post}\n{visible}"
            f"{'review_active' if row['review_active'] else row['state']} • "
            f"Restantes : {remaining} • Révisées : {reviewed} • Ignorées : {ignored}"
        )

    def retranslate(self) -> None:
        text = self.catalog.text
        self.title.setText(text("nav.image_analysis"))
        self.local_button.setText(text("image_analysis.add_files"))
        self.manual_add.setText(text("image_analysis.add_manual"))
        self.accept.setText(text("image_analysis.accept")); self.reject.setText(text("image_analysis.reject"))
        self.retry_button.setText(text("image_analysis.retry")); self.skip_button.setText(text("image_analysis.skip"))
        shortcut_name = "Espace" if self.catalog.code == "fr" else "Space"
        complete_text = text("image_analysis.complete")
        self.complete_button.setText(f"{complete_text} [{shortcut_name}]")
        self.complete_button.setToolTip(
            f"{shortcut_name} — {complete_text}"
        )
        self.source_table.setHorizontalHeaderLabels((
            "ID", "Artiste", "Post", text("image_analysis.column_state"),
            text("image_analysis.column_error"),
        ))
        self.observation_model.set_headers((
            text("image_analysis.column_tag"), "Category", text("image_analysis.column_source"),
            text("image_analysis.column_confidence"), text("image_analysis.column_decision"),
        ))
