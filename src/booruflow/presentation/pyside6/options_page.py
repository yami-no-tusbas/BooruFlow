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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from booruflow.application.database_paths import gelbooru_tag_database
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
    alias_update_requested = Signal(str, str)
    database_stop_requested = Signal()
    browser_test_requested = Signal(dict)
    browser_reset_requested = Signal()
    embedded_session_open_requested = Signal()
    embedded_session_test_requested = Signal()
    embedded_session_reset_requested = Signal()
    publication_backend_changed = Signal(str)
    credentials_test_requested = Signal(str, dict)

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
        self._publisher_status = ("not_tested", "")
        self._credential_statuses = {"gelbooru": "not_tested", "e621": "not_tested"}
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
        self.show_api_key = QToolButton()
        self.show_api_key.setCheckable(True)
        self.show_api_key.toggled.connect(
            lambda shown: self.api_key.setEchoMode(
                QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
            )
        )
        self.site.currentIndexChanged.connect(self._site_changed)
        site_form.addRow(self.site_label, self.site)
        site_form.addRow(self.user_id_label, self.user_id)
        key_row = QHBoxLayout(); key_row.addWidget(self.api_key, 1); key_row.addWidget(self.show_api_key)
        site_form.addRow(self.api_key_label, key_row)
        self.test_credentials = QPushButton()
        site_form.addRow("", self.test_credentials)
        self.credentials_status = QLabel()
        site_form.addRow("", self.credentials_status)
        self.test_credentials.clicked.connect(self._test_credentials)
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
        self.database_site_label = QLabel()
        self.database_path_label = QLabel()
        self.database_site = QComboBox()
        self.database_site.addItem("Gelbooru", "gelbooru")
        self.database_site.addItem("e621", "e621")
        self.database_path = PathRow(catalog)
        self.database_path.action.show()
        self.database_site.currentIndexChanged.connect(self._database_site_changed)
        self.database_path.edit.textChanged.connect(self._database_path_edited)
        self.database_path.action_requested.connect(
            lambda: self._database_action(str(self.database_site.currentData()))
        )
        self.gelbooru_database.action.show()
        self.e621_database.action.show()
        self.gelbooru_database.action_requested.connect(lambda: self._database_action("gelbooru"))
        self.e621_database.action_requested.connect(lambda: self._database_action("e621"))
        path_grid.addWidget(self.database_site_label, 0, 0)
        path_grid.addWidget(self.database_site, 0, 1)
        path_grid.addWidget(self.database_path_label, 1, 0)
        path_grid.addWidget(self.database_path, 1, 1)
        path_grid.addWidget(self.output_root_label, 2, 0)
        path_grid.addWidget(self.output_root, 2, 1)
        self.gelbooru_database.hide(); self.e621_database.hide(); self.blacklist_file.hide()
        self.gelbooru_database_label.hide(); self.e621_database_label.hide(); self.blacklist_file_label.hide()
        self.alias_actions = QWidget()
        self.alias_label = QLabel()
        alias_layout = QHBoxLayout(self.alias_actions)
        alias_layout.setContentsMargins(0, 0, 0, 0)
        self.alias_update = QPushButton()
        self.alias_pending = QPushButton()
        self.alias_reconcile = QPushButton()
        alias_layout.addWidget(self.alias_update)
        alias_layout.addWidget(self.alias_pending)
        alias_layout.addWidget(self.alias_reconcile)
        alias_layout.addStretch(1)
        self.alias_status = QLabel()
        self.alias_status.setWordWrap(True)
        self.alias_label.hide(); self.alias_actions.hide(); self.alias_status.hide()
        self.alias_update.clicked.connect(lambda: self._alias_action("incremental"))
        self.alias_pending.clicked.connect(lambda: self._alias_action("pending"))
        self.alias_reconcile.clicked.connect(lambda: self._alias_action("full"))
        layout.addWidget(self.paths_group)
        self.browser_group = QGroupBox()
        browser_form = QFormLayout(self.browser_group)
        self.browser_mode = QComboBox()
        self.browser_mode.addItem("", "system")
        self.browser_mode.addItem("", "dedicated")
        self.browser_mode.addItem("", "custom")
        self.browser_command = QLineEdit()
        self.browser_command.setPlaceholderText(self.catalog.text("options.browser_command_placeholder"))
        self.browser_command_label = QLabel()
        self.clear_browser_profile = QCheckBox()
        self.reset_browser_profile = QPushButton()
        self.test_browser = QPushButton()
        self.browser_explanation = QLabel()
        self.browser_explanation.setWordWrap(True)
        self.browser_mode_label = QLabel(); browser_form.addRow(self.browser_mode_label, self.browser_mode)
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
        self.publisher_group = QGroupBox()
        publisher_form = QFormLayout(self.publisher_group)
        self.publish_backend = QComboBox()
        self.publish_backend.addItem("", "embedded")
        self.publish_backend.addItem("", "cdp")
        self.publish_backend.addItem("", "disabled")
        self.open_embedded_session = QPushButton()
        self.test_embedded_session = QPushButton()
        self.reset_embedded_session = QPushButton()
        self.embedded_session_status = QLabel()
        self.publisher_explanation = QLabel()
        self.publisher_explanation.setWordWrap(True)
        self.publish_backend_label = QLabel(); publisher_form.addRow(self.publish_backend_label, self.publish_backend)
        self.publish_backend.hide(); self.publish_backend_label.hide()
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
        self.wd14_enabled = QCheckBox()
        self.wd14_threshold_label = QLabel()
        self.wd14_threshold = QDoubleSpinBox(); self.wd14_threshold.setRange(0, 100)
        self.wd14_threshold.setDecimals(0); self.wd14_threshold.setSingleStep(5); self.wd14_threshold.setSuffix(" %")
        image_analysis_form.addRow("", self.wd14_enabled)
        image_analysis_form.addRow(self.wd14_threshold_label, self.wd14_threshold)
        self.image_advanced = QGroupBox(); self.image_advanced.setCheckable(True); self.image_advanced.setChecked(False)
        advanced_form = QFormLayout(self.image_advanced)
        advanced_form.addRow(self.download_prefetch_label, self.download_prefetch)
        advanced_form.addRow(self.analysis_prefetch_label, self.analysis_prefetch)
        self.store_threshold_label = QLabel(); self.store_threshold = QDoubleSpinBox(); self.store_threshold.setRange(0, 100); self.store_threshold.setSuffix(" %")
        self.heartbeat_label = QLabel(); self.heartbeat = QSpinBox(); self.heartbeat.setRange(1, 60)
        self.stale_timeout_label = QLabel(); self.stale_timeout = QSpinBox(); self.stale_timeout.setRange(2, 300)
        self.recycle_count_label = QLabel(); self.recycle_count = QSpinBox(); self.recycle_count.setRange(0, 100000)
        advanced_form.addRow(self.store_threshold_label, self.store_threshold)
        advanced_form.addRow(self.heartbeat_label, self.heartbeat)
        advanced_form.addRow(self.stale_timeout_label, self.stale_timeout)
        advanced_form.addRow(self.recycle_count_label, self.recycle_count)
        image_analysis_form.addRow(self.image_advanced)
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
        database = gelbooru_tag_database(settings)
        self.gelbooru_database.edit.setText(str(database) if database else "")
        self.e621_database.edit.setText(str(settings.get("e621_database", "")))
        self.blacklist_file.edit.setText(str(settings.get("blacklist_file", "")))
        self.output_root.edit.setText(str(settings.get("output_root", "")))
        self.download_prefetch.setValue(int(settings.get("image_analysis_download_prefetch", 10)))
        self.analysis_prefetch.setValue(int(settings.get("image_analysis_analysis_prefetch", 2)))
        self.wd14_enabled.setChecked(bool(settings.get("image_analysis_wd14_enabled", True)))
        self.wd14_threshold.setValue(self._percent_from_setting(
            settings.get("image_analysis_wd14_display_threshold", 0.30), 0.30
        ))
        self.store_threshold.setValue(self._percent_from_setting(
            settings.get("image_analysis_wd14_store_threshold", 0.10), 0.10
        ))
        self.heartbeat.setValue(int(settings.get("image_analysis_worker_heartbeat_interval", 2)))
        self.stale_timeout.setValue(int(settings.get("image_analysis_worker_stale_timeout", 15)))
        self.recycle_count.setValue(int(settings.get(
            "image_analysis_worker_recycle_after",
            settings.get("image_analysis_worker_recycle_count", 100),
        )))
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
        self._database_site_changed()

    @staticmethod
    def _percent_from_setting(value: object, default: float) -> float:
        """Present normal fractions and safely recover refactor-era percent values."""
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return default * 100
        if 0 <= numeric <= 1:
            return numeric * 100
        return numeric if 0 <= numeric <= 100 else default * 100

    def _database_site_changed(self) -> None:
        row = self.gelbooru_database if self.database_site.currentData() == "gelbooru" else self.e621_database
        self.database_path.edit.blockSignals(True); self.database_path.edit.setText(row.edit.text()); self.database_path.edit.blockSignals(False)

    def _database_path_edited(self, value: str) -> None:
        row = self.gelbooru_database if self.database_site.currentData() == "gelbooru" else self.e621_database
        row.edit.setText(value)

    def _test_credentials(self) -> None:
        self._capture_credentials(self._current_site)
        self.credentials_test_requested.emit(self._current_site, dict(self._credentials[self._current_site]))

    def set_credential_test_running(self, site: str) -> None:
        self._credential_statuses[site] = "testing"
        if site == self._current_site:
            self._update_credential_status()

    def show_credential_test_result(self, site: str, status: str) -> None:
        self._credential_statuses[site] = status
        if site == self._current_site:
            self._update_credential_status()

    def _update_credential_status(self) -> None:
        status = self._credential_statuses[self._current_site]
        self.credentials_status.setText(self.catalog.text(f"options.credentials_status_{status}"))
        self.test_credentials.setEnabled(status != "testing")

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
            self.open_embedded_session.setText(self.catalog.text("options.publisher_login"))
            self.publisher_explanation.setText(self.catalog.text("options.publisher_cdp_explanation"))
        elif backend == "disabled":
            self.open_embedded_session.setText(self.catalog.text("options.publisher_login"))
            self.publisher_explanation.setText(self.catalog.text("options.publisher_disabled"))
        else:
            self.open_embedded_session.setText(self.catalog.text("options.publisher_login"))
            self.publisher_explanation.setText(self.catalog.text("options.publisher_explanation"))

    def _publish_backend_selected(self) -> None:
        self._update_publish_fields()
        self.publication_backend_changed.emit(str(self.publish_backend.currentData()))

    def set_embedded_session_test_running(self, running: bool) -> None:
        self.test_embedded_session.setEnabled(
            not running and str(self.publish_backend.currentData()) != "disabled"
        )
        if running:
            self._publisher_status = ("testing", "")
            self.embedded_session_status.setText(self.catalog.text("options.publisher_status_testing"))

    def show_embedded_session_test_result(self, result: str) -> None:
        self.set_embedded_session_test_running(False)
        self._publisher_status = ("result", result)
        self.embedded_session_status.setText(self.catalog.text("options.publisher_status", status=result))

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
        self.user_id_label.setText(self.catalog.text(
            "options.e621_username" if self._current_site == "e621" else "options.user_id"
        ))
        self._update_credential_status()

    def _language_selected(self) -> None:
        self.language_changed.emit(str(self.language.currentData()))

    def _save(self) -> None:
        self._capture_credentials(self._current_site)
        settings = {
            **self._settings,
            "language": str(self.language.currentData()),
            "gelbooru_tag_database": self.gelbooru_database.edit.text().strip(),
            "gelbooru_database": self.gelbooru_database.edit.text().strip(),
            "e621_database": self.e621_database.edit.text().strip(),
            "blacklist_file": self.blacklist_file.edit.text().strip(),
            "output_root": self.output_root.edit.text().strip(),
            "image_analysis_download_prefetch": self.download_prefetch.value(),
            "image_analysis_analysis_prefetch": self.analysis_prefetch.value(),
            "image_analysis_worker_heartbeat_interval": self.heartbeat.value(),
            "image_analysis_worker_stale_timeout": self.stale_timeout.value(),
            "image_analysis_worker_recycle_after": self.recycle_count.value(),
            "image_analysis_wd14_enabled": self.wd14_enabled.isChecked(),
            "image_analysis_wd14_display_threshold": self.wd14_threshold.value() / 100,
            "image_analysis_wd14_store_threshold": self.store_threshold.value() / 100,
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
        self.user_id_label.setText(text(
            "options.e621_username" if self._current_site == "e621" else "options.user_id"
        ))
        self.api_key_label.setText(text("options.api_key"))
        self.show_api_key.setText(text("options.reveal_api_key"))
        self.show_api_key.setToolTip(text("options.reveal_api_key_tip"))
        self.test_credentials.setText(text("options.test_credentials"))
        self._update_credential_status()
        self.paths_group.setTitle(text("options.paths"))
        self.database_site_label.setText(text("options.site")); self.database_path_label.setText(text("options.database_path"))
        self.image_analysis_group.setTitle(text("options.image_analysis"))
        self.wd14_enabled.setText(text("options.wd14_enabled")); self.wd14_threshold_label.setText(text("options.wd14_display_threshold"))
        self.image_advanced.setTitle(text("options.advanced")); self.store_threshold_label.setText(text("options.wd14_store_threshold"))
        self.heartbeat_label.setText(text("options.worker_heartbeat")); self.stale_timeout_label.setText(text("options.worker_stale")); self.recycle_count_label.setText(text("options.worker_recycle"))
        self.download_prefetch_label.setText(text("options.download_prefetch"))
        self.analysis_prefetch_label.setText(text("options.analysis_prefetch"))
        self.wd14_enabled.setToolTip(text("options.wd14_enabled_tip"))
        self.wd14_threshold.setToolTip(text("options.wd14_display_threshold_tip"))
        self.store_threshold.setToolTip(text("options.wd14_store_threshold_tip"))
        self.download_prefetch.setToolTip(text("options.download_prefetch_tip"))
        self.analysis_prefetch.setToolTip(text("options.analysis_prefetch_tip"))
        self.heartbeat.setToolTip(text("options.worker_heartbeat_tip"))
        self.stale_timeout.setToolTip(text("options.worker_stale_tip"))
        self.recycle_count.setToolTip(text("options.worker_recycle_tip"))
        self.gelbooru_database_label.setText(text("options.gelbooru_database"))
        self.e621_database_label.setText(text("options.e621_database"))
        self.blacklist_file_label.setText(text("options.blacklist_file"))
        self.output_root_label.setText(text("options.output_folder"))
        self.gelbooru_database.retranslate()
        self.e621_database.retranslate()
        self.blacklist_file.retranslate()
        self.output_root.retranslate()
        self.database_path.retranslate()
        for site, row in (("gelbooru", self.gelbooru_database), ("e621", self.e621_database)):
            row.action.setText(text("options.stop_database") if self._database_running_site == site else text("options.update_database"))
        self.note.setText(text("options.note"))
        self.alias_update.setText(
            text("options.stop_database")
            if self._database_running_site.startswith("aliases:")
            else text("options.alias_update")
        )
        self.alias_label.setText(text("options.alias_label"))
        self.alias_pending.setText(text("options.alias_pending"))
        self.alias_reconcile.setText(text("options.alias_reconcile"))
        self.save_button.setText(text("options.save"))
        self.browser_group.setTitle(text("options.browser")); self.browser_mode_label.setText(text("options.browser_open_external")); self.browser_command_label.setText(text("options.browser_command")); self.clear_browser_profile.setText(text("options.browser_clear_profile")); self.reset_browser_profile.setText(text("options.browser_reset")); self.test_browser.setText(text("options.browser_test")); self.browser_explanation.setText(text("options.browser_explanation"))
        self.publisher_group.setTitle(text("options.publisher")); self.open_embedded_session.setText(text("options.publisher_login")); self.test_embedded_session.setText(text("options.publisher_check")); self.reset_embedded_session.setText(text("options.publisher_reset"))
        status_kind, status_value = self._publisher_status
        self.embedded_session_status.setText(
            text("options.publisher_status_testing") if status_kind == "testing"
            else text("options.publisher_status", status=status_value) if status_kind == "result"
            else text("options.publisher_status_not_tested")
        )
        for index, key in enumerate(("system", "dedicated", "custom")): self.browser_mode.setItemText(index, text(f"options.browser_mode_{key}"))
        self._update_publish_fields()

    def set_database_running(self, running: bool, site: str = "") -> None:
        self._database_running_site = site if running else ""
        self.gelbooru_database.action.setEnabled(not running or site == "gelbooru")
        self.e621_database.action.setEnabled(not running or site == "e621")
        alias_running = running and site.startswith("aliases:")
        self.alias_update.setEnabled(not running or alias_running)
        self.alias_pending.setEnabled(not running)
        self.alias_reconcile.setEnabled(not running)
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

    def _alias_action(self, mode: str) -> None:
        if self._database_running_site.startswith("aliases:"):
            self.database_stop_requested.emit()
            return
        from booruflow.application.database_paths import gelbooru_alias_database

        alias_database = gelbooru_alias_database(self._settings)
        self.alias_update_requested.emit(mode, str(alias_database) if alias_database else "")

    def set_alias_summary(self, values: dict[str, str]) -> None:
        state = values.get("state", "unknown")
        state_text = self.catalog.text(f"options.alias_state_{state}")
        self.alias_status.setText(self.catalog.text(
            "options.alias_summary",
            active=values.get("active", "0"), pending=values.get("pending", "0"),
            missing=values.get("missing", "0"), new=values.get("new", "0"),
            modified=values.get("modified", "0"),
            checkpoint=values.get("checkpoint", "0"), state=state_text,
        ))
