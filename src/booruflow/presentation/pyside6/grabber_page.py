"""Optional Grabber session controls."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from booruflow.application.grabber_batches import BatchRequest, read_tag_entries
from booruflow.infrastructure.localization import LanguageCatalog


class GrabberPage(QWidget):
    create_requested = Signal(object)
    load_requested = Signal()
    launch_requested = Signal()
    previous_requested = Signal()

    def __init__(self, catalog: LanguageCatalog, settings: dict[str, object], available: bool) -> None:
        super().__init__()
        self.catalog = catalog
        self.available = available
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 24)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(self.title)
        self.group = QGroupBox()
        box = QVBoxLayout(self.group)
        self.tags = QPlainTextEdit()
        self.tags.setPlaceholderText("artist_one\nartist_two\ne621\twolf")
        box.addWidget(self.tags)
        import_row = QHBoxLayout()
        self.import_button = QPushButton()
        import_row.addWidget(self.import_button)
        import_row.addStretch(1)
        box.addLayout(import_row)
        form = QFormLayout()
        self.site_label = QLabel()
        self.site = QComboBox()
        self.site.addItem("Gelbooru", "gelbooru")
        self.site.addItem("e621", "e621")
        self.prefix = QLineEdit(str(settings.get("tab_prefix", "-rating:general")))
        self.suffix = QLineEdit(str(settings.get("tab_suffix", "")))
        self.tabs_per_batch = QSpinBox(); self.tabs_per_batch.setRange(1, 500); self.tabs_per_batch.setValue(int(settings.get("tabs_per_batch", 15)))
        self.images_per_tab = QSpinBox(); self.images_per_tab.setRange(1, 1000); self.images_per_tab.setValue(int(settings.get("images_per_tab", 100)))
        self.labels = {key: QLabel() for key in ("prefix", "suffix", "tabs", "images")}
        form.addRow(self.site_label, self.site)
        form.addRow(self.labels["prefix"], self.prefix)
        form.addRow(self.labels["suffix"], self.suffix)
        form.addRow(self.labels["tabs"], self.tabs_per_batch)
        form.addRow(self.labels["images"], self.images_per_tab)
        box.addLayout(form)
        layout.addWidget(self.group)
        actions = QHBoxLayout()
        self.create_button = QPushButton()
        self.load_button = QPushButton()
        self.previous_button = QPushButton()
        self.launch_button = QPushButton()
        for button in (self.create_button, self.load_button, self.previous_button): actions.addWidget(button)
        actions.addStretch(1); actions.addWidget(self.launch_button)
        layout.addLayout(actions)
        self.state = QLabel(); self.state.setWordWrap(True); self.state.setContentsMargins(2, 6, 2, 6)
        layout.addWidget(self.state)
        layout.addStretch(1)
        self.import_button.clicked.connect(self._import)
        self.create_button.clicked.connect(self._create)
        self.load_button.clicked.connect(self.load_requested.emit)
        self.launch_button.clicked.connect(self.launch_requested.emit)
        self.previous_button.clicked.connect(self.previous_requested.emit)
        for button in (self.create_button, self.load_button, self.previous_button, self.launch_button, self.import_button):
            button.setEnabled(available)
        self.retranslate()

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.catalog.text("grabber.choose_list"), "", "Text (*.txt *.tsv);;All files (*)")
        if path:
            self.tags.setPlainText(Path(path).read_text(encoding="utf-8-sig", errors="replace"))

    def _create(self) -> None:
        try:
            request = BatchRequest(
                read_tag_entries(self.tags.toPlainText(), str(self.site.currentData())),
                self.tabs_per_batch.value(), self.images_per_tab.value(),
                self.prefix.text(), self.suffix.text(),
            )
        except ValueError as exc:
            self.state.setText(self.catalog.text("grabber.invalid", error=exc))
            return
        self.create_requested.emit(request)

    def show_session(self, state: dict | None) -> None:
        if not state:
            self.state.setText(self.catalog.text("grabber.no_session")); return
        total = len(state.get("files", [])); current = int(state.get("current", 0))
        if current >= total:
            self.state.setText(self.catalog.text("grabber.complete", total=total, tags=state.get("total_tags", 0)))
        else:
            self.state.setText(self.catalog.text("grabber.progress", current=current + 1, total=total, tags=state.get("total_tags", 0)))
        self.previous_button.setEnabled(self.available and current > 0)
        self.launch_button.setEnabled(self.available and current < total)

    def retranslate(self) -> None:
        text = self.catalog.text
        self.title.setText(text("nav.grabber")); self.group.setTitle(text("grabber.group"))
        self.site_label.setText(text("options.site"))
        for key, label in self.labels.items(): label.setText(text(f"grabber.{key}"))
        self.import_button.setText(text("grabber.import")); self.create_button.setText(text("grabber.create"))
        self.load_button.setText(text("grabber.resume")); self.previous_button.setText(text("grabber.previous")); self.launch_button.setText(text("grabber.launch"))
        if not self.available: self.state.setText(text("page.grabber"))
        elif not self.state.text(): self.state.setText(text("grabber.no_session"))
