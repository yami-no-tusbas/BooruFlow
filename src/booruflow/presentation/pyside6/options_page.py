"""Options page with site-scoped credentials and local application paths."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class PathRow(QWidget):
    def __init__(self, *, directory: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.directory = directory
        self.edit = QLineEdit()
        self.button = QPushButton("Browse…")
        self.button.clicked.connect(self.browse)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)

    def browse(self) -> None:
        current = self.edit.text().strip()
        start = str(Path(current).parent if current else Path.home())
        if self.directory:
            selected = QFileDialog.getExistingDirectory(self, "Choose folder", current or start)
        else:
            selected, _filter = QFileDialog.getOpenFileName(
                self,
                "Choose database",
                start,
                "SQLite databases (*.db *.sqlite);;All files (*)",
            )
        if selected:
            self.edit.setText(selected)


class OptionsPage(QWidget):
    save_requested = Signal(dict, dict)

    def __init__(
        self,
        settings: dict[str, object] | None = None,
        credentials: dict[str, object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._credentials = {
            "gelbooru": self._site_credentials(credentials, "gelbooru"),
            "e621": self._site_credentials(credentials, "e621"),
        }
        self._current_site = "gelbooru"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)
        title = QLabel("Options")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(title)

        general = QGroupBox("General")
        general_form = QFormLayout(general)
        self.language = QComboBox()
        self.language.addItem("English", "en")
        self.language.addItem("Français", "fr")
        general_form.addRow("Language:", self.language)
        layout.addWidget(general)

        sites = QGroupBox("Site credentials")
        site_form = QFormLayout(sites)
        self.site = QComboBox()
        self.site.addItem("Gelbooru", "gelbooru")
        self.site.addItem("e621", "e621")
        self.user_id = QLineEdit()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_api_key = QCheckBox("Show API key")
        self.show_api_key.toggled.connect(
            lambda shown: self.api_key.setEchoMode(
                QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
            )
        )
        self.site.currentIndexChanged.connect(self._site_changed)
        site_form.addRow("Site:", self.site)
        site_form.addRow("User ID:", self.user_id)
        site_form.addRow("API key:", self.api_key)
        site_form.addRow("", self.show_api_key)
        layout.addWidget(sites)

        paths = QGroupBox("Local paths")
        path_form = QFormLayout(paths)
        self.gelbooru_database = PathRow()
        self.e621_database = PathRow()
        self.grabber_directory = PathRow(directory=True)
        path_form.addRow("Gelbooru database:", self.gelbooru_database)
        path_form.addRow("e621 database:", self.e621_database)
        path_form.addRow("Grabber folder:", self.grabber_directory)
        layout.addWidget(paths)

        note = QLabel(
            "Credentials are stored only in the local config folder and are excluded from Git."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.save_button = QPushButton("Save options")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        layout.addStretch(1)

        self._load_settings(settings or {})
        self._display_credentials("gelbooru")

    @staticmethod
    def _site_credentials(values: dict[str, object] | None, site: str) -> dict[str, str]:
        raw = (values or {}).get(site, {})
        if not isinstance(raw, dict):
            return {"user_id": "", "api_key": ""}
        return {
            "user_id": str(raw.get("user_id", "")),
            "api_key": str(raw.get("api_key", "")),
        }

    def _load_settings(self, settings: dict[str, object]) -> None:
        language = str(settings.get("language", "en"))
        index = self.language.findData(language)
        self.language.setCurrentIndex(index if index >= 0 else 0)
        self.gelbooru_database.edit.setText(str(settings.get("gelbooru_database", "")))
        self.e621_database.edit.setText(str(settings.get("e621_database", "")))
        self.grabber_directory.edit.setText(str(settings.get("grabber_directory", "")))

    def _capture_credentials(self, site: str) -> None:
        self._credentials[site] = {
            "user_id": self.user_id.text().strip(),
            "api_key": self.api_key.text().strip(),
        }

    def _display_credentials(self, site: str) -> None:
        values = self._credentials[site]
        self.user_id.setText(values["user_id"])
        self.api_key.setText(values["api_key"])

    def _site_changed(self) -> None:
        self._capture_credentials(self._current_site)
        self._current_site = str(self.site.currentData())
        self._display_credentials(self._current_site)

    def _save(self) -> None:
        self._capture_credentials(self._current_site)
        settings = {
            "language": str(self.language.currentData()),
            "gelbooru_database": self.gelbooru_database.edit.text().strip(),
            "e621_database": self.e621_database.edit.text().strip(),
            "grabber_directory": self.grabber_directory.edit.text().strip(),
        }
        self.save_requested.emit(settings, self._credentials)
