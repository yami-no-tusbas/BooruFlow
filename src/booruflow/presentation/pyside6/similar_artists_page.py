"""Interactive multi-axis Similar Artists exploration page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from booruflow.domain.similar_artists import ArtistIdentity
from booruflow.infrastructure.image_sources import post_page_url
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.image_analysis_page import ScaledImageLabel


def confidence_label(catalog: LanguageCatalog, value: str) -> str:
    return catalog.text(f"similar.confidence.{value}")


class SimilarArtistsPage(QWidget):
    language_refreshed = Signal()
    artist_search_requested = Signal(object)
    item_search_requested = Signal(int)
    local_image_requested = Signal(str)
    update_requested = Signal()
    gallery_requested = Signal(object)
    compare_requested = Signal(object)
    references_added = Signal(list)
    reference_removed = Signal(int)
    references_cleared = Signal()
    remote_requested = Signal(str, str)
    continue_requested = Signal()
    reference_activated = Signal(int)
    corpus_requested = Signal()
    unassigned_examine_requested = Signal()
    references_assign_requested = Signal()
    filename_repair_requested = Signal()
    library_index_requested = Signal(list)
    library_pause_requested = Signal()
    library_cancel_requested = Signal()
    library_resume_requested = Signal()
    remote_discovery_requested = Signal(str, str)
    remote_cancel_requested = Signal()
    artist_open_requested = Signal(object)
    remote_purge_requested = Signal(int)
    local_duplicates_requested = Signal()

    def __init__(self, catalog: LanguageCatalog) -> None:
        super().__init__()
        self.catalog = catalog
        self.setAcceptDrops(True)
        self.artist_options: list[dict] = []
        self.result_rows: list[dict] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 20)
        root.setSpacing(10)
        self.title = QLabel()
        self.title.setStyleSheet("font-size:22px;font-weight:600")
        root.addWidget(self.title)
        self.subtitle = QLabel()
        self.subtitle.setStyleSheet("font-size:17px;font-weight:600")
        root.addWidget(self.subtitle)
        self.drop_zone = QPushButton()
        self.drop_zone.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.drop_zone.setMinimumHeight(105)
        self.drop_zone.setStyleSheet("border:2px dashed #55aaff;padding:20px;font-size:16px")
        self.drop_zone.clicked.connect(self._choose_many)
        root.addWidget(self.drop_zone)
        remote_row = QHBoxLayout()
        self.remote_site = QComboBox()
        self.remote_site.addItem("Gelbooru", "gelbooru")
        self.remote_site.addItem("e621", "e621")
        self.remote_id = QLineEdit()
        self.remote_load = QPushButton()
        self.remote_label = QLabel()
        remote_row.addWidget(self.remote_label)
        remote_row.addWidget(self.remote_site)
        remote_row.addWidget(self.remote_id, 1)
        remote_row.addWidget(self.remote_load)
        root.addLayout(remote_row)
        self.references_group = QGroupBox()
        references_layout = QVBoxLayout(self.references_group)
        self.references = QListWidget()
        self.references.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.references.setViewMode(QListWidget.ViewMode.IconMode)
        self.references.setIconSize(QtCoreSize(110, 90))
        self.references.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.references.setMaximumHeight(170)
        references_layout.addWidget(self.references)
        reference_actions = QHBoxLayout()
        self.add_references = QPushButton()
        self.remove_reference = QPushButton()
        self.clear_references = QPushButton()
        self.continue_button = QPushButton()
        self.continue_button.hide()
        for widget in (
            self.add_references,
            self.remove_reference,
            self.clear_references,
            self.continue_button,
        ):
            reference_actions.addWidget(widget)
        reference_actions.addStretch(1)
        references_layout.addLayout(reference_actions)
        root.addWidget(self.references_group)
        controls = QGroupBox()
        self.advanced_group = controls
        controls.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        controls.setCheckable(True)
        controls.setChecked(False)
        grid = QGridLayout(controls)
        self.mode = QComboBox()
        self.mode.addItem("", "artist")
        self.mode.addItem("", "image")
        self.mode.hide()
        self.backend = QComboBox()
        self.backend.addItem("Author_ID", "author_id_embedding")
        self.backend.addItem("OpenCLIP", "openclip")
        self.minimum_images = QSpinBox()
        self.minimum_images.setRange(1, 10_000)
        self.minimum_images.setValue(2)
        self.limit = QSpinBox()
        self.limit.setRange(1, 200)
        self.limit.setValue(20)
        self.mode_label = QLabel()
        self.backend_label = QLabel()
        self.minimum_images_label = QLabel()
        self.limit_label = QLabel()
        grid.addWidget(self.mode_label, 0, 0)
        grid.addWidget(self.mode, 0, 1)
        grid.addWidget(self.backend_label, 0, 2)
        grid.addWidget(self.backend, 0, 3)
        grid.addWidget(self.minimum_images_label, 0, 4)
        grid.addWidget(self.minimum_images, 0, 5)
        grid.addWidget(self.limit_label, 0, 6)
        grid.addWidget(self.limit, 0, 7)
        self.artist_search = QLineEdit()
        self.artist_list = QListWidget()
        self.artist_list.setMaximumHeight(105)
        self.artist_go = QPushButton()
        grid.addWidget(self.artist_search, 1, 0, 1, 3)
        grid.addWidget(self.artist_go, 1, 3)
        grid.addWidget(self.artist_list, 2, 0, 1, 4)
        self.local_path = QLineEdit()
        self.choose_file = QPushButton()
        self.image_go = QPushButton()
        self.item_id = QSpinBox()
        self.item_id.setRange(0, 2_147_483_647)
        self.item_go = QPushButton("AnalysisItem")
        grid.addWidget(self.local_path, 1, 4, 1, 2)
        grid.addWidget(self.choose_file, 1, 6)
        grid.addWidget(self.image_go, 1, 7)
        self.item_label = QLabel()
        grid.addWidget(self.item_label, 2, 4)
        grid.addWidget(self.item_id, 2, 5)
        grid.addWidget(self.item_go, 2, 6)
        self.purge_days = QSpinBox()
        self.purge_days.setRange(1, 3650)
        self.purge_days.setValue(90)
        self.purge_remote = QPushButton()
        self.purge_days_label = QLabel()
        grid.addWidget(self.purge_days_label, 3, 0)
        grid.addWidget(self.purge_days, 3, 1)
        grid.addWidget(self.purge_remote, 3, 2, 1, 3)
        root.addWidget(controls)
        status_row = QHBoxLayout()
        self.corpus = QLabel()
        self.corpus.setWordWrap(True)
        status_row.addWidget(self.corpus, 1)
        self.update_profiles = QPushButton()
        status_row.addWidget(self.update_profiles)
        root.addLayout(status_row)
        artist_health = QVBoxLayout()
        self.unassigned_status = QLabel()
        artist_health.addWidget(self.unassigned_status)
        artist_actions = QHBoxLayout()
        self.examine_unassigned = QPushButton()
        self.repair_filenames = QPushButton()
        self.assign_references = QPushButton()
        artist_actions.addWidget(self.examine_unassigned)
        artist_actions.addWidget(self.repair_filenames)
        artist_actions.addWidget(self.assign_references)
        artist_actions.addStretch(1)
        artist_health.addLayout(artist_actions)
        root.addLayout(artist_health)
        self.analysis_title = QLabel()
        self.analysis_title.setStyleSheet("font-size:17px;font-weight:600")
        root.addWidget(self.analysis_title)
        self.state = QLabel()
        self.state.setWordWrap(True)
        root.addWidget(self.state)
        self.query_summary = QLabel()
        self.query_summary.setStyleSheet("font-weight:600")
        root.addWidget(self.query_summary)
        self.use_corpus = QPushButton()
        self.use_corpus.hide()
        root.addWidget(self.use_corpus)
        self.identification = QLabel()
        self.identification.setWordWrap(True)
        self.identification.hide()
        root.addWidget(self.identification)
        self.results_title = QLabel()
        self.results_title.setStyleSheet("font-size:17px;font-weight:600")
        root.addWidget(self.results_title)
        self.results = QTableWidget(0, 12)
        self.results.horizontalHeader().setMinimumSectionSize(40)
        self.results.horizontalHeader().setDefaultSectionSize(75)
        self.results.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results.setSortingEnabled(False)
        self.results.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.results, 1)
        self.gallery = QPushButton()
        self.compare = QPushButton()
        self.gallery.hide()
        self.compare.hide()
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.artist_search.textChanged.connect(self._filter_artists)
        self.artist_go.clicked.connect(self._emit_artist)
        self.artist_list.itemDoubleClicked.connect(lambda _item: self._emit_artist())
        self.choose_file.clicked.connect(self._choose)
        self.image_go.clicked.connect(self._emit_local)
        self.item_go.clicked.connect(lambda: self.item_search_requested.emit(self.item_id.value()))
        self.update_profiles.clicked.connect(self.update_requested)
        self.gallery.clicked.connect(lambda: self._emit_result(self.gallery_requested))
        self.compare.clicked.connect(lambda: self._emit_result(self.compare_requested))
        self.minimum_images.valueChanged.connect(self._refilter)
        self.limit.valueChanged.connect(self._refilter)
        self.add_references.clicked.connect(self._choose_many)
        self.remove_reference.clicked.connect(self._remove_selected_reference)
        self.clear_references.clicked.connect(self.references_cleared)
        self.continue_button.clicked.connect(self.continue_requested)
        self.remote_load.clicked.connect(
            lambda: (
                self.remote_requested.emit(
                    str(self.remote_site.currentData()), self.remote_id.text().strip()
                )
                if self.remote_id.text().strip()
                else None
            )
        )
        self.references.itemDoubleClicked.connect(
            lambda item: self.reference_activated.emit(int(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.use_corpus.clicked.connect(self.corpus_requested)
        self.results.itemDoubleClicked.connect(
            lambda _item: self._emit_result(self.gallery_requested)
        )
        controls.toggled.connect(lambda checked: self._set_advanced_visible(controls, checked))
        self.examine_unassigned.clicked.connect(self.unassigned_examine_requested)
        self.assign_references.clicked.connect(self.references_assign_requested)
        self.repair_filenames.clicked.connect(self.filename_repair_requested)
        mass = QVBoxLayout()
        self.library_title = QLabel()
        self.library_title.setStyleSheet("font-size:17px;font-weight:600")
        mass.addWidget(self.library_title)
        library_actions = QHBoxLayout()
        self.library_index = QPushButton()
        self.library_files = QPushButton()
        self.library_resume = QPushButton()
        self.library_resume.hide()
        self.library_pause = QPushButton()
        self.library_cancel = QPushButton()
        self.library_pause.setEnabled(False)
        self.library_cancel.setEnabled(False)
        library_actions.addWidget(self.library_index)
        library_actions.addWidget(self.library_files)
        library_actions.addWidget(self.library_resume)
        library_actions.addWidget(self.library_pause)
        library_actions.addWidget(self.library_cancel)
        library_actions.addStretch(1)
        mass.addLayout(library_actions)
        self.library_status = QLabel()
        self.library_status.setWordWrap(True)
        mass.addWidget(self.library_status)
        self.library_phase = QLabel()
        mass.addWidget(self.library_phase)
        self.library_current = QLabel()
        self.library_current.setWordWrap(True)
        mass.addWidget(self.library_current)
        self.library_progress = QProgressBar()
        self.library_progress.setRange(0, 100)
        self.library_progress.setValue(0)
        self.library_progress.setFormat("0 / 0 · 0.0 %")
        mass.addWidget(self.library_progress)
        discovery_actions = QHBoxLayout()
        self.discovery_source = QComboBox()
        self.discovery_source.addItem("Auto", "auto")
        self.discovery_source.addItem("Gelbooru", "gelbooru")
        self.discovery_source.addItem("e621", "e621")
        self.discovery_source.addItem("", "all")
        self.discovery_mode = QComboBox()
        self.discovery_mode.addItem("", "quick")
        self.discovery_mode.addItem("", "normal")
        self.discovery_mode.addItem("", "large")
        self.discover_remote = QPushButton()
        self.only_new = QCheckBox()
        self.discovery_source_label = QLabel()
        discovery_actions.addWidget(self.discovery_source_label)
        discovery_actions.addWidget(self.discovery_source)
        discovery_actions.addWidget(self.discovery_mode)
        discovery_actions.addWidget(self.discover_remote)
        discovery_actions.addWidget(self.only_new)
        discovery_actions.addStretch(1)
        mass.addLayout(discovery_actions)
        root.insertLayout(root.indexOf(self.analysis_title), mass)
        self.local_duplicates = QPushButton()
        mass.insertWidget(2, self.local_duplicates, 0, Qt.AlignmentFlag.AlignLeft)
        self.remote_title = QLabel()
        self.remote_title.setStyleSheet("font-size:17px;font-weight:600")
        mass.insertWidget(mass.count() - 1, self.remote_title)
        self.remote_status = QLabel()
        self.remote_status.setWordWrap(True)
        mass.addWidget(self.remote_status)
        self.remote_progress = QProgressBar()
        self.remote_progress.setRange(0, 100)
        self.remote_progress.setValue(0)
        mass.addWidget(self.remote_progress)
        self.remote_cancel = QPushButton()
        self.remote_cancel.setEnabled(False)
        mass.addWidget(self.remote_cancel, 0, Qt.AlignmentFlag.AlignLeft)
        self.library_index.clicked.connect(self._choose_library_roots)
        self.library_files.clicked.connect(self._choose_library_files)
        self.library_resume.clicked.connect(self.library_resume_requested)
        self.library_pause.clicked.connect(self.library_pause_requested)
        self.library_cancel.clicked.connect(self.library_cancel_requested)
        self.local_duplicates.clicked.connect(self.local_duplicates_requested)
        self.discover_remote.clicked.connect(
            lambda: self.remote_discovery_requested.emit(
                str(self.discovery_mode.currentData()), str(self.discovery_source.currentData())
            )
        )
        self.remote_cancel.clicked.connect(self.remote_cancel_requested)
        self.only_new.toggled.connect(self._refilter)
        self.purge_remote.clicked.connect(
            lambda: self.remote_purge_requested.emit(self.purge_days.value())
        )
        self._advanced_widgets = (
            self.mode,
            self.backend,
            self.minimum_images,
            self.limit,
            self.artist_search,
            self.artist_list,
            self.artist_go,
            self.item_label,
            self.item_id,
            self.item_go,
            self.corpus,
            self.update_profiles,
            self.purge_days,
            self.purge_remote,
        )
        self._mode_changed()
        self._set_advanced_visible(controls, False)
        self.retranslate()

    def retranslate(self) -> None:
        text = self.catalog.text
        self.title.setText(text("nav.similar_artists"))
        self.subtitle.setText(text("similar.subtitle"))
        self.drop_zone.setText(text("similar.drop_zone"))
        self.remote_id.setPlaceholderText(text("similar.post_id"))
        self.remote_load.setText(text("similar.load"))
        self.remote_label.setText(text("similar.remote_post"))
        self.references_group.setTitle(
            text(
                "similar.references_title",
                count=self._count("unique_image_count", self.references.count()),
            )
        )
        self.add_references.setText(text("similar.add_images"))
        self.remove_reference.setText(text("similar.remove_selection"))
        self.clear_references.setText(text("similar.clear"))
        self.continue_button.setText(text("similar.continue_anyway"))
        self.advanced_group.setTitle(text("similar.advanced_options"))
        self.mode.setItemText(0, text("similar.mode.artist"))
        self.mode.setItemText(1, text("similar.mode.image"))
        self.mode_label.setText(text("similar.search_by"))
        self.backend_label.setText(text("similar.primary_ranking"))
        self.minimum_images_label.setText(text("similar.minimum_images"))
        self.limit_label.setText(text("similar.top"))
        self.artist_search.setPlaceholderText(text("similar.artist_search_placeholder"))
        self.artist_go.setText(text("similar.search"))
        self.local_path.setPlaceholderText(text("similar.local_image_placeholder"))
        self.choose_file.setText(text("similar.choose"))
        self.image_go.setText(text("similar.analyze_image"))
        self.item_label.setText(text("similar.internal_item"))
        self.item_id.setToolTip(text("similar.internal_item_tooltip"))
        self.item_go.setText(text("similar.open"))
        self.purge_days_label.setText(text("similar.unused_days"))
        self.purge_remote.setText(text("similar.purge_remote"))
        self.update_profiles.setText(text("similar.update_profiles"))
        self.examine_unassigned.setText(text("similar.review"))
        self.repair_filenames.setText(text("similar.repair_filenames"))
        self.repair_filenames.setToolTip(text("similar.repair_filenames_tooltip"))
        self.assign_references.setText(text("similar.assign_artist"))
        self.analysis_title.setText(text("similar.analysis_title"))
        if not self.state.text():
            self.state.setText(text("similar.ready"))
        self.use_corpus.setText(text("similar.full_corpus"))
        self.results.setHorizontalHeaderLabels(
            tuple(
                text(f"similar.column.{key}")
                for key in (
                    "rank",
                    "artist",
                    "site",
                    "style",
                    "content",
                    "palette",
                    "images",
                    "profile",
                    "coherence",
                    "works",
                    "compare",
                    "booru",
                )
            )
        )
        self.results.horizontalHeaderItem(3).setToolTip(text("similar.tooltip.style"))
        self.results.horizontalHeaderItem(4).setToolTip(text("similar.tooltip.content"))
        self.results.horizontalHeaderItem(5).setToolTip(text("similar.tooltip.palette"))
        self.gallery.setText(text("similar.view_close_works"))
        self.compare.setText(text("similar.compare"))
        self.library_title.setText(text("similar.library_indexing"))
        self.library_index.setText(text("similar.index_folders"))
        self.library_files.setText(text("similar.index_files"))
        self.library_resume.setText(text("similar.resume"))
        self.library_pause.setText(text("similar.pause"))
        self.library_cancel.setText(text("similar.cancel"))
        if not self.library_status.text():
            self.library_status.setText(text("similar.no_indexing"))
        if not self.library_phase.text():
            self.library_phase.setText(text("similar.phase_none"))
        if not self.library_current.text():
            self.library_current.setText(text("similar.current_file_none"))
        self.local_duplicates.setText(text("similar.local_duplicates"))
        self.discovery_source.setItemText(3, text("similar.all"))
        self.discovery_source.setToolTip(text("similar.discovery_sources_tooltip"))
        for index, key in enumerate(("quick", "normal", "broad")):
            self.discovery_mode.setItemText(index, text(f"similar.discovery_mode.{key}"))
            self.discovery_mode.setItemData(
                index, text(f"similar.discovery_mode.{key}_tooltip"), Qt.ItemDataRole.ToolTipRole
            )
        self.discovery_mode.setToolTip(text("similar.discovery_budget_tooltip"))
        self.discover_remote.setText(text("similar.discover_remote"))
        self.only_new.setText(text("similar.only_new"))
        self.discovery_source_label.setText(text("similar.source"))
        self.remote_title.setText(text("similar.remote_title"))
        if not self.remote_status.text():
            self.remote_status.setText(text("similar.no_discovery"))
        self.remote_cancel.setText(text("similar.cancel_discovery"))
        self._filter_artists(self.artist_search.text())
        self._refilter()
        self.language_refreshed.emit()

    def set_artists(self, options: list[dict]) -> None:
        self.artist_options = list(options)
        self._filter_artists(self.artist_search.text())

    def _count(self, key: str, count: int) -> str:
        form = "one" if count == 1 else "many"
        return self.catalog.text(f"similar.{key}.{form}", count=count)

    def set_backend_available(self, backend: str, available: bool, reason: str = "") -> None:
        index = self.backend.findData(backend)
        if index >= 0:
            item = self.backend.model().item(index)
            item.setEnabled(available)
            item.setToolTip(reason)
            if not available and self.backend.currentIndex() == index:
                replacement = next(
                    (
                        value
                        for value in range(self.backend.count())
                        if self.backend.model().item(value).isEnabled()
                    ),
                    -1,
                )
                if replacement >= 0:
                    self.backend.setCurrentIndex(replacement)

    def _filter_artists(self, text: str) -> None:
        folded = text.casefold().strip()
        self.artist_list.clear()
        for option in self.artist_options:
            artist = option["artist"]
            if folded and folded not in artist.tag.casefold():
                continue
            suffix = (
                "" if option["profiled"] else " — " + self.catalog.text("similar.profile_unbuilt")
            )
            image_count = self._count("image_count", option["image_count"])
            item = QListWidgetItem(f"{artist.tag} — {artist.site.title()} — {image_count}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, artist)
            self.artist_list.addItem(item)
            if self.artist_list.count() >= 100:
                break

    def _selected_artist(self) -> ArtistIdentity | None:
        item = self.artist_list.currentItem() or (
            self.artist_list.item(0) if self.artist_list.count() else None
        )
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _emit_artist(self) -> None:
        artist = self._selected_artist()
        if artist:
            self.artist_search_requested.emit(artist)

    def _choose(self) -> None:
        value, _ = QFileDialog.getOpenFileName(
            self,
            self.catalog.text("similar.dialog.choose_image"),
            "",
            self.catalog.text("similar.dialog.image_filter"),
        )
        if value:
            self.local_path.setText(value)

    def _choose_many(self) -> None:
        values, _ = QFileDialog.getOpenFileNames(
            self,
            self.catalog.text("similar.dialog.choose_references"),
            "",
            self.catalog.text("similar.dialog.all_image_filter"),
        )
        if values:
            self.references_added.emit(values)

    def _choose_library_roots(self) -> None:
        dialog = QFileDialog(self, self.catalog.text("similar.index_folders"))
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        for view_type in (QListView, QTreeView):
            for view in dialog.findChildren(view_type):
                view.setSelectionMode(view.SelectionMode.ExtendedSelection)
        if dialog.exec():
            self.library_index_requested.emit(dialog.selectedFiles())

    def _choose_library_files(self) -> None:
        values, _ = QFileDialog.getOpenFileNames(
            self,
            self.catalog.text("similar.index_files"),
            "",
            self.catalog.text("similar.dialog.image_filter"),
        )
        if values:
            self.library_index_requested.emit(values)

    def _emit_local(self) -> None:
        if self.local_path.text().strip():
            self.local_image_requested.emit(self.local_path.text().strip())

    def _mode_changed(self) -> None:
        for widget in (
            self.artist_search,
            self.artist_list,
            self.artist_go,
            self.item_id,
            self.item_go,
        ):
            widget.setVisible(True)
        for widget in (self.local_path, self.choose_file, self.image_go):
            widget.hide()

    def _set_advanced_visible(self, _group, visible: bool) -> None:
        _group.setMaximumHeight(16777215 if visible else 30)
        for widget in getattr(self, "_advanced_widgets", ()):
            widget.setVisible(visible)

    def show_references(
        self,
        entries: list[dict],
        summary: str,
        quality: str,
        warning: str = "",
        active_item_id: int | None = None,
    ) -> None:
        self.references.clear()
        self.references_group.setTitle(
            self.catalog.text(
                "similar.references_title", count=self._count("unique_image_count", len(entries))
            )
        )
        for entry in entries:
            text = f"#{entry['item_id']}" + (
                "\n" + self.catalog.text("similar.active_query")
                if entry["item_id"] == active_item_id
                else ""
            )
            if entry.get("similarity") is not None:
                text += f"\n{self.catalog.text('similar.coherence')} {entry['similarity']:.3f}"
            item = QListWidgetItem(QIcon(str(entry["path"])), text)
            item.setData(Qt.ItemDataRole.UserRole, entry["item_id"])
            item.setToolTip(entry.get("provenance", str(entry["path"])))
            self.references.addItem(item)
            if entry["item_id"] == active_item_id:
                item.setSelected(True)
        self.state.setText(
            " · ".join(
                value
                for value in (
                    summary,
                    self.catalog.text("similar.quality", quality=quality),
                    warning,
                )
                if value
            )
        )
        self.continue_button.setVisible(len(entries) == 1)

    def _remove_selected_reference(self) -> None:
        item = self.references.currentItem()
        if item:
            self.reference_removed.emit(int(item.data(Qt.ItemDataRole.UserRole)))

    def show_results(
        self, query: str, rows: list[dict], identification: dict | None = None
    ) -> None:
        self.query_summary.setText(query)
        self.result_rows = list(rows)
        self.identification.setVisible(bool(identification))
        if identification:
            one = identification.get("top1")
            two = identification.get("top2")
            margin = identification.get("margin")
            self.identification.setText(
                self.catalog.text("similar.probable_artists")
                + " — "
                + "; ".join(
                    filter(
                        None,
                        (
                            f"1. {one.artist.tag} {one.centroid_similarity:.4f} ({self._count('image_count', one.image_count)})"
                            if one
                            else "",
                            f"2. {two.artist.tag} {two.centroid_similarity:.4f}" if two else "",
                            self.catalog.text("similar.margin", value=f"{margin:.4f}")
                            if margin is not None
                            else "",
                        ),
                    )
                )
            )
        self._refilter()

    def set_single_reference_mode(self, enabled: bool, count: int) -> None:
        self.use_corpus.setText(self.catalog.text("similar.use_references", count=count))
        self.use_corpus.setVisible(enabled and count > 1)

    def _refilter(self, *_args) -> None:
        rows = [
            row
            for row in self.result_rows
            if row["image_count"] >= self.minimum_images.value()
            and (not self.only_new.isChecked() or row.get("is_new", False))
        ][: self.limit.value()]
        self.results.setRowCount(len(rows))
        self._visible_rows = rows
        self.results_title.setText(
            self.catalog.text("similar.results_title", count=self._count("result_count", len(rows)))
        )
        for index, row in enumerate(rows):
            values = (
                self.catalog.text("similar.best_result") if index == 0 else f"#{index + 1}",
                row["artist"].tag,
                row["artist"].site,
                "—" if row.get("author_id") is None else f"{row['author_id']:.4f}",
                "—" if row.get("openclip") is None else f"{row['openclip']:.4f}",
                "—" if row.get("palette_distance") is None else f"{row['palette_distance']:.4f}",
                row["image_count"],
                confidence_label(self.catalog, row["confidence"]),
                "—" if row.get("coherence") is None else f"{row['coherence']:.4f}",
            )
            for column, value in enumerate(values):
                self.results.setItem(index, column, QTableWidgetItem(str(value)))
            if row.get("representative"):
                self.results.item(index, 1).setIcon(QIcon(str(row["representative"])))
            gallery = QPushButton(self.catalog.text("similar.close_works"))
            compare = QPushButton(self.catalog.text("similar.compare"))
            artist = row["artist"]
            gallery.clicked.connect(
                lambda _checked=False, value=artist: self.gallery_requested.emit(value)
            )
            compare.clicked.connect(
                lambda _checked=False, value=artist: self.compare_requested.emit(value)
            )
            self.results.setCellWidget(index, 9, gallery)
            self.results.setCellWidget(index, 10, compare)
            if artist.site in {"gelbooru", "e621"}:
                remote = QPushButton(self.catalog.text("similar.open"))
                remote.clicked.connect(
                    lambda _checked=False, value=artist: self.artist_open_requested.emit(value)
                )
                self.results.setCellWidget(index, 11, remote)
            if row.get("remote_discovery"):
                gallery.setText(self.catalog.text("similar.works_booru"))
                gallery.setToolTip(self.catalog.text("similar.works_booru_tooltip"))
        self.results.resizeColumnsToContents()
        if rows:
            self.results.selectRow(0)

    def _emit_result(self, signal) -> None:
        row = self.results.currentRow()
        if row >= 0 and row < len(getattr(self, "_visible_rows", [])):
            signal.emit(self._visible_rows[row]["artist"])

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.references_added.emit(paths)
            event.acceptProposedAction()


class ImageGalleryDialog(QDialog):
    def __init__(
        self,
        title: str,
        images: list[dict],
        parent=None,
        pixel_resolver=None,
        all_images: list[dict] | None = None,
        browser_launcher=None,
        catalog: LanguageCatalog | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1000, 700)
        self.browser_launcher = browser_launcher
        self.catalog = (
            catalog
            or getattr(parent, "catalog", None)
            or LanguageCatalog(Path(__file__).resolve().parents[4] / "resources" / "i18n", "en")
        )
        text = self.catalog.text
        root = QVBoxLayout(self)
        mode = QComboBox()
        mode.addItem(text("similar.gallery.closest"), "closest")
        mode.addItem(text("similar.gallery.all"), "all")
        root.addWidget(mode)
        split = QSplitter()
        thumbnails = QListWidget()
        thumbnails.setViewMode(QListWidget.ViewMode.IconMode)
        thumbnails.setResizeMode(QListWidget.ResizeMode.Adjust)
        thumbnails.setIconSize(QtCoreSize(120, 120))
        preview_host = QWidget()
        preview_layout = QVBoxLayout(preview_host)
        preview = ScaledImageLabel()
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        self.details.anchorClicked.connect(self._open_link)
        self.details.setMaximumHeight(150)
        preview_layout.addWidget(preview, 1)
        preview_layout.addWidget(self.details)
        split.addWidget(thumbnails)
        split.addWidget(preview_host)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)
        zoom = QComboBox()
        for label, value in (("Fit", 0), ("100 %", 100), ("200 %", 200), ("400 %", 400)):
            zoom.addItem(label, value)
        zoom.currentIndexChanged.connect(lambda: preview.set_zoom(int(zoom.currentData())))
        root.addWidget(zoom)
        collections = {
            "closest": list(images),
            "all": list(all_images if all_images is not None else images),
        }
        loaded = 0
        load_more = QPushButton(text("similar.gallery.load_more"))
        root.addWidget(load_more, 0, Qt.AlignmentFlag.AlignLeft)

        def populate(reset=False):
            nonlocal loaded
            if reset:
                thumbnails.clear()
                loaded = 0
            values = collections[str(mode.currentData())]
            end = min(len(values), loaded + 24)
            for image in values[loaded:end]:
                score = image.get("score")
                suffix = (
                    f" · {score:.4f}"
                    if score is not None and str(mode.currentData()) == "closest"
                    else ""
                )
                path = Path(str(image["path"]))
                item = QListWidgetItem(
                    QIcon(str(path)) if path.is_file() else QIcon(),
                    f"#{image['item_id']}{suffix}\n{text('similar.gallery.load_on_selection')}"
                    if not path.is_file()
                    else f"#{image['item_id']}{suffix}",
                )
                item.setData(Qt.ItemDataRole.UserRole, image)
                thumbnails.addItem(item)
            loaded = end
            load_more.setVisible(loaded < len(values))
            if thumbnails.count() and thumbnails.currentRow() < 0:
                thumbnails.setCurrentRow(0)

        mode.currentIndexChanged.connect(lambda: populate(True))
        load_more.clicked.connect(populate)

        def selected(item, _old=None):
            if not item:
                return
            value = item.data(Qt.ItemDataRole.UserRole)
            path = Path(value["path"])
            if not path.is_file() and pixel_resolver:
                try:
                    path = Path(pixel_resolver(int(value["item_id"])))
                    value["path"] = str(path)
                except Exception as exc:  # noqa: BLE001 - independent remote preview boundary
                    self.details.setPlainText(text("similar.gallery.remote_unavailable", error=exc))
                    return
            preview.set_image(path)
            item.setIcon(QIcon(str(path)))
            item.setText(item.text().replace("\n" + text("similar.gallery.load_on_selection"), ""))
            self._show_provenances(value)

        thumbnails.currentItemChanged.connect(selected)
        thumbnails.itemDoubleClicked.connect(
            lambda item: self._open_entry(
                item.data(Qt.ItemDataRole.UserRole), self.browser_launcher
            )
        )
        populate()

    def _show_provenances(self, entry: dict) -> None:
        lines = [f"<b>{self.catalog.text('similar.gallery.sources')}</b>"]
        for row in entry.get("provenances", []):
            if row.get("local_path"):
                path = str(row["local_path"])
                state = (
                    ""
                    if Path(path).is_file()
                    else " — " + self.catalog.text("similar.gallery.missing_file")
                )
                lines.append(f"{self.catalog.text('similar.gallery.local')} : {path}{state}")
            elif row.get("site") and row.get("post_id"):
                url = post_page_url(str(row["site"]), str(row["post_id"]))
                lines.append(f'<a href="{url}">{row["site"].title()} #{row["post_id"]}</a>')
        self.details.setHtml("<br>".join(lines))

    def _open_link(self, url: QUrl) -> None:
        if self.browser_launcher and "gelbooru.com" in url.host().casefold():
            self.browser_launcher.open(url.toString())
        else:
            QDesktopServices.openUrl(url)

    @staticmethod
    def _open_entry(entry: dict, browser_launcher=None) -> None:
        for row in entry.get("provenances", []):
            if row.get("local_path") and Path(str(row["local_path"])).is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(row["local_path"])))
                return
        for row in entry.get("provenances", []):
            if row.get("site") and row.get("post_id"):
                url = post_page_url(str(row["site"]), str(row["post_id"]))
                if str(row["site"]) == "gelbooru" and browser_launcher:
                    browser_launcher.open(url)
                else:
                    QDesktopServices.openUrl(QUrl(url))
                return


def QtCoreSize(width: int, height: int):
    from PySide6.QtCore import QSize

    return QSize(width, height)
