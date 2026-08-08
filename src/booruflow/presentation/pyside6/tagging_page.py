"""Manual browser-assisted tagging review page."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QProgressBar, QPushButton, QScrollArea, QSpinBox, QToolButton,
    QVBoxLayout, QWidget,
)

from booruflow.application.tagging import TaggingRequest
from booruflow.infrastructure.localization import LanguageCatalog


class CollapsibleResultGroup(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(4)
        self.toggle = QToolButton(); self.toggle.setText(title); self.toggle.setCheckable(True); self.toggle.setChecked(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.content = QWidget(); self.grid = QGridLayout(self.content); self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.toggle.toggled.connect(self._toggle)
        layout.addWidget(self.toggle); layout.addWidget(self.content)

    def _toggle(self, expanded: bool) -> None:
        self.content.setVisible(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)


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
        self.results_scroll = QScrollArea(); self.results_scroll.setWidgetResizable(True); self.results_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.results = QWidget(); self.results_layout = QVBoxLayout(self.results); self.results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.results_scroll.setWidget(self.results)
        self.result_generation = 0
        self.network = QNetworkAccessManager(self)
        layout.addWidget(self.results_scroll, 1)
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
            self._clear_results()
            self.state.setText(self.catalog.text("tagging.running"))

    def set_progress(self, page: int, current: int, total: int, examined: int, retained: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(current)
        self.progress.setFormat(f"{current}/{total}")
        self.state.setText(self.catalog.text(
            "tagging.progress", page=page, examined=examined, retained=retained
        ))

    def show_results(self, posts: list[dict]) -> None:
        self._clear_results(); generation = self.result_generation
        grouped = {key: [post for post in posts if post.get("priority") == key] for key in ("critical", "high", "low")}
        for key, values in grouped.items():
            section = CollapsibleResultGroup(
                self.catalog.text("tagging.section", priority=self.catalog.text(f"tagging.priority.{key}"), count=len(values))
            )
            self.results_layout.addWidget(section)
            for index, post in enumerate(sorted(values, key=lambda value: int(value.get("tag_count", 0)))):
                post_id = int(post.get("id", 0)); count = int(post.get("tag_count", 0))
                card = QToolButton(); card.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
                card.setIconSize(QSize(150, 135)); card.setFixedSize(175, 180)
                card.setText(self.catalog.text("tagging.card", id=post_id, count=count))
                card.clicked.connect(lambda _checked=False, value=post_id: self._open_post(value))
                section.grid.addWidget(card, index // 4, index % 4)
                preview = str(post.get("preview_url") or "")
                if preview:
                    request = QNetworkRequest(QUrl(preview)); request.setRawHeader(b"User-Agent", b"BooruFlow/0.1"); request.setRawHeader(b"Referer", b"https://gelbooru.com/")
                    reply = self.network.get(request)
                    reply.finished.connect(lambda current=reply, target=card, value=generation: self._thumbnail_ready(current, target, value))

    def _clear_results(self) -> None:
        self.result_generation += 1
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def _thumbnail_ready(self, reply: QNetworkReply, button: QToolButton, generation: int) -> None:
        try:
            if generation == self.result_generation and reply.error() == QNetworkReply.NetworkError.NoError:
                pixmap = QPixmap()
                if pixmap.loadFromData(bytes(reply.readAll())):
                    button.setIcon(QIcon(pixmap))
        except RuntimeError:
            pass
        finally:
            reply.deleteLater()

    def _open_post(self, post_id: int) -> None:
        QDesktopServices.openUrl(QUrl(
            f"https://gelbooru.com/index.php?page=post&s=view&id={post_id}"
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
