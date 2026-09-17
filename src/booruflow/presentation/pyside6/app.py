"""PySide6 application bootstrap."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from booruflow.application import resolve_capabilities
from booruflow.application.database_paths import (
    gelbooru_alias_database,
    gelbooru_tag_database,
    migrate_database_settings,
)
from booruflow.application.hydra_model_manager import hydra_directory, migrated_hydra_settings
from booruflow.infrastructure.gelbooru_aliases import migrate_alias_catalog
from booruflow.infrastructure.grabber import GrabberInstallation
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.settings import JsonSettingsRepository, migrate_blacklist_setting
from booruflow.infrastructure.task_repository import JsonTaskRepository
from booruflow.presentation.pyside6.main_window import MainWindow


def project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def initial_settings(root: Path) -> dict[str, object]:
    return {
        "language": "en",
        "gelbooru_tag_database": str(root / "data" / "databases" / "gelbooru_tags.db"),
        "gelbooru_alias_database": str(root / "data" / "databases" / "gelbooru_aliases.db"),
        "e621_database": str(root / "data" / "databases" / "e621_tags.db"),
        "blacklist_file": "",
        "output_root": str(root / "var" / "results"),
        "gelbooru_browser_mode": "system",
        "gelbooru_browser_custom_command": "",
        "gelbooru_browser_clear_profile_on_close": False,
        "gelbooru_publish_backend": "embedded",
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
        "image_analysis_hydra_enabled": False,
        "image_analysis_hydra_source_directory": str(hydra_directory(root)),
        "image_analysis_hydra_model_path": str(hydra_directory(root) / "hydra-3.5.safetensors"),
        "image_analysis_hydra_device": "auto",
        "image_analysis_hydra_seqlen": 256,
        "image_analysis_wd14_display_threshold": 0.30,
        "image_analysis_worker_recycle_after": 100,
        "image_analysis_drop_confirmation_threshold": 250,
    }


def create_application(argv: list[str] | None = None, diagnostics=None) -> tuple[QApplication, MainWindow]:
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    if diagnostics is not None:
        diagnostics.install_qt_message_handler()
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
    else:
        settings, migrated = migrate_blacklist_setting(settings)
        settings, database_migrated = migrate_database_settings(settings, root)
        settings, hydra_migrated = migrated_hydra_settings(settings, root)
        migrated = migrated or database_migrated or hydra_migrated
        if migrated:
            settings_repository.save(settings)
    alias_database = gelbooru_alias_database(settings)
    if alias_database is not None and not alias_database.exists():
        migrate_alias_catalog(gelbooru_tag_database(settings), alias_database)
    grabber_executable = str(settings.get("grabber_executable", "")).strip() or shutil.which(
        "Grabber.exe"
    )
    grabber = Path(grabber_executable).parent if grabber_executable else None
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
    if diagnostics is not None:
        diagnostics.set_logger(window.log_threadsafe)
    return app, window


def run(argv: list[str] | None = None, diagnostics=None) -> int:
    app, window = create_application(argv, diagnostics)
    window.show()
    return app.exec()
