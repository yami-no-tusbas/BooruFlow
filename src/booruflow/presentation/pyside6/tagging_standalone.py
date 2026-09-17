"""Standalone Gelbooru tagging assistant for community contributors."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.settings import JsonSettingsRepository
from booruflow.presentation.pyside6.tagging_controller import TaggingController
from booruflow.presentation.pyside6.tagging_page import TaggingPage
from booruflow.presentation.pyside6.ui_logging import sanitize_log_text


def project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def runtime_paths() -> tuple[Path, Path]:
    """Return writable portable root and bundled resource root."""

    if getattr(sys, "frozen", False):
        writable_root = Path(sys.executable).resolve().parent
        bundled_root = Path(getattr(sys, "_MEIPASS", writable_root))
        return writable_root, bundled_root
    root = project_root()
    return root, root


class StandaloneTaggingWindow(QMainWindow):
    def __init__(
        self,
        catalog: LanguageCatalog,
        settings_repository: JsonSettingsRepository,
        credentials_repository: JsonSettingsRepository,
    ) -> None:
        super().__init__()
        self.catalog = catalog
        self.settings_repository = settings_repository
        self.credentials_repository = credentials_repository
        settings = settings_repository.load()
        credentials = credentials_repository.load().get("gelbooru", {})
        if not isinstance(credentials, dict):
            credentials = {}

        self.setWindowTitle(self.catalog.text("standalone.title"))
        self.resize(980, 760)
        self.setMinimumSize(760, 560)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 8)
        layout.setSpacing(8)

        self.credentials_group = QGroupBox(self.catalog.text("standalone.credentials"))
        credentials_layout = QFormLayout(self.credentials_group)
        self.user_id = QLineEdit(str(credentials.get("user_id", "")))
        self.api_key = QLineEdit(str(credentials.get("api_key", "")))
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_key = QCheckBox(self.catalog.text("options.show_api_key"))
        self.show_key.toggled.connect(self._toggle_api_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key, 1)
        key_row.addWidget(self.show_key)
        self.user_id_label = QLabel(self.catalog.text("options.user_id"))
        self.api_key_label = QLabel(self.catalog.text("options.api_key"))
        credentials_layout.addRow(self.user_id_label, self.user_id)
        credentials_layout.addRow(self.api_key_label, key_row)
        self.credential_note = QLabel(self.catalog.text("standalone.credential_note"))
        self.credential_note.setWordWrap(True)
        credentials_layout.addRow(self.credential_note)
        self.save_credentials_button = QPushButton(
            self.catalog.text("standalone.save_credentials")
        )
        self.save_credentials_button.clicked.connect(self.save_credentials)
        credentials_layout.addRow("", self.save_credentials_button)
        layout.addWidget(self.credentials_group)

        self.page = TaggingPage(catalog, settings)
        layout.addWidget(self.page, 1)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1_000)
        self.log_view.setMaximumHeight(135)
        self.log_view.hide()
        layout.addWidget(self.log_view)
        footer = QHBoxLayout()
        self.status = QLabel(self.catalog.text("standalone.ready"))
        self.log_button = QPushButton(self.catalog.text("log.show"))
        self.log_button.clicked.connect(self.toggle_log)
        self.clear_button = QPushButton(self.catalog.text("log.clear"))
        self.clear_button.clicked.connect(self.log_view.clear)
        footer.addWidget(self.status, 1)
        footer.addWidget(self.log_button)
        footer.addWidget(self.clear_button)
        layout.addLayout(footer)
        self.setCentralWidget(central)

        self.controller = TaggingController(
            catalog,
            self.page,
            self.credentials,
            self.log,
            parent=self,
        )
        self.page.start_requested.connect(self.start)
        self.page.stop_requested.connect(self.controller.stop)

    def retranslate(self) -> None:
        """Refresh standalone chrome and the embedded Tagging page in place."""
        text = self.catalog.text
        self.setWindowTitle(text("standalone.title"))
        self.credentials_group.setTitle(text("standalone.credentials"))
        self.user_id_label.setText(text("options.user_id"))
        self.api_key_label.setText(text("options.api_key"))
        self.show_key.setText(text("options.show_api_key"))
        self.credential_note.setText(text("standalone.credential_note"))
        self.save_credentials_button.setText(text("standalone.save_credentials"))
        self.log_button.setText(text("log.hide" if self.log_view.isVisible() else "log.show"))
        self.clear_button.setText(text("log.clear"))
        self.page.retranslate()

    def credentials(self) -> dict[str, object]:
        return {
            "gelbooru": {
                "user_id": self.user_id.text().strip(),
                "api_key": self.api_key.text().strip(),
            }
        }

    def start(self, request) -> None:
        credentials = self.credentials()["gelbooru"]
        if not credentials["user_id"] or not credentials["api_key"]:
            message = self.catalog.text("standalone.credentials_required")
            self.page.state.setText(message)
            self.status.setText(message)
            return
        self.save_credentials(show_status=False)
        self.save_settings(request)
        self.controller.start(request)

    def save_credentials(self, _checked: bool = False, *, show_status: bool = True) -> None:
        self.credentials_repository.save(self.credentials())
        if show_status:
            self.status.setText(self.catalog.text("standalone.credentials_saved"))

    def save_settings(self, request) -> None:
        settings = self.settings_repository.load()
        settings.update(
            {
                "tagging_query": request.query,
                "tagging_pages": request.pages_per_block,
                "tagging_start": request.start_page,
                "tagging_minimum": request.minimum_tags,
                "tagging_maximum": request.maximum_tags,
                "tagging_critical": request.critical_maximum,
                "tagging_high": request.high_maximum,
            }
        )
        self.settings_repository.save(settings)

    def save_current_settings(self) -> None:
        settings = self.settings_repository.load()
        settings.update(
            {
                "tagging_query": self.page.query.text().strip(),
                **{
                    f"tagging_{key}": spin.value()
                    for key, spin in self.page.spins.items()
                },
            }
        )
        self.settings_repository.save(settings)

    def closeEvent(self, event) -> None:
        self.save_current_settings()
        self.controller.stop()
        super().closeEvent(event)

    def _toggle_api_key(self, visible: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.api_key.setEchoMode(mode)

    def toggle_log(self) -> None:
        visible = not self.log_view.isVisible()
        self.log_view.setVisible(visible)
        self.log_button.setText(self.catalog.text("log.hide" if visible else "log.show"))

    def log(self, message: str) -> None:
        self.log_view.appendPlainText(
            f"[{datetime.now():%H:%M:%S}] {sanitize_log_text(message)}"  # noqa: DTZ005
        )


def create_application(argv: list[str] | None = None) -> tuple[QApplication, StandaloneTaggingWindow]:
    application = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    QCoreApplication.setOrganizationName("BooruFlow")
    QCoreApplication.setApplicationName("Gelbooru Tagging Helper")
    writable_root, bundled_root = runtime_paths()
    config = writable_root / "config"
    settings = JsonSettingsRepository(config / "gelbooru_tagging_settings.json")
    credentials = JsonSettingsRepository(config / "gelbooru_tagging_credentials.json")
    catalog = LanguageCatalog(bundled_root / "resources" / "i18n", "en")
    return application, StandaloneTaggingWindow(catalog, settings, credentials)


def run(argv: list[str] | None = None) -> int:
    application, window = create_application(argv)
    window.show()
    return application.exec()


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
