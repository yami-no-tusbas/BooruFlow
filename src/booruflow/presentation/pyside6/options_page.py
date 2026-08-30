"""Localized options page with site-scoped credentials and paths."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.settings import migrate_blacklist_setting


class PathRow(QWidget):
    action_requested = Signal()
    def __init__(
        self,
        catalog: LanguageCatalog,
        *,
        directory: bool = False,
        dialog_title_key: str = "options.choose_database",
        file_filter_key: str = "options.database_filter",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.directory = directory
        self.dialog_title_key = dialog_title_key
        self.file_filter_key = file_filter_key
        self.edit = QLineEdit()
        self.button = QPushButton()
        self.action = QPushButton()
        self.action.hide()
        self.button.clicked.connect(self.browse)
        self.action.clicked.connect(self.action_requested.emit)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)
        layout.addWidget(self.action)
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
                self.catalog.text(self.dialog_title_key),
                start,
                self.catalog.text(self.file_filter_key),
            )
        if selected:
            self.edit.setText(selected)


class OptionsPage(QWidget):
    save_requested = Signal(dict, dict)
    language_changed = Signal(str)
    database_update_requested = Signal(str, str)
    database_stop_requested = Signal()
    browser_test_requested = Signal(dict)
    browser_reset_requested = Signal()
    embedded_session_open_requested = Signal()
    embedded_session_test_requested = Signal()
    embedded_session_reset_requested = Signal()
    publication_backend_changed = Signal(str)

    def __init__(
        self,
        catalog: LanguageCatalog,
        settings: dict[str, object] | None = None,
        credentials: dict[str, object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self._settings = dict(settings or {})
        self._credentials = {
            "gelbooru": self._site_credentials(credentials, "gelbooru"),
            "e621": self._site_credentials(credentials, "e621"),
        }
        self._current_site = "gelbooru"
        self._database_running_site = ""
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
        path_grid = QGridLayout(self.paths_group)
        path_grid.setColumnMinimumWidth(0, 190)
        path_grid.setColumnStretch(1, 1)
        self.gelbooru_database_label = QLabel()
        self.e621_database_label = QLabel()
        self.blacklist_file_label = QLabel()
        self.output_root_label = QLabel()
        self.gelbooru_database = PathRow(catalog)
        self.e621_database = PathRow(catalog)
        self.blacklist_file = PathRow(
            catalog,
            dialog_title_key="options.choose_blacklist",
            file_filter_key="options.text_filter",
        )
        self.output_root = PathRow(catalog, directory=True)
        self.gelbooru_database.action.show()
        self.e621_database.action.show()
        self.gelbooru_database.action_requested.connect(lambda: self._database_action("gelbooru"))
        self.e621_database.action_requested.connect(lambda: self._database_action("e621"))
        path_grid.addWidget(self.gelbooru_database_label, 0, 0)
        path_grid.addWidget(self.gelbooru_database, 0, 1)
        path_grid.addWidget(self.e621_database_label, 1, 0)
        path_grid.addWidget(self.e621_database, 1, 1)
        path_grid.addWidget(self.blacklist_file_label, 2, 0)
        path_grid.addWidget(self.blacklist_file, 2, 1)
        path_grid.addWidget(self.output_root_label, 3, 0)
        path_grid.addWidget(self.output_root, 3, 1)
        layout.addWidget(self.paths_group)
        self.browser_group = QGroupBox("Navigateur pour ouvrir les posts Gelbooru")
        browser_form = QFormLayout(self.browser_group)
        self.browser_mode = QComboBox()
        self.browser_mode.addItem("System default browser", "system")
        self.browser_mode.addItem("Dedicated browser profile", "dedicated")
        self.browser_mode.addItem("Custom command", "custom")
        self.browser_command = QLineEdit()
        self.browser_command.setPlaceholderText('"C:\\Path\\browser.exe" {url}')
        self.browser_command_label = QLabel("Command template")
        self.clear_browser_profile = QCheckBox("Clear profile on close")
        self.reset_browser_profile = QPushButton("Reset dedicated profile")
        self.test_browser = QPushButton("Test Gelbooru browser")
        self.browser_explanation = QLabel(
            "BooruFlow only controls how Gelbooru pages are opened. It does not read "
            "your browser cookies, passwords, history, or tabs."
        )
        self.browser_explanation.setWordWrap(True)
        browser_form.addRow("Open Gelbooru with", self.browser_mode)
        browser_form.addRow(self.browser_command_label, self.browser_command)
        browser_form.addRow("", self.clear_browser_profile)
        browser_form.addRow("", self.reset_browser_profile)
        browser_form.addRow("", self.test_browser)
        browser_form.addRow("", self.browser_explanation)
        layout.addWidget(self.browser_group)
        self.browser_mode.currentIndexChanged.connect(self._update_browser_fields)
        self.browser_command.textChanged.connect(self._update_browser_fields)
        self.test_browser.clicked.connect(lambda: self.browser_test_requested.emit(self._browser_settings()))
        self.reset_browser_profile.clicked.connect(self.browser_reset_requested.emit)
        self.publisher_group = QGroupBox("Publication automatique Gelbooru")
        publisher_form = QFormLayout(self.publisher_group)
        self.publish_backend = QComboBox()
        self.publish_backend.addItem("Navigateur intégré (recommandé)", "embedded")
        self.publish_backend.addItem("Navigateur externe Chromium / CDP", "cdp")
        self.publish_backend.addItem("Désactivé", "disabled")
        self.open_embedded_session = QPushButton("Ouvrir la session Gelbooru")
        self.test_embedded_session = QPushButton("Tester la session Gelbooru")
        self.reset_embedded_session = QPushButton("Réinitialiser la session Gelbooru")
        self.embedded_session_status = QLabel("État : Non testé")
        self.publisher_explanation = QLabel(
            "Le navigateur intégré conserve une session Gelbooru isolée. Le navigateur utilisé "
            "pour ouvrir les posts reste un réglage indépendant."
        )
        self.publisher_explanation.setWordWrap(True)
        publisher_form.addRow("Mode", self.publish_backend)
        publisher_form.addRow("", self.open_embedded_session)
        publisher_form.addRow("", self.test_embedded_session)
        publisher_form.addRow("", self.reset_embedded_session)
        publisher_form.addRow("", self.embedded_session_status)
        publisher_form.addRow("", self.publisher_explanation)
        layout.addWidget(self.publisher_group)
        self.open_embedded_session.clicked.connect(self.embedded_session_open_requested.emit)
        self.test_embedded_session.clicked.connect(self.embedded_session_test_requested.emit)
        self.reset_embedded_session.clicked.connect(self.embedded_session_reset_requested.emit)
        self.publish_backend.currentIndexChanged.connect(self._publish_backend_selected)
        self.image_analysis_group = QGroupBox()
        image_analysis_form = QFormLayout(self.image_analysis_group)
        self.download_prefetch_label = QLabel(); self.download_prefetch = QSpinBox()
        self.download_prefetch.setRange(1, 100)
        self.analysis_prefetch_label = QLabel(); self.analysis_prefetch = QSpinBox()
        self.analysis_prefetch.setRange(1, 10)
        self.wd14_enabled = QCheckBox("WD14 local")
        self.wd14_threshold_label = QLabel("Seuil d’affichage WD14")
        self.wd14_threshold = QDoubleSpinBox(); self.wd14_threshold.setRange(0, 1)
        self.wd14_threshold.setDecimals(2); self.wd14_threshold.setSingleStep(0.05)
        image_analysis_form.addRow(self.download_prefetch_label, self.download_prefetch)
        image_analysis_form.addRow(self.analysis_prefetch_label, self.analysis_prefetch)
        image_analysis_form.addRow("", self.wd14_enabled)
        image_analysis_form.addRow(self.wd14_threshold_label, self.wd14_threshold)
        layout.addWidget(self.image_analysis_group)
        self.note = QLabel()
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.database_status = QLabel()
        self.database_status.setWordWrap(True)
        layout.addWidget(self.database_status)
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
        settings, _migrated = migrate_blacklist_setting(settings)
        index = self.language.findData(str(settings.get("language", "en")))
        self.language.setCurrentIndex(max(index, 0))
        self.gelbooru_database.edit.setText(str(settings.get("gelbooru_database", "")))
        self.e621_database.edit.setText(str(settings.get("e621_database", "")))
        self.blacklist_file.edit.setText(str(settings.get("blacklist_file", "")))
        self.output_root.edit.setText(str(settings.get("output_root", "")))
        self.download_prefetch.setValue(int(settings.get("image_analysis_download_prefetch", 10)))
        self.analysis_prefetch.setValue(int(settings.get("image_analysis_analysis_prefetch", 2)))
        self.wd14_enabled.setChecked(bool(settings.get("image_analysis_wd14_enabled", True)))
        self.wd14_threshold.setValue(float(
            settings.get("image_analysis_wd14_display_threshold", 0.30)
        ))
        mode_index = self.browser_mode.findData(str(settings.get("gelbooru_browser_mode", "system")))
        self.browser_mode.setCurrentIndex(max(mode_index, 0))
        self.browser_command.setText(str(settings.get("gelbooru_browser_custom_command", "")))
        self.clear_browser_profile.setChecked(bool(settings.get("gelbooru_browser_clear_profile_on_close", False)))
        publish_index = self.publish_backend.findData(
            str(settings.get("gelbooru_publish_backend", "embedded"))
        )
        self.publish_backend.setCurrentIndex(max(publish_index, 0))
        self._update_browser_fields()
        self._update_publish_fields()

    def _browser_settings(self) -> dict[str, object]:
        return {
            "gelbooru_browser_mode": str(self.browser_mode.currentData()),
            "gelbooru_browser_custom_command": self.browser_command.text().strip(),
            "gelbooru_browser_clear_profile_on_close": self.clear_browser_profile.isChecked(),
            "gelbooru_publish_backend": str(self.publish_backend.currentData()),
        }

    def _update_browser_fields(self) -> None:
        mode = str(self.browser_mode.currentData())
        custom = mode == "custom"
        dedicated = mode == "dedicated"
        self.browser_command_label.setVisible(custom)
        self.browser_command.setVisible(custom)
        self.clear_browser_profile.setVisible(dedicated)
        self.reset_browser_profile.setVisible(dedicated)
        valid = not custom or "{url}" in self.browser_command.text()
        self.browser_command.setStyleSheet("" if valid else "border: 1px solid #d9534f;")
        self.test_browser.setEnabled(valid)

    def _update_publish_fields(self) -> None:
        backend = str(self.publish_backend.currentData())
        embedded = backend == "embedded"
        self.open_embedded_session.setEnabled(backend != "disabled")
        self.test_embedded_session.setEnabled(backend != "disabled")
        self.reset_embedded_session.setEnabled(embedded)
        if backend == "cdp":
            self.open_embedded_session.setText("Ouvrir le navigateur Gelbooru dédié")
            self.publisher_explanation.setText(
                "Le mode CDP pilote uniquement un profil Chromium dédié via 127.0.0.1. "
                "Les cookies, mots de passe et jetons restent dans le navigateur."
            )
        elif backend == "disabled":
            self.open_embedded_session.setText("Ouvrir la session Gelbooru")
            self.publisher_explanation.setText("La publication Gelbooru est désactivée.")
        else:
            self.open_embedded_session.setText("Ouvrir la session intégrée")
            self.publisher_explanation.setText(
                "Le navigateur intégré conserve une session Gelbooru isolée. Le navigateur "
                "utilisé pour ouvrir les posts reste un réglage indépendant."
            )

    def _publish_backend_selected(self) -> None:
        self._update_publish_fields()
        self.publication_backend_changed.emit(str(self.publish_backend.currentData()))

    def set_embedded_session_test_running(self, running: bool) -> None:
        self.test_embedded_session.setEnabled(
            not running and str(self.publish_backend.currentData()) != "disabled"
        )
        if running:
            self.embedded_session_status.setText("État : Test en cours…")

    def show_embedded_session_test_result(self, result: str) -> None:
        self.set_embedded_session_test_running(False)
        self.embedded_session_status.setText(f"État : {result}")

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
            **self._settings,
            "language": str(self.language.currentData()),
            "gelbooru_database": self.gelbooru_database.edit.text().strip(),
            "e621_database": self.e621_database.edit.text().strip(),
            "blacklist_file": self.blacklist_file.edit.text().strip(),
            "output_root": self.output_root.edit.text().strip(),
            "image_analysis_download_prefetch": self.download_prefetch.value(),
            "image_analysis_analysis_prefetch": self.analysis_prefetch.value(),
            "image_analysis_worker_heartbeat_interval": 2,
            "image_analysis_worker_stale_timeout": 15,
            "image_analysis_wd14_enabled": self.wd14_enabled.isChecked(),
            "image_analysis_wd14_display_threshold": self.wd14_threshold.value(),
            **self._browser_settings(),
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
        self.image_analysis_group.setTitle(text("options.image_analysis"))
        self.download_prefetch_label.setText(text("options.download_prefetch"))
        self.analysis_prefetch_label.setText(text("options.analysis_prefetch"))
        self.gelbooru_database_label.setText(text("options.gelbooru_database"))
        self.e621_database_label.setText(text("options.e621_database"))
        self.blacklist_file_label.setText(text("options.blacklist_file"))
        self.output_root_label.setText(text("options.output_folder"))
        self.gelbooru_database.retranslate()
        self.e621_database.retranslate()
        self.blacklist_file.retranslate()
        self.output_root.retranslate()
        for site, row in (("gelbooru", self.gelbooru_database), ("e621", self.e621_database)):
            row.action.setText(text("options.stop_database") if self._database_running_site == site else text("options.update_database"))
        self.note.setText(text("options.note"))
        self.save_button.setText(text("options.save"))

    def set_database_running(self, running: bool, site: str = "") -> None:
        self._database_running_site = site if running else ""
        self.gelbooru_database.action.setEnabled(not running or site == "gelbooru")
        self.e621_database.action.setEnabled(not running or site == "e621")
        self.retranslate()
        self.database_status.setText(
            self.catalog.text("options.database_running", site=site) if running else ""
        )

    def _database_action(self, site: str) -> None:
        if self._database_running_site:
            if self._database_running_site == site: self.database_stop_requested.emit()
            return
        row = self.gelbooru_database if site == "gelbooru" else self.e621_database
        self.database_update_requested.emit(site, row.edit.text().strip())
