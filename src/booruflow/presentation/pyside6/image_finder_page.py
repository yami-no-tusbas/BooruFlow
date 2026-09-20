"""Image Finder page: browsing UI only, with no network logic."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from booruflow.domain.image_finder import RemoteArtwork, RemoteMediaType, SearchMode
from booruflow.presentation.pyside6.status_bar import PageStatus
from booruflow.presentation.pyside6.thumbnail_cache import ThumbnailCacheKey, ThumbnailMemoryCache
from booruflow.presentation.pyside6.thumbnail_loader import ThumbnailLoader


class ImageFinderPage(QWidget):
    search_requested = Signal(str, object)
    next_requested = Signal()
    connect_requested = Signal(str)
    disconnect_requested = Signal()
    download_requested = Signal(object)

    def __init__(self, catalog, connected: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.page_status = PageStatus("image_finder", self)
        self.artworks: list[RemoteArtwork] = []
        self._thumbnail_items = {}
        self.thumbnail_loader = ThumbnailLoader(ThumbnailMemoryCache(64 * 1024 * 1024), self)
        self.thumbnail_loader.image_ready.connect(self._thumbnail_ready)
        self.current_page = 0
        layout = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(self.title)
        auth = QHBoxLayout()
        self.auth_status = QLabel()
        self.refresh_token = QLineEdit()
        self.refresh_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.connect_button = QPushButton()
        self.disconnect_button = QPushButton()
        self.connect_button.clicked.connect(
            lambda: self.connect_requested.emit(self.refresh_token.text().strip())
        )
        self.disconnect_button.clicked.connect(self.disconnect_requested)
        auth.addWidget(self.auth_status)
        auth.addWidget(self.refresh_token, 1)
        auth.addWidget(self.connect_button)
        auth.addWidget(self.disconnect_button)
        layout.addLayout(auth)
        search = QHBoxLayout()
        self.source = QComboBox(); self.source.addItem("Pixiv", "pixiv")
        self.mode = QComboBox()
        for mode in SearchMode:
            self.mode.addItem(mode.value.replace("_", " ").title(), mode)
        self.query = QLineEdit()
        self.search_button = QPushButton()
        self.next_button = QPushButton()
        self.search_button.clicked.connect(self._search)
        self.query.returnPressed.connect(self._search)
        self.next_button.clicked.connect(self.next_requested)
        search.addWidget(self.source); search.addWidget(self.mode); search.addWidget(self.query, 1)
        search.addWidget(self.search_button); search.addWidget(self.next_button)
        layout.addLayout(search)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.results = QListWidget()
        self.results.currentRowChanged.connect(self._selection_changed)
        self.details = QTextBrowser(); self.details.setOpenExternalLinks(False)
        detail_panel = QWidget(); detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self.details, 1)
        actions = QHBoxLayout()
        self.previous_page = QPushButton("◀")
        self.next_page = QPushButton("▶")
        self.open_source = QPushButton()
        self.download = QPushButton()
        self.previous_page.clicked.connect(lambda: self._move_page(-1))
        self.next_page.clicked.connect(lambda: self._move_page(1))
        self.open_source.clicked.connect(self._open_source)
        self.download.clicked.connect(self._download)
        for widget in (self.previous_page, self.next_page, self.open_source, self.download):
            actions.addWidget(widget)
        detail_layout.addLayout(actions)
        splitter.addWidget(self.results); splitter.addWidget(detail_panel)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.set_connected(connected)
        self.retranslate()

    def _search(self) -> None:
        text = self.query.text().strip()
        if text:
            self.search_requested.emit(text, self.mode.currentData())

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        self.auth_status.setText(self.catalog.text(
            "image_finder.connected" if connected else "image_finder.not_connected"
        ))
        self.disconnect_button.setEnabled(connected)
        self.search_button.setEnabled(connected)

    def set_busy(self, busy: bool) -> None:
        self.search_button.setEnabled(not busy and self._connected)
        self.next_button.setEnabled(not busy and self.next_button.property("available") is True)

    def set_results(self, artworks: tuple[RemoteArtwork, ...], append: bool, has_next: bool) -> None:
        if not append:
            self.results.clear(); self.artworks.clear(); self._thumbnail_items.clear()
        for artwork in artworks:
            self.artworks.append(artwork)
            item = QListWidgetItem(
                f"{artwork.title or artwork.source_post_id}\n{artwork.artist.name} · {artwork.page_count} page(s)"
            )
            item.setData(Qt.ItemDataRole.UserRole, len(self.artworks) - 1)
            self.results.addItem(item)
            preview = artwork.images[0].preview_url
            if preview:
                key = ThumbnailCacheKey("pixiv", int(artwork.source_post_id), preview)
                self._thumbnail_items[key] = item
        self.next_button.setProperty("available", has_next)
        self.next_button.setEnabled(has_next)
        if self.results.count() and self.results.currentRow() < 0:
            self.results.setCurrentRow(0)
        self.thumbnail_loader.begin_wave(list(self._thumbnail_items))

    def _thumbnail_ready(self, key, image) -> None:
        item = self._thumbnail_items.get(key)
        if item is not None:
            item.setIcon(QIcon(QPixmap.fromImage(image)))

    def selected(self):
        item = self.results.currentItem()
        return self.artworks[item.data(Qt.ItemDataRole.UserRole)] if item else None

    def selected_image(self):
        artwork = self.selected()
        if not artwork:
            return None
        return artwork.images[min(self.current_page, len(artwork.images) - 1)]

    def _selection_changed(self, _row: int) -> None:
        self.current_page = 0
        self._show_details()

    def _move_page(self, delta: int) -> None:
        artwork = self.selected()
        if artwork:
            self.current_page = max(0, min(artwork.page_count - 1, self.current_page + delta))
            self._show_details()

    def _show_details(self) -> None:
        artwork = self.selected(); image = self.selected_image()
        if not artwork or not image:
            self.details.clear(); return
        media = "Ugoira" if image.media_type is RemoteMediaType.UGOIRA else "Image"
        self.details.setHtml(
            f"<h3>{artwork.title}</h3><p><b>ID:</b> {artwork.source_post_id}<br>"
            f"<b>Artist:</b> {artwork.artist.name} ({artwork.artist.artist_id})<br>"
            f"<b>Page:</b> {image.page_index + 1}/{artwork.page_count}<br>"
            f"<b>Dimensions:</b> {image.width or '?'} × {image.height or '?'}<br>"
            f"<b>Rating:</b> {artwork.rating}<br><b>Type:</b> {media}</p>"
            f"<p>{artwork.description}</p><p>{' · '.join(artwork.tags)}</p>"
        )
        supported = image.media_type is not RemoteMediaType.UGOIRA and bool(image.original_url)
        self.download.setEnabled(supported)
        self.previous_page.setEnabled(self.current_page > 0)
        self.next_page.setEnabled(self.current_page + 1 < artwork.page_count)

    def _open_source(self) -> None:
        artwork = self.selected()
        if artwork:
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(artwork.source_url))

    def _download(self) -> None:
        image = self.selected_image()
        if image:
            self.download_requested.emit(image)

    def retranslate(self) -> None:
        text = self.catalog.text
        self.title.setText(text("nav.image_finder"))
        self.refresh_token.setPlaceholderText(text("image_finder.refresh_token"))
        self.connect_button.setText(text("image_finder.connect"))
        self.disconnect_button.setText(text("image_finder.disconnect"))
        self.query.setPlaceholderText(text("image_finder.search_placeholder"))
        self.search_button.setText(text("image_finder.search"))
        self.next_button.setText(text("image_finder.next"))
        self.open_source.setText(text("image_finder.open_source"))
        self.download.setText(text("image_finder.download_original"))
        self.set_connected(self._connected)
