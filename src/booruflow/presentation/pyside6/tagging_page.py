"""Manual browser-assisted tagging review page."""

from __future__ import annotations

from PySide6.QtCore import QSize, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QProgressBar, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from booruflow.application.tagging import TaggingRequest
from booruflow.infrastructure.localization import LanguageCatalog


class TaggingPage(QWidget):
    start_requested = Signal(object)
    stop_requested = Signal()

    def __init__(self, catalog: LanguageCatalog, settings: dict[str, object]) -> None:
        super().__init__()
        self.catalog = catalog
        self.settings = settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 24)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(self.title)
        self.group = QGroupBox()
        controls = QVBoxLayout(self.group)
        form = QFormLayout()
        self.query_label = QLabel()
        self.query = QLineEdit(str(settings.get("tagging_query", "rating:general")))
        form.addRow(self.query_label, self.query)
        controls.addLayout(form)
        grid = QGridLayout()
        self.spins: dict[str, QSpinBox] = {}
        defaults = {"pages": 10, "start": 1, "minimum": 0, "maximum": 12, "critical": 5, "high": 8}
        self.spin_labels: dict[str, QLabel] = {}
        for index, (key, default) in enumerate(defaults.items()):
            label = QLabel()
            spin = QSpinBox()
            spin.setRange(1 if key in {"pages", "start"} else 0, 1_000_000 if key == "start" else 1_000)
            spin.setValue(int(settings.get(f"tagging_{key}", default)))
            self.spins[key] = spin
            self.spin_labels[key] = label
            grid.addWidget(label, index // 3, (index % 3) * 2)
            grid.addWidget(spin, index // 3, (index % 3) * 2 + 1)
        controls.addLayout(grid)
        layout.addWidget(self.group)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.start_button = QPushButton()
        self.stop_button = QPushButton()
        self.stop_button.setEnabled(False)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        layout.addLayout(actions)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.state = QLabel()
        self.state.setContentsMargins(2, 4, 2, 4)
        layout.addWidget(self.state)
        self.results = QListWidget()
        self.results.setAlternatingRowColors(True)
        self.results.setIconSize(QSize(120, 120))
        self.results.itemActivated.connect(self._open_item)
        self.network = QNetworkAccessManager(self)
        layout.addWidget(self.results, 1)
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.retranslate()

    def _start(self) -> None:
        try:
            request = TaggingRequest(
                self.query.text().strip(), self.spins["pages"].value(),
                self.spins["start"].value(), self.spins["minimum"].value(),
                self.spins["maximum"].value(), self.spins["critical"].value(),
                self.spins["high"].value(),
            )
        except ValueError as exc:
            self.state.setText(self.catalog.text("tagging.invalid", error=exc))
            return
        self.start_requested.emit(request)

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        if running:
            self.results.clear()
            self.state.setText(self.catalog.text("tagging.running"))

    def set_progress(self, page: int, current: int, total: int, examined: int, retained: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(current)
        self.progress.setFormat(f"{current}/{total}")
        self.state.setText(self.catalog.text(
            "tagging.progress", page=page, examined=examined, retained=retained
        ))

    def show_results(self, posts: list[dict]) -> None:
        self.results.clear()
        order = {"critical": 0, "high": 1, "low": 2}
        for post in sorted(posts, key=lambda value: (order.get(str(value.get("priority")), 9), int(value.get("tag_count", 0)))):
            post_id = int(post.get("id", 0))
            priority = self.catalog.text(f"tagging.priority.{post.get('priority', 'low')}")
            item = QListWidgetItem(
                self.catalog.text("tagging.result", priority=priority, id=post_id, count=int(post.get("tag_count", 0)))
            )
            item.setData(256, post_id)
            self.results.addItem(item)
            preview = str(post.get("preview_url") or "")
            if preview:
                request = QNetworkRequest(QUrl(preview))
                request.setRawHeader(b"User-Agent", b"BooruFlow/0.1")
                request.setRawHeader(b"Referer", b"https://gelbooru.com/")
                reply = self.network.get(request)
                reply.finished.connect(lambda current=reply, target=item: self._thumbnail_ready(current, target))

    def _thumbnail_ready(self, reply: QNetworkReply, item: QListWidgetItem) -> None:
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                pixmap = QPixmap()
                if pixmap.loadFromData(bytes(reply.readAll())):
                    item.setIcon(QIcon(pixmap))
        finally:
            reply.deleteLater()

    def _open_item(self, item: QListWidgetItem) -> None:
        QDesktopServices.openUrl(QUrl(
            f"https://gelbooru.com/index.php?page=post&s=view&id={int(item.data(256))}"
        ))

    def retranslate(self) -> None:
        text = self.catalog.text
        self.title.setText(text("nav.tagging"))
        self.group.setTitle(text("tagging.group"))
        self.query_label.setText(text("tagging.query"))
        for key, label in self.spin_labels.items():
            label.setText(text(f"tagging.{key}"))
        self.start_button.setText(text("tagging.start_button"))
        self.stop_button.setText(text("tagging.stop"))
        if self.start_button.isEnabled():
            self.state.setText(text("tagging.ready"))
