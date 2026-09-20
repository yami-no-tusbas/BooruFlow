"""Read-only folder artist counter and Everything shortcut."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from booruflow.infrastructure.folder_artists import (
    FolderArtistScan,
    ImageCountFilter,
    parse_image_count_filter,
)
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.ui_components import DataTable


class FolderArtistsPage(QWidget):
    analyze_requested = Signal(str)
    stop_requested = Signal()
    open_requested = Signal(str)
    folder_changed = Signal(str)

    def __init__(self, catalog: LanguageCatalog, initial_folder: str = "") -> None:
        super().__init__()
        self.catalog = catalog
        self._running = False
        self._active_count_filter: ImageCountFilter | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 22)

        self.title = QLabel()
        self.title.setStyleSheet("font-size:22px;font-weight:600;")
        layout.addWidget(self.title)
        self.explanation = QLabel()
        self.explanation.setWordWrap(True)
        layout.addWidget(self.explanation)

        folder_row = QHBoxLayout()
        self.folder_label = QLabel()
        self.folder = QLineEdit(initial_folder)
        self.choose_button = QPushButton()
        self.analyze_button = QPushButton()
        self.choose_button.clicked.connect(self.choose_folder)
        self.analyze_button.clicked.connect(self._analyze_or_stop)
        self.folder.textChanged.connect(self._folder_edited)
        self.folder.returnPressed.connect(self._analyze_or_stop)
        folder_row.addWidget(self.folder_label)
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(self.choose_button)
        folder_row.addWidget(self.analyze_button)
        layout.addLayout(folder_row)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        filter_row = QHBoxLayout()
        filter_row.addStretch(1)
        self.count_filter_label = QLabel()
        self.count_filter = QLineEdit()
        self.count_filter.setMaximumWidth(190)
        self.count_filter.textChanged.connect(self._count_filter_changed)
        self.count_filter_error = QLabel()
        self.count_filter_error.setStyleSheet("color:#B91C1C;")
        filter_row.addWidget(self.count_filter_label)
        filter_row.addWidget(self.count_filter)
        filter_row.addWidget(self.count_filter_error)
        layout.addLayout(filter_row)
        self.table = DataTable(0, 2)
        self.table.setSelectionMode(self.table.SelectionMode.SingleSelection)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(
            0, self.table.horizontalHeader().ResizeMode.Stretch
        )
        self.table.doubleClicked.connect(lambda _index: self.open_selected())
        self.table.itemSelectionChanged.connect(self._update_open_enabled)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.state = QLabel()
        bottom.addWidget(self.state, 1)
        self.open_button = QPushButton()
        self.open_button.clicked.connect(self.open_selected)
        bottom.addWidget(self.open_button)
        layout.addLayout(bottom)
        self.retranslate()
        self._update_open_enabled()

    def choose_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            self.catalog.text("folder_artists.choose_title"),
            self.folder.text().strip() or str(Path.home()),
        )
        if selected:
            self.folder.setText(selected)

    def choose_everything(self) -> str:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            self.catalog.text("folder_artists.choose_everything"),
            str(Path.home()),
            self.catalog.text("folder_artists.everything_filter"),
        )
        return selected

    def _folder_edited(self, value: str) -> None:
        self.clear_results()
        self.folder_changed.emit(value)

    def _analyze_or_stop(self) -> None:
        if self._running:
            self.stop_requested.emit()
        else:
            self.analyze_requested.emit(self.folder.text().strip())

    def set_running(self, running: bool) -> None:
        self._running = running
        self.choose_button.setEnabled(not running)
        self.folder.setEnabled(not running)
        self.analyze_button.setText(
            self.catalog.text("folder_artists.stop" if running else "folder_artists.analyze")
        )
        self._update_open_enabled()

    def set_progress(self, images: int) -> None:
        self.state.setText(self.catalog.text("folder_artists.scanning", images=images))

    def clear_results(self) -> None:
        self.table.setRowCount(0)
        self.summary.clear()
        self._update_open_enabled()

    def show_scan(self, scan: FolderArtistScan) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(scan.artists))
        for row, artist in enumerate(scan.artists):
            name_item = QTableWidgetItem(artist.name)
            count_item = QTableWidgetItem()
            count_item.setData(Qt.ItemDataRole.DisplayRole, artist.images)
            count_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, count_item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(1, Qt.SortOrder.DescendingOrder)
        self.summary.setText(
            self.catalog.text(
                "folder_artists.summary",
                found=scan.images_found,
                with_artist=scan.images_with_artist,
                anonymous=scan.anonymous_ignored,
                unrecognized=scan.unrecognized_names,
                artists=len(scan.artists),
                inaccessible=scan.inaccessible_entries,
            )
        )
        self._apply_count_filter(self._active_count_filter)
        self._update_open_enabled()

    def _count_filter_changed(self, expression: str) -> None:
        try:
            parsed = parse_image_count_filter(expression)
        except ValueError:
            self.count_filter.setStyleSheet("border:1px solid #B91C1C;")
            self.count_filter_error.setText(self.catalog.text("folder_artists.filter_invalid"))
            return
        self.count_filter.setStyleSheet("")
        self.count_filter_error.clear()
        self._active_count_filter = parsed
        self._apply_count_filter(parsed)

    def _apply_count_filter(self, count_filter: ImageCountFilter | None) -> None:
        selected_row = next(
            (index.row() for index in self.table.selectionModel().selectedRows()), None
        )
        hide_selection = False
        for row in range(self.table.rowCount()):
            count = int(self.table.item(row, 1).data(Qt.ItemDataRole.DisplayRole))
            hidden = count_filter is not None and not count_filter.matches(count)
            self.table.setRowHidden(row, hidden)
            hide_selection = hide_selection or (hidden and row == selected_row)
        if hide_selection:
            self.table.clearSelection()
        self._update_open_enabled()

    def selected_artist(self) -> str:
        rows = self.table.selectionModel().selectedRows()
        return self.table.item(rows[0].row(), 0).text() if rows else ""

    def open_selected(self) -> None:
        artist = self.selected_artist()
        if artist and not self._running:
            self.open_requested.emit(artist)

    def _update_open_enabled(self) -> None:
        self.open_button.setEnabled(bool(self.selected_artist()) and not self._running)

    def retranslate(self) -> None:
        text = self.catalog.text
        self.title.setText(text("nav.folder_artists"))
        self.explanation.setText(text("folder_artists.explanation"))
        self.folder_label.setText(text("folder_artists.folder"))
        self.choose_button.setText(text("folder_artists.choose"))
        self.analyze_button.setText(
            text("folder_artists.stop" if self._running else "folder_artists.analyze")
        )
        self.table.setHorizontalHeaderLabels(
            (text("folder_artists.column_artist"), text("folder_artists.column_images"))
        )
        self.count_filter_label.setText(text("folder_artists.filter_images"))
        self.count_filter.setPlaceholderText(text("folder_artists.filter_placeholder"))
        if self.count_filter_error.text():
            self.count_filter_error.setText(text("folder_artists.filter_invalid"))
        self.table.set_empty_text(text("folder_artists.empty"))
        self.open_button.setText(text("folder_artists.open"))
        if not self.state.text():
            self.state.setText(text("folder_artists.ready"))
