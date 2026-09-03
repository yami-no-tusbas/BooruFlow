"""Drag-and-drop cleanup audit page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from booruflow.application.model_inventory import format_size, inventory_models, model_totals
from booruflow.infrastructure.localization import LanguageCatalog


class FolderDropList(QListWidget):
    paths_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        self.add_paths(Path(url.toLocalFile()) for url in event.mimeData().urls())
        event.acceptProposedAction()

    def add_paths(self, paths) -> None:
        existing = {self.item(index).text() for index in range(self.count())}
        for path in paths:
            value = str(path)
            if path.is_dir() and value not in existing:
                self.addItem(value)
                existing.add(value)
        self.paths_changed.emit()

    def values(self) -> tuple[Path, ...]:
        return tuple(Path(self.item(index).text()) for index in range(self.count()))


class CleanupPage(QWidget):
    scan_requested = Signal(tuple)
    stop_requested = Signal()
    recycle_requested = Signal()
    blacklist_changed = Signal(str)

    def __init__(
        self, catalog: LanguageCatalog, settings: dict[str, object] | None = None,
        project_root: Path | None = None,
    ) -> None:
        super().__init__()
        self.catalog = catalog
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 24)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(self.title)
        self.project_root = project_root
        self.disk_group = QGroupBox()
        disk_layout = QVBoxLayout(self.disk_group)
        self.disk_usage = QLabel()
        self.disk_usage.setTextInteractionFlags(self.disk_usage.textInteractionFlags())
        disk_layout.addWidget(self.disk_usage)
        disk_actions = QHBoxLayout()
        self.refresh_disk_button = QPushButton()
        self.open_models_button = QPushButton()
        self.open_models_button.setEnabled(project_root is not None)
        disk_actions.addWidget(self.refresh_disk_button)
        disk_actions.addWidget(self.open_models_button)
        disk_actions.addStretch(1)
        disk_layout.addLayout(disk_actions)
        layout.addWidget(self.disk_group)
        self.group = QGroupBox()
        box = QVBoxLayout(self.group)
        self.help = QLabel()
        self.help.setWordWrap(True)
        box.addWidget(self.help)
        self.folders = FolderDropList()
        self.folders.setMinimumHeight(130)
        box.addWidget(self.folders)
        buttons = QHBoxLayout()
        self.add_button = QPushButton()
        self.remove_button = QPushButton()
        self.clear_button = QPushButton()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        box.addLayout(buttons)
        layout.addWidget(self.group)
        self.blacklist_group = QGroupBox()
        blacklist_layout = QHBoxLayout(self.blacklist_group)
        self.blacklist_label = QLabel()
        self.blacklist_file = QLineEdit(str((settings or {}).get("blacklist_file", "")))
        self.blacklist_browse = QPushButton()
        blacklist_layout.addWidget(self.blacklist_label)
        blacklist_layout.addWidget(self.blacklist_file, 1)
        blacklist_layout.addWidget(self.blacklist_browse)
        layout.addWidget(self.blacklist_group)
        actions = QHBoxLayout()
        self.scan_button = QPushButton()
        self.stop_button = QPushButton()
        self.recycle_button = QPushButton()
        self.stop_button.setEnabled(False)
        self.recycle_button.setEnabled(False)
        actions.addWidget(self.scan_button)
        actions.addWidget(self.stop_button)
        actions.addStretch(1)
        actions.addWidget(self.recycle_button)
        layout.addLayout(actions)
        self.state = QLabel()
        self.state.setContentsMargins(2, 6, 2, 6)
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        self.results = QListWidget()
        layout.addWidget(self.results, 1)
        self.add_button.clicked.connect(self._add)
        self.remove_button.clicked.connect(self._remove)
        self.clear_button.clicked.connect(self.folders.clear)
        self.scan_button.clicked.connect(self._scan)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.recycle_button.clicked.connect(self.recycle_requested.emit)
        self.blacklist_browse.clicked.connect(self._browse_blacklist)
        self.blacklist_file.editingFinished.connect(
            lambda: self.blacklist_changed.emit(self.blacklist_file.text().strip())
        )
        self.refresh_disk_button.clicked.connect(self.refresh_disk_usage)
        self.open_models_button.clicked.connect(self._open_models)
        self.retranslate()
        self.refresh_disk_usage()

    def refresh_disk_usage(self) -> None:
        if self.project_root is None:
            self.disk_usage.setText(self.catalog.text("cleanup.disk_unavailable"))
            return
        totals = model_totals(inventory_models(self.project_root))
        self.disk_usage.setText(self.catalog.text(
            "cleanup.disk_models", total=format_size(totals["total"]),
            wd14=format_size(totals["wd14"]), embeddings=format_size(totals["embeddings"]),
            e621=format_size(totals["e621"]), other=format_size(totals["other"]),
        ))

    def _open_models(self) -> None:
        if self.project_root is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project_root / "var" / "models")))

    def _browse_blacklist(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self, self.catalog.text("cleanup.choose_blacklist"),
            self.blacklist_file.text().strip(), self.catalog.text("options.text_filter"),
        )
        if selected:
            self.blacklist_file.setText(selected)
            self.blacklist_changed.emit(selected)

    def _add(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.catalog.text("cleanup.choose_folder"))
        if path:
            self.folders.add_paths((Path(path),))

    def _remove(self) -> None:
        for item in self.folders.selectedItems():
            self.folders.takeItem(self.folders.row(item))

    def _scan(self) -> None:
        roots = self.folders.values()
        if not roots:
            self.state.setText(self.catalog.text("cleanup.no_folder"))
            return
        missing = [str(path) for path in roots if not path.is_dir()]
        if missing:
            self.state.setText(self.catalog.text("cleanup.missing", path=missing[0]))
            return
        self.scan_requested.emit(roots)

    def set_running(self, running: bool) -> None:
        self.scan_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        if running:
            self.recycle_button.setEnabled(False)
            self.results.clear()
            self.state.setText(self.catalog.text("cleanup.running"))

    def set_progress(self, files: int, matches: int) -> None:
        self.state.setText(self.catalog.text("cleanup.progress", files=files, matches=matches))

    def show_matches(self, matches: list) -> None:
        self.results.clear()
        seen: set[Path] = set()
        for match in matches:
            if match.path not in seen:
                self.results.addItem(str(match.path))
                seen.add(match.path)
        self.recycle_button.setEnabled(bool(seen))

    def retranslate(self) -> None:
        text = self.catalog.text
        self.title.setText(text("nav.cleanup"))
        self.disk_group.setTitle(text("cleanup.disk_group"))
        self.refresh_disk_button.setText(text("cleanup.disk_refresh"))
        self.open_models_button.setText(text("cleanup.disk_open_models"))
        self.group.setTitle(text("cleanup.group"))
        self.help.setText(text("cleanup.help"))
        self.blacklist_group.setTitle(text("cleanup.blacklist_group"))
        self.blacklist_label.setText(text("cleanup.blacklist_file"))
        self.blacklist_browse.setText(text("options.browse"))
        self.add_button.setText(text("cleanup.add"))
        self.remove_button.setText(text("cleanup.remove"))
        self.clear_button.setText(text("cleanup.clear"))
        self.scan_button.setText(text("cleanup.scan"))
        self.stop_button.setText(text("cleanup.stop"))
        self.recycle_button.setText(text("cleanup.recycle"))
        if self.scan_button.isEnabled():
            self.state.setText(text("cleanup.ready"))
