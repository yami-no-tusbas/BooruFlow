"""PySide6 application bootstrap."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from booruflow.application import resolve_capabilities
from booruflow.infrastructure.grabber import GrabberInstallation
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.settings import JsonSettingsRepository
from booruflow.infrastructure.task_repository import JsonTaskRepository
from booruflow.presentation.pyside6.main_window import MainWindow


def project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def initial_settings(root: Path) -> dict[str, object]:
    return {
        "language": "en",
        "gelbooru_database": str(root / "data" / "databases" / "gelbooru_tags.db"),
        "e621_database": str(root / "data" / "databases" / "e621_tags.db"),
        "grabber_directory": "",
        "output_root": str(root / "var" / "results"),
        "image_analysis_download_prefetch": 10,
        "image_analysis_analysis_prefetch": 2,
        "image_analysis_worker_heartbeat_interval": 2,
        "image_analysis_worker_stale_timeout": 15,
        "image_analysis_wd14_enabled": True,
        "image_analysis_wd14_model_id": "SmilingWolf/wd-vit-tagger-v3",
        "image_analysis_wd14_model_directory": str(
            root / "var" / "models" / "image_analysis" / "wd-vit-tagger-v3"
        ),
        "image_analysis_wd14_store_threshold": 0.10,
        "image_analysis_wd14_display_threshold": 0.30,
        "image_analysis_worker_recycle_after": 100,
        "image_analysis_drop_confirmation_threshold": 250,
    }


def create_application(argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    QCoreApplication.setOrganizationName("BooruFlow")
    QCoreApplication.setApplicationName("BooruFlow")
    root = project_root()
    config = root / "config"
    settings_repository = JsonSettingsRepository(config / "booruflow_settings.json")
    credentials_repository = JsonSettingsRepository(config / "booruflow_credentials.json")
    task_repository = JsonTaskRepository(root / "var" / "state" / "task_history.json")
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
        task_repository=task_repository,
        project_root=root,
        python_executable=sys.executable,
    )
    return app, window


def run(argv: list[str] | None = None) -> int:
    app, window = create_application(argv)
    window.show()
    return app.exec()
