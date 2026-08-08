"""Localized options page with site-scoped credentials and paths."""

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

from booruflow.infrastructure.localization import LanguageCatalog


class PathRow(QWidget):
    def __init__(
        self,
        catalog: LanguageCatalog,
        *,
        directory: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.directory = directory
        self.edit = QLineEdit()
        self.button = QPushButton()
        self.button.clicked.connect(self.browse)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)
        self.retranslate()

    def retranslate(self) -> None:
        self.button.setText(self.catalog.text("options.browse"))

    def browse(self) -> None:
        current = self.edit.text().strip()
        start = str(Path(current).parent if current else Path.home())
        if self.directory:
            selected = QFileDialog.getExistingDirectory(
                self, self.catalog.text("options.choose_folder"), current or start
            )
        else:
            selected, _filter = QFileDialog.getOpenFileName(
                self,
                self.catalog.text("options.choose_database"),
                start,
                self.catalog.text("options.database_filter"),
            )
        if selected:
            self.edit.setText(selected)


class OptionsPage(QWidget):
    save_requested = Signal(dict, dict)
    language_changed = Signal(str)

    def __init__(
        self,
        catalog: LanguageCatalog,
        settings: dict[str, object] | None = None,
        credentials: dict[str, object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self._credentials = {
            "gelbooru": self._site_credentials(credentials, "gelbooru"),
            "e621": self._site_credentials(credentials, "e621"),
        }
        self._current_site = "gelbooru"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(self.title)

        self.general_group = QGroupBox()
        general_form = QFormLayout(self.general_group)
        self.language_label = QLabel()
        self.language = QComboBox()
        for code, name in self.catalog.available.items():
            self.language.addItem(name, code)
        general_form.addRow(self.language_label, self.language)
        layout.addWidget(self.general_group)

        self.sites_group = QGroupBox()
        site_form = QFormLayout(self.sites_group)
        self.site_label = QLabel()
        self.user_id_label = QLabel()
        self.api_key_label = QLabel()
        self.site = QComboBox()
        self.site.addItem("Gelbooru", "gelbooru")
        self.site.addItem("e621", "e621")
        self.user_id = QLineEdit()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_api_key = QCheckBox()
        self.show_api_key.toggled.connect(
            lambda shown: self.api_key.setEchoMode(
                QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
            )
        )
        self.site.currentIndexChanged.connect(self._site_changed)
        site_form.addRow(self.site_label, self.site)
        site_form.addRow(self.user_id_label, self.user_id)
        site_form.addRow(self.api_key_label, self.api_key)
        site_form.addRow("", self.show_api_key)
        layout.addWidget(self.sites_group)

        self.paths_group = QGroupBox()
        path_form = QFormLayout(self.paths_group)
        self.gelbooru_database_label = QLabel()
        self.e621_database_label = QLabel()
        self.grabber_directory_label = QLabel()
        self.output_root_label = QLabel()
        self.gelbooru_database = PathRow(catalog)
        self.e621_database = PathRow(catalog)
        self.grabber_directory = PathRow(catalog, directory=True)
        self.output_root = PathRow(catalog, directory=True)
        path_form.addRow(self.gelbooru_database_label, self.gelbooru_database)
        path_form.addRow(self.e621_database_label, self.e621_database)
        path_form.addRow(self.grabber_directory_label, self.grabber_directory)
        path_form.addRow(self.output_root_label, self.output_root)
        layout.addWidget(self.paths_group)
        self.note = QLabel()
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.save_button = QPushButton()
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        layout.addStretch(1)
        self._load_settings(settings or {})
        self._display_credentials("gelbooru")
        self.language.currentIndexChanged.connect(self._language_selected)
        self.retranslate()

    @staticmethod
    def _site_credentials(values: dict[str, object] | None, site: str) -> dict[str, str]:
        raw = (values or {}).get(site, {})
        if not isinstance(raw, dict):
            return {"user_id": "", "api_key": ""}
        return {"user_id": str(raw.get("user_id", "")), "api_key": str(raw.get("api_key", ""))}

    def _load_settings(self, settings: dict[str, object]) -> None:
        index = self.language.findData(str(settings.get("language", "en")))
        self.language.setCurrentIndex(index if index >= 0 else 0)
        self.gelbooru_database.edit.setText(str(settings.get("gelbooru_database", "")))
        self.e621_database.edit.setText(str(settings.get("e621_database", "")))
        self.grabber_directory.edit.setText(str(settings.get("grabber_directory", "")))
        self.output_root.edit.setText(str(settings.get("output_root", "")))

    def _capture_credentials(self, site: str) -> None:
        self._credentials[site] = {
            "user_id": self.user_id.text().strip(),
            "api_key": self.api_key.text().strip(),
        }

    def _display_credentials(self, site: str) -> None:
        self.user_id.setText(self._credentials[site]["user_id"])
        self.api_key.setText(self._credentials[site]["api_key"])

    def _site_changed(self) -> None:
        self._capture_credentials(self._current_site)
        self._current_site = str(self.site.currentData())
        self._display_credentials(self._current_site)

    def _language_selected(self) -> None:
        self.language_changed.emit(str(self.language.currentData()))

    def _save(self) -> None:
        self._capture_credentials(self._current_site)
        settings = {
            "language": str(self.language.currentData()),
            "gelbooru_database": self.gelbooru_database.edit.text().strip(),
            "e621_database": self.e621_database.edit.text().strip(),
            "grabber_directory": self.grabber_directory.edit.text().strip(),
            "output_root": self.output_root.edit.text().strip(),
        }
        self.save_requested.emit(settings, self._credentials)

    def retranslate(self) -> None:
        text = self.catalog.text
        self.title.setText(text("nav.options"))
        self.general_group.setTitle(text("options.general"))
        self.language_label.setText(text("options.language"))
        self.sites_group.setTitle(text("options.credentials"))
        self.site_label.setText(text("options.site"))
        self.user_id_label.setText(text("options.user_id"))
        self.api_key_label.setText(text("options.api_key"))
        self.show_api_key.setText(text("options.show_api_key"))
        self.paths_group.setTitle(text("options.paths"))
        self.gelbooru_database_label.setText(text("options.gelbooru_database"))
        self.e621_database_label.setText(text("options.e621_database"))
        self.grabber_directory_label.setText(text("options.grabber_folder"))
        self.output_root_label.setText(text("options.output_folder"))
        self.gelbooru_database.retranslate()
        self.e621_database.retranslate()
        self.grabber_directory.retranslate()
        self.output_root.retranslate()
        self.note.setText(text("options.note"))
        self.save_button.setText(text("options.save"))
