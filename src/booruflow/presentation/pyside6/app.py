"""PySide6 application bootstrap."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication

from booruflow import startup_profile
from booruflow.application.capabilities import ApplicationCapabilities
from booruflow.application.database_paths import (
    gelbooru_alias_database,
    gelbooru_tag_database,
    migrate_database_settings,
)
from booruflow.application.hydra_model_manager import hydra_directory, migrated_hydra_settings
from booruflow.application.portable_settings import PortableSettingsRepository
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.settings import JsonSettingsRepository, migrate_blacklist_setting
from booruflow.infrastructure.task_repository import JsonTaskRepository
from booruflow.presentation.pyside6.main_window import MainWindow
from booruflow.runtime import application_root, bundled_bootstrap_root, resource_root


def project_root() -> Path:
    return application_root()


def initial_settings(root: Path) -> dict[str, object]:
    return {
        "language": "en",
        "first_run_wizard_completed": False,
        "gelbooru_tag_database": str(root / "data" / "databases" / "gelbooru_tags.db"),
        "gelbooru_alias_database": str(root / "data" / "databases" / "gelbooru_aliases.db"),
        "e621_database": str(root / "data" / "databases" / "e621_tags.db"),
        "blacklist_file": "",
        "output_root": str(root / "var" / "results"),
        "folder_artists_directory": "",
        "everything_executable": "",
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
    startup_profile.checkpoint("Bootstrap/imports")
    started = startup_profile.now_ns()
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    startup_profile.duration("QApplication creation", started)
    startup_profile.checkpoint("QApplication creation + diagnostics")
    if diagnostics is not None:
        diagnostics.install_qt_message_handler()
    QCoreApplication.setOrganizationName("BooruFlow")
    QCoreApplication.setApplicationName("BooruFlow")
    root = project_root()
    config = root / "config"
    settings_repository = (
        PortableSettingsRepository(config / "booruflow_settings.json", root)
        if getattr(sys, "frozen", False)
        else JsonSettingsRepository(config / "booruflow_settings.json")
    )
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
    startup_profile.checkpoint("Configuration loading/migration")
    alias_database = gelbooru_alias_database(settings)
    bundled = bundled_bootstrap_root()
    alias_archive = bundled / "bootstrap" / "gelbooru-aliases.zip" if bundled else None
    if (alias_database is not None and not alias_database.exists()
            and not (alias_archive and alias_archive.is_file())):
        from booruflow.infrastructure.gelbooru_aliases import migrate_alias_catalog

        migrate_alias_catalog(gelbooru_tag_database(settings), alias_database)
    startup_profile.checkpoint("Database paths/alias migration")
    from booruflow.infrastructure.grabber import grabber_availability

    capabilities = ApplicationCapabilities(
        grabber=grabber_availability(settings.get("grabber_executable", ""))
    )
    startup_profile.checkpoint("Capabilities/services bootstrap")
    catalog = LanguageCatalog(
        resource_root() / "resources" / "i18n",
        str(settings.get("language", "en")),
    )
    startup_profile.checkpoint("i18n catalog loading")
    window_started = startup_profile.now_ns()
    window = MainWindow(
        capabilities,
        catalog,
        settings_repository=settings_repository,
        credentials_repository=credentials_repository,
        task_repository=task_repository,
        project_root=root,
        resource_root=resource_root(),
        python_executable=sys.executable,
        start_image_worker=False,
    )
    startup_profile.duration("MainWindow total", window_started)
    if diagnostics is not None:
        diagnostics.set_logger(window.log_threadsafe)
    return app, window


def run(argv: list[str] | None = None, diagnostics=None) -> int:
    app, window = create_application(argv, diagnostics)
    wait_for_worker = os.environ.get("BOORUFLOW_STARTUP_PROFILE_WAIT_WORKER", "") == "1"
    profile_feature = os.environ.get("BOORUFLOW_STARTUP_PROFILE_FEATURE", "").strip()
    feature_started = False
    feature_started_ns = 0

    if startup_profile.enabled() and wait_for_worker:
        def worker_profile_state(key: str, state: str, _detail: str) -> None:
            nonlocal feature_started, feature_started_ns
            if key != "image_analysis":
                return
            startup_profile.write(visible=window.isVisible())
            if state in {"ready", "failed"}:
                if state == "ready" and profile_feature and not feature_started:
                    feature_started = True
                    feature_started_ns = startup_profile.now_ns()
                    if profile_feature in window.NAVIGATION_KEYS:
                        window.navigate_to_key(profile_feature)
                    else:
                        window.feature_lifecycle.request(profile_feature)
                    return
                window.close()

        window.feature_lifecycle.state_changed.connect(worker_profile_state)

    if startup_profile.enabled() and profile_feature:
        def profiled_feature_state(key: str, state: str, _detail: str) -> None:
            if key != profile_feature or not feature_started:
                return
            if state == "ready":
                startup_profile.duration(
                    f"Feature first access: {profile_feature}", feature_started_ns
                )
                second_started = startup_profile.now_ns()
                if profile_feature in window.NAVIGATION_KEYS:
                    window.navigate_to_key("home")
                    window.navigate_to_key(profile_feature)
                else:
                    window.feature_lifecycle.request(profile_feature)
                startup_profile.duration(
                    f"Feature second access: {profile_feature}", second_started
                )
                startup_profile.write(visible=window.isVisible())
                window.close()
            elif state == "failed":
                startup_profile.write(visible=window.isVisible())
                window.close()

        window.feature_lifecycle.state_changed.connect(profiled_feature_state)

    show_started = startup_profile.now_ns()
    window.show()
    startup_profile.duration("show()", show_started)
    startup_profile.checkpoint("show()")
    def startup_visible_probe() -> None:
        startup_profile.event("First event-loop callback")

        def await_exposed() -> None:
            nonlocal feature_started, feature_started_ns
            handle = window.windowHandle()
            exposed = bool(window.isVisible() and handle is not None and handle.isExposed())
            if not exposed:
                QTimer.singleShot(10, await_exposed)
                return
            startup_profile.event("Window visible/exposed")
            startup_profile.event("Dashboard interactive")
            if not window._settings.get("first_run_wizard_completed") and not startup_profile.enabled():
                window.open_first_run_wizard()
            if not (startup_profile.enabled() and os.environ.get("BOORUFLOW_STARTUP_PROFILE_EXIT") == "1"):
                if not window.start_bundled_bootstrap(window.complete_deferred_startup):
                    window.complete_deferred_startup()
            else:
                window.complete_deferred_startup()
            if profile_feature and not wait_for_worker and not feature_started:
                feature_started = True
                feature_started_ns = startup_profile.now_ns()
                if profile_feature in window.NAVIGATION_KEYS:
                    window.navigate_to_key(profile_feature)
                else:
                    window.feature_lifecycle.request(profile_feature)
            if startup_profile.enabled():
                startup_profile.write(visible=True)
                if (
                    os.environ.get("BOORUFLOW_STARTUP_PROFILE_EXIT", "") == "1"
                    and not wait_for_worker
                    and not profile_feature
                ):
                    window.close()

        await_exposed()

    QTimer.singleShot(0, startup_visible_probe)
    return app.exec()
