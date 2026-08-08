"""PySide6 application bootstrap."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from booruflow.application import resolve_capabilities
from booruflow.infrastructure.grabber import GrabberInstallation
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.settings import JsonSettingsRepository
from booruflow.presentation.pyside6.main_window import MainWindow


def project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def read_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def initial_settings(root: Path) -> dict[str, object]:
    legacy = read_json(root / "config" / "artist_by_tag_gui_settings.json")
    return {
        "language": "en",
        "gelbooru_database": str(
            legacy.get(
                "local_gel_db",
                root / "data" / "databases" / "g_tags_260712_blacklist.db",
            )
        ),
        "e621_database": str(
            legacy.get(
                "local_e621_db",
                root / "data" / "databases" / "e621_tags.db",
            )
        ),
        "grabber_directory": str(legacy.get("grabber", "")),
    }


def create_application(argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    QCoreApplication.setOrganizationName("BooruFlow")
    QCoreApplication.setApplicationName("BooruFlow")
    root = project_root()
    config = root / "config"
    settings_repository = JsonSettingsRepository(config / "booruflow_settings.json")
    credentials_repository = JsonSettingsRepository(config / "booruflow_credentials.json")
    settings = settings_repository.load()
    if not settings:
        settings = initial_settings(root)
        settings_repository.save(settings)
    grabber_value = str(settings.get("grabber_directory", "")).strip()
    grabber = Path(grabber_value) if grabber_value else None
    capabilities = resolve_capabilities(GrabberInstallation(grabber))
    catalog = LanguageCatalog(
        root / "resources" / "i18n",
        str(settings.get("language", "en")),
    )
    window = MainWindow(
        capabilities,
        catalog,
        settings_repository=settings_repository,
        credentials_repository=credentials_repository,
    )
    return app, window


def run(argv: list[str] | None = None) -> int:
    app, window = create_application(argv)
    window.show()
    return app.exec()
