"""Main window, localization and controllers for BooruFlow."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import QProcess, QSize, Qt, QThread, Signal
from PySide6.QtGui import QBrush, QFontMetrics, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from booruflow import startup_profile
from booruflow.application.capabilities import ApplicationCapabilities
from booruflow.application.database_paths import gelbooru_alias_database, gelbooru_tag_database
from booruflow.application.ports import SettingsRepository
from booruflow.application.tasks import MemoryTaskRepository, TaskRepository
from booruflow.application.taxonomy import TaxonomyRepository
from booruflow.infrastructure.browser_launcher import BrowserLauncher
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.feature_lifecycle import (
    FeatureLifecycle,
    FeaturePolicy,
    FeatureState,
)
from booruflow.presentation.pyside6.feature_loading import FeaturePageHost
from booruflow.presentation.pyside6.icons import navigation_icon
from booruflow.presentation.pyside6.pages import DashboardPage, ScrollablePageHost
from booruflow.presentation.pyside6.status_bar import StatusBarController
from booruflow.presentation.pyside6.task_manager import TaskManager
from booruflow.presentation.pyside6.ui_logging import RunLog


class _InternalBooruLauncher:
    def __init__(self, window) -> None:
        self.window = window

    def open(self, url: str) -> bool:
        self.window._open_internal_booru_url(url)
        return True


class MainWindow(QMainWindow):
    diagnostic_log_requested = Signal(str)
    NAVIGATION_KEYS = (
        "home",
        "review",
        "tagging",
        "image_analysis",
        "image_finder",
        "auto_organize",
        "folder_artists",
        "similar_artists",
        "organization",
        "tag_browser",
        "wiki_audit",
        "wiki",
        "cleanup",
        "options",
        "grabber",
        "tasks",
    )
    VISIBLE_NAVIGATION = (
        ("page", "home"),
        ("group", "main"),
        ("page", "tagging"),
        ("page", "image_finder"),
        ("page", "similar_artists"),
        ("group", "wiki_tags"),
        ("page", "organization"),
        ("page", "tag_browser"),
        ("page", "wiki_audit"),
        ("page", "wiki"),
        ("group", "local_tools"),
        ("page", "grabber"),
        ("page", "auto_organize"),
        ("page", "folder_artists"),
        ("page", "cleanup"),
        ("group", "settings"),
        ("page", "options"),
    )
    LEGACY_NAVIGATION_ALIASES: ClassVar[dict[str, str]] = {"review": "grabber"}

    def __init__(
        self,
        capabilities: ApplicationCapabilities,
        catalog: LanguageCatalog,
        parent: QWidget | None = None,
        settings_repository: SettingsRepository | None = None,
        credentials_repository: SettingsRepository | None = None,
        task_repository: TaskRepository | None = None,
        project_root: Path | None = None,
        python_executable: str | None = None,
        start_image_worker: bool = True,
    ) -> None:
        main_window_started = startup_profile.now_ns()
        super().__init__(parent)
        self._early_logs: list[str] = []
        self._log_history: list[str] = []
        self._show_debug_logs = False
        self._embedded_form_diagnostic_active = False
        self._embedded_http_diagnostic_active = False
        self._pending_organization_tag: str | None = None
        self._deferred_startup_complete = False
        self._organization_loaded = False
        self._ready_callbacks: dict[str, list] = {}
        self.capabilities = capabilities
        self.catalog = catalog
        self.settings_repository = settings_repository
        self.credentials_repository = credentials_repository
        self.task_manager = TaskManager(task_repository or MemoryTaskRepository(), self)
        self.project_root = project_root or Path.cwd()
        self._run_log = RunLog.create(self.project_root)
        self.disk_log_path = self._run_log.path
        self.python_executable = python_executable or sys.executable
        self.taxonomy_repository = TaxonomyRepository(
            self.project_root / "data" / "taxonomy" / "tag_organization.json",
            self.project_root / "data" / "databases",
        )
        startup_profile.checkpoint("MainWindow base/task/log/taxonomy repositories")
        settings = settings_repository.load() if settings_repository else {}
        self._settings = dict(settings)
        self.browser_launcher = BrowserLauncher(self.project_root / "var", settings)
        self.gelbooru_session_factory = None
        startup_profile.checkpoint("Browser launcher/session service")
        self.embedded_gelbooru_profile = None
        self.embedded_gelbooru_bridge = None
        self.embedded_gelbooru_session_factory = None
        startup_profile.checkpoint("Embedded QtWebEngine profile deferred")
        self.gelbooru_session_dialog = None
        self.internal_browser_launcher = _InternalBooruLauncher(self)
        self.publish_backend = str(settings.get("gelbooru_publish_backend", "embedded"))
        self.options_session_test_worker = None
        startup_profile.checkpoint("Embedded bridge/settings/credentials")
        self.resize(1120, 760)
        self.setMinimumSize(860, 600)

        self.navigation = QListWidget()
        self.navigation.setIconSize(QSize(30, 30))
        self.navigation.setSpacing(2)
        for kind, key in self.VISIBLE_NAVIGATION:
            if kind == "group":
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole + 1, key)
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setSizeHint(QSize(0, 24))
                font = self.navigation.font()
                font.setPointSize(max(8, font.pointSize() - 1))
                font.setBold(True)
                item.setFont(font)
                item.setForeground(
                    QBrush(self.navigation.palette().color(QPalette.ColorRole.PlaceholderText))
                )
                item.setBackground(
                    QBrush(self.navigation.palette().color(QPalette.ColorRole.Window))
                )
            else:
                item = QListWidgetItem(navigation_icon(key), "")
                item.setData(Qt.ItemDataRole.UserRole, key)
                # Keep the supported 720 px window usable with all group labels visible.
                item.setSizeHint(QSize(0, 38))
            self.navigation.addItem(item)

        self.pages = QStackedWidget()
        self.content_pages: list[QWidget | None] = [None] * len(self.NAVIGATION_KEYS)
        self.page_hosts: dict[str, FeaturePageHost] = {}
        self.feature_lifecycle = FeatureLifecycle(self)
        self.feature_lifecycle.state_changed.connect(self._feature_state_changed)

        self.dashboard_page = DashboardPage(catalog, capabilities.grabber)
        self.dashboard_page.navigate_requested.connect(self.navigate_to_key)
        self.content_pages[0] = self.dashboard_page
        self.pages.addWidget(ScrollablePageHost(self.dashboard_page))
        startup_profile.checkpoint("Page: Dashboard")
        for key in self.NAVIGATION_KEYS[1:]:
            host = FeaturePageHost(None, self.catalog, f"nav.{key}")
            host.retry_requested.connect(
                lambda key=key: self.feature_lifecycle.retry(key)
            )
            self.page_hosts[key] = host
            self.pages.addWidget(host)

        for attribute in (
            "review_page", "review_coordinator", "review_controller",
            "tagging_page", "tagging_controller", "image_analysis_page",
            "image_analysis_controller", "image_finder_page", "image_finder_controller",
            "auto_organize_page", "auto_organize_controller",
            "folder_artists_page", "folder_artists_controller", "similar_artists_page",
            "similar_artists_controller", "organization_page", "organization_coordinator",
            "tag_browser_page", "wiki_audit_page", "wiki_page", "cleanup_page",
            "cleanup_controller",
            "options_page", "options_maintenance_controller", "database_controller",
            "credential_validation_controller",
            "grabber_tools_page", "grabber_page", "grabber_controller", "task_page",
            "hydra_model_process",
            "database_process", "grabber_process",
        ):
            setattr(self, attribute, None)

        self.feature_lifecycle.register("home", FeaturePolicy.EAGER, self._noop_prepare, ready=True)
        self.feature_lifecycle.register(
            "review", FeaturePolicy.LAZY, self._noop_prepare, dependencies=("grabber",)
        )
        self._register_feature("tagging", FeaturePolicy.LAZY, self._build_tagging)
        self.feature_lifecycle.register(
            "image_analysis", FeaturePolicy.WARMUP, self._prepare_image_analysis
        )
        self._register_feature("image_finder", FeaturePolicy.LAZY, self._build_image_finder)
        self._register_feature("auto_organize", FeaturePolicy.LAZY, self._build_auto_organize)
        self._register_feature("folder_artists", FeaturePolicy.LAZY, self._build_folder_artists)
        self.feature_lifecycle.register(
            "similar_artists",
            FeaturePolicy.LAZY,
            self._prepare_similar_artists,
            dependencies=("image_analysis",),
        )
        self.feature_lifecycle.register(
            "organization", FeaturePolicy.WARMUP, self._prepare_organization
        )
        self._register_feature("tag_browser", FeaturePolicy.LAZY, self._build_tag_browser)
        self._register_feature("wiki_audit", FeaturePolicy.LAZY, self._build_wiki_audit)
        self._register_feature("wiki", FeaturePolicy.LAZY, self._build_wiki)
        self.feature_lifecycle.register(
            "cleanup", FeaturePolicy.LAZY, self._prepare_cleanup
        )
        self._register_feature("options", FeaturePolicy.LAZY, self._build_options)
        self._register_feature("grabber", FeaturePolicy.LAZY, self._build_grabber)
        self._register_feature("tasks", FeaturePolicy.LAZY, self._build_tasks)
        self.feature_lifecycle.register(
            "embedded_webengine", FeaturePolicy.LAZY,
            self._prepare_embedded_webengine,
        )
        self._image_analysis_feature_done = None
        self._image_analysis_feature_failed = None
        startup_profile.event("Pages constructed before Dashboard READY: 1")

        workspace = QWidget()
        workspace_layout = QHBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(self.navigation)
        workspace_layout.addWidget(self.pages, 1)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2_000)
        self.log_view.setMinimumHeight(120)
        self.log_view.setMaximumHeight(190)
        self.log_view.hide()
        for early_message in self._early_logs:
            if self._show_debug_logs or "[DEBUG]" not in early_message:
                self.log_view.appendPlainText(early_message)
        self._early_logs.clear()
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(8, 8, 8, 4)
        central_layout.setSpacing(6)
        central_layout.addWidget(workspace, 1)
        central_layout.addWidget(self.log_view)
        self.setCentralWidget(central)

        status_bar = QStatusBar()
        status_bar.setContentsMargins(8, 3, 8, 3)
        self.setStatusBar(status_bar)
        self.status_controller = StatusBarController(
            status_bar, self.catalog, self.log, self
        )
        # Compatibility aliases for existing shell integrations and tests.
        self.status_label = self.status_controller.page_label
        self.status_message_label = self.status_controller.message_label
        self.global_progress = self.status_controller.progress
        self.log_button = QPushButton()
        self.log_button.clicked.connect(self.toggle_log)
        self.debug_log_toggle = QCheckBox()
        self.debug_log_toggle.setChecked(False)
        self.debug_log_toggle.toggled.connect(self._set_debug_logs_visible)
        self.clear_log_button = QPushButton()
        self.clear_log_button.clicked.connect(self._clear_visible_logs)
        status_bar.addPermanentWidget(self.debug_log_toggle)
        status_bar.addPermanentWidget(self.log_button)
        status_bar.addPermanentWidget(self.clear_log_button)
        self.diagnostic_log_requested.connect(
            self.log, Qt.ConnectionType.QueuedConnection
        )

        self.navigation.currentRowChanged.connect(self._navigation_changed)
        self.navigation.setCurrentRow(0)
        self.retranslate()
        startup_profile.checkpoint("MainWindow layout/signals/retranslate")
        self.log(self.catalog.text("log.started"))
        if not capabilities.grabber.available:
            self.log(
                self.catalog.text("log.grabber_unavailable", reason=capabilities.grabber.reason)
            )
        startup_profile.duration("MainWindow constructor", main_window_started)

    @staticmethod
    def _noop_prepare(completed, _failed) -> None:
        completed()

    def _register_feature(self, key: str, policy: FeaturePolicy, builder) -> None:
        self.feature_lifecycle.register(
            key,
            policy,
            lambda completed, failed, key=key, builder=builder: self._construct_feature(
                key, builder, completed, failed
            ),
        )

    def _construct_feature(self, key: str, builder, completed, failed) -> None:
        if self.content_pages[self.NAVIGATION_KEYS.index(key)] is not None:
            completed()
            return
        started = startup_profile.now_ns()
        try:
            self._build_and_install_feature(key, builder)
        except Exception as exc:  # noqa: BLE001 - feature construction boundary
            failed(exc)
            return
        startup_profile.duration(f"Page/controller lazy construction: {key}", started)
        completed()

    def _build_and_install_feature(self, key: str, builder) -> QWidget:
        children_before = set(self.children())
        try:
            page = builder()
            self._install_feature_page(key, page)
            return page
        except Exception:
            for child in set(self.children()) - children_before:
                child.deleteLater()
            self._reset_feature_attributes(key)
            raise

    def _reset_feature_attributes(self, key: str) -> None:
        attributes = {
            "review": (),
            "tagging": ("tagging_page", "tagging_controller"),
            "image_analysis": ("image_analysis_page", "image_analysis_controller"),
            "image_finder": ("image_finder_page", "image_finder_controller"),
            "auto_organize": ("auto_organize_page", "auto_organize_controller"),
            "folder_artists": ("folder_artists_page", "folder_artists_controller"),
            "similar_artists": ("similar_artists_page", "similar_artists_controller"),
            "organization": ("organization_page", "organization_coordinator"),
            "tag_browser": ("tag_browser_page",),
            "wiki_audit": ("wiki_audit_page",),
            "wiki": ("wiki_page",),
            "cleanup": ("cleanup_page", "cleanup_controller"),
            "options": (
                "options_page", "database_controller", "database_process",
                "credential_validation_controller", "options_maintenance_controller",
                "hydra_model_process",
            ),
            "grabber": (
                "grabber_tools_page", "review_page", "review_coordinator", "review_controller",
                "grabber_page", "grabber_controller", "grabber_process",
            ),
            "tasks": ("task_page",),
        }
        for attribute in attributes.get(key, ()):
            setattr(self, attribute, None)

    def _install_feature_page(self, key: str, page: QWidget) -> None:
        index = self.NAVIGATION_KEYS.index(key)
        self.content_pages[index] = page
        self.page_hosts[key].set_page(page)
        if hasattr(page, "retranslate"):
            page.retranslate()

    @property
    def constructed_page_keys(self) -> tuple[str, ...]:
        return tuple(
            key for key, page in zip(self.NAVIGATION_KEYS, self.content_pages, strict=True)
            if page is not None
        )

    def _build_review(self):
        from booruflow.presentation.pyside6.review_controller import ReviewCoordinator
        from booruflow.presentation.pyside6.review_page import ReviewPage

        page = ReviewPage(self.catalog, self._settings)
        coordinator = ReviewCoordinator(
            self.project_root, self.python_executable, self.catalog, page,
            self.settings_repository, self._credentials, self.log, self.task_manager, self,
        )
        page.start_requested.connect(coordinator.start)
        page.stop_requested.connect(coordinator.stop)
        page.count_requested.connect(coordinator.count)
        page.autocomplete_requested.connect(coordinator.autocomplete)
        page.grabber_tags_requested.connect(self._review_results_to_grabber)
        self.review_page = page
        self.review_coordinator = coordinator
        self.review_controller = coordinator.process_controller
        return page

    def _build_tagging(self):
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage
        from booruflow.presentation.pyside6.thumbnail_cache import ThumbnailMemoryCache

        if not hasattr(self, "_tagging_thumbnail_cache"):
            self._tagging_thumbnail_cache = ThumbnailMemoryCache()
        page = TaggingPage(
            self.catalog, self._settings, self.internal_browser_launcher,
            thumbnail_cache=self._tagging_thumbnail_cache,
        )
        self.status_controller.bind(page.page_status)
        controller = TaggingController(
            self.catalog, page, self._credentials, self.log, self.task_manager, self,
            project_root=self.project_root, settings=self._settings,
            publisher_factory=self._build_tagging_publisher,
            session_factory_provider=self._active_gelbooru_session_factory,
            publication_backend_provider=lambda: self.publish_backend,
            e621_validation_factory=self._build_e621_validation_client,
            diagnostic_mode_provider=self._embedded_form_diagnostic_enabled,
            http_diagnostic_mode_provider=self._embedded_http_diagnostic_enabled,
            publication_prepare=self._prepare_publication_backend,
        )
        page.start_requested.connect(controller.start)
        page.stop_requested.connect(controller.stop)
        page.search_settings_saved.connect(self._save_tagging_settings)
        if self.image_analysis_controller is not None:
            controller.bind_image_analysis(self.image_analysis_controller)
        if self.database_controller is not None:
            self.database_controller.alias_page = self.options_page
        self.tagging_page = page
        self.tagging_controller = controller
        return page

    def _build_image_finder(self):
        from booruflow.presentation.pyside6.image_finder_controller import ImageFinderController
        from booruflow.presentation.pyside6.image_finder_page import ImageFinderPage

        credentials = self._credentials()
        page = ImageFinderPage(
            self.catalog, connected=bool(str(credentials.get("pixiv_refresh_token", "")).strip())
        )
        self.status_controller.bind(page.page_status)
        controller = ImageFinderController(
            page, self.credentials_repository, self.log, self
        )
        self.image_finder_page = page
        self.image_finder_controller = controller
        return page

    def _build_image_analysis(self):
        from booruflow.presentation.pyside6.image_analysis_controller import (
            ImageAnalysisController,
        )
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage

        page = ImageAnalysisPage(self.catalog)
        controller = ImageAnalysisController(
            self.project_root, self.python_executable, page, self._settings,
            self._credentials, self.log, False, self,
        )
        controller.worker_state_changed.connect(self._image_analysis_feature_state_changed)
        if self.options_page is not None:
            controller.bind_installation_page(self.options_page)
        if self.tagging_controller is not None:
            self.tagging_controller.bind_image_analysis(controller)
        self.image_analysis_page = page
        self.image_analysis_controller = controller
        return page

    def _install_image_analysis_from_options(self, operation: str) -> None:
        if self.image_analysis_controller is None or self.options_page is None:
            return
        self.image_analysis_controller.bind_installation_page(self.options_page)
        if operation == "runtime":
            self.image_analysis_controller.install_gpu_runtime(parent=self.options_page)
        else:
            self.image_analysis_controller.install_wd14(parent=self.options_page)

    def _build_auto_organize(self):
        from booruflow.presentation.pyside6.auto_organize_controller import (
            AutoOrganizeController,
        )
        from booruflow.presentation.pyside6.auto_organize_page import AutoOrganizePage

        page = AutoOrganizePage(self.catalog)
        controller = AutoOrganizeController(
            self.project_root, page, self.log, self,
            gelbooru_tag_database(self._settings), credential_provider=self._credentials,
        )
        page.analyze_requested.connect(controller.analyze)
        page.stop_requested.connect(controller.stop)
        page.execute_requested.connect(controller.execute)
        self.auto_organize_page = page
        self.auto_organize_controller = controller
        return page

    def _build_folder_artists(self):
        from booruflow.presentation.pyside6.folder_artists_controller import (
            FolderArtistsController,
        )
        from booruflow.presentation.pyside6.folder_artists_page import FolderArtistsPage

        page = FolderArtistsPage(
            self.catalog, str(self._settings.get("folder_artists_directory", ""))
        )
        controller = FolderArtistsController(
            self.catalog, page, self.settings_repository, self.log, self
        )
        page.analyze_requested.connect(controller.start)
        page.stop_requested.connect(controller.stop)
        page.open_requested.connect(controller.open_artist)
        page.folder_changed.connect(controller.folder_changed)
        self.folder_artists_page = page
        self.folder_artists_controller = controller
        return page

    def _build_similar_artists(self):
        from booruflow.presentation.pyside6.similar_artists_controller import (
            SimilarArtistsController,
        )
        from booruflow.presentation.pyside6.similar_artists_page import SimilarArtistsPage

        if self.image_analysis_controller is None:
            raise RuntimeError("Image Analysis must be prepared before Similar Artists")
        page = SimilarArtistsPage(self.catalog)
        controller = SimilarArtistsController(
            self.project_root, page, self.image_analysis_controller, self.log,
            self.task_manager, self, browser_launcher=self.internal_browser_launcher,
        )
        self.similar_artists_page = page
        self.similar_artists_controller = controller
        return page

    def _build_organization(self):
        from booruflow.application.taxonomy import default_document
        from booruflow.presentation.pyside6.organization_controller import (
            OrganizationCoordinator,
        )
        from booruflow.presentation.pyside6.organization_page import OrganizationPage

        page = OrganizationPage(
            self.catalog, default_document(), self.internal_browser_launcher
        )
        coordinator = OrganizationCoordinator(
            self.project_root, self.catalog, page, self.taxonomy_repository,
            self.settings_repository, self._credentials, self.log, self.task_manager, self,
        )
        page.save_requested.connect(coordinator.save)
        page.update_requested.connect(coordinator.update)
        page.stop_requested.connect(coordinator.stop_update)
        page.review_tags_requested.connect(self._review_organization_tags)
        page.tag_details_requested.connect(coordinator.load_details)
        page.wiki_refresh_requested.connect(coordinator.refresh_details)
        page.wiki_draft_requested.connect(self._prepare_wiki)
        coordinator.preview_ready.connect(self._confirm_taxonomy_update)
        self.status_controller.bind(page.page_status)
        self.organization_page = page
        self.organization_coordinator = coordinator
        return page

    def _build_tag_browser(self):
        from booruflow.presentation.pyside6.tag_browser_page import TagBrowserPage

        database = gelbooru_tag_database(self._settings)
        e621 = str(self._settings.get("e621_database", ""))
        page = TagBrowserPage(
            self.catalog,
            {"gelbooru": database, "e621": Path(e621) if e621 else None},
            {"gelbooru": gelbooru_alias_database(self._settings), "e621": None},
        )
        self.status_controller.bind(page.page_status)
        if self.database_controller is not None:
            self.database_controller.tag_browser_page = page
        self.tag_browser_page = page
        return page

    def _build_wiki(self):
        from booruflow.presentation.pyside6.wiki_page import WikiPage

        page = WikiPage(
            self.catalog, self.project_root / "var" / "wiki_drafts",
            gelbooru_tag_database(self._settings), self.settings_repository,
            self.internal_browser_launcher,
        )
        self.status_controller.bind(page.page_status)
        page.organization_tag_requested.connect(self._open_organization_tag)
        self.wiki_page = page
        return page

    def _build_wiki_audit(self):
        from booruflow.presentation.pyside6.wiki_audit_page import WikiAuditPage

        page = WikiAuditPage(
            self.catalog,
            gelbooru_tag_database(self._settings),
            gelbooru_alias_database(self._settings),
            self.taxonomy_repository.database_path("gelbooru"),
            self.internal_browser_launcher,
            self._credentials,
            self.log,
        )
        self.wiki_audit_page = page
        return page

    def _build_cleanup(self):
        from booruflow.presentation.pyside6.cleanup_controller import CleanupController
        from booruflow.presentation.pyside6.cleanup_page import CleanupPage

        page = CleanupPage(self.catalog, self._settings, self.project_root)
        controller = CleanupController(
            self.project_root, self.catalog, page, self.settings_repository,
            self.log, self.task_manager, self,
        )
        page.scan_requested.connect(controller.start)
        page.stop_requested.connect(controller.stop)
        page.recycle_requested.connect(self._recycle_cleanup)
        page.blacklist_changed.connect(self._save_cleanup_blacklist)
        self.cleanup_page = page
        self.cleanup_controller = controller
        return page

    def _build_options(self):
        from booruflow.presentation.pyside6.credential_validation_controller import (
            CredentialValidationController,
        )
        from booruflow.presentation.pyside6.database_update_controller import (
            DatabaseUpdateController,
        )
        from booruflow.presentation.pyside6.options_maintenance_controller import (
            OptionsMaintenanceController,
        )
        from booruflow.presentation.pyside6.options_page import OptionsPage

        page = OptionsPage(
            self.catalog, self._settings, self._credentials(), self.project_root
        )
        self.status_controller.bind(page.page_status)
        page.save_requested.connect(self._save_options)
        page.language_changed.connect(self.change_language)
        database = DatabaseUpdateController(
            self.project_root, self.python_executable, self.catalog, page,
            self.tag_browser_page, self._credentials, self.log, self.task_manager, self,
            alias_page=page,
            database_activated=self._activate_database_path,
        )
        page.database_update_requested.connect(database.start)
        page.database_stop_requested.connect(database.stop)
        page.alias_update_requested.connect(database.start_aliases)
        credential = CredentialValidationController(page, log=self.log, parent=self)
        page.credentials_test_requested.connect(credential.start)
        page.browser_test_requested.connect(self._test_browser)
        page.browser_reset_requested.connect(self._reset_browser_profile)
        page.publication_backend_changed.connect(self._select_publish_backend)
        page.embedded_session_open_requested.connect(self._open_gelbooru_session)
        page.embedded_session_test_requested.connect(self._test_gelbooru_session)
        page.embedded_session_reset_requested.connect(self._reset_embedded_session)
        page.gpu_runtime_install_requested.connect(
            lambda: self._run_when_ready(
                "image_analysis", lambda: self._install_image_analysis_from_options("runtime")
            )
        )
        page.wd14_install_requested.connect(
            lambda: self._run_when_ready(
                "image_analysis", lambda: self._install_image_analysis_from_options("model")
            )
        )
        maintenance = OptionsMaintenanceController(
            self.project_root, self.catalog, page, self.log, self
        )
        page.storage_refresh_requested.connect(maintenance.refresh_storage)
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(self._hydra_operation_finished)
        page.hydra_install_requested.connect(lambda: self._start_hydra_operation("install"))
        page.hydra_migrate_requested.connect(lambda: self._start_hydra_operation("migrate"))
        page.hydra_remove_requested.connect(self._remove_hydra)
        self.options_page = page
        self.options_maintenance_controller = maintenance
        self.hydra_model_process = process
        self.database_controller = database
        self.database_process = database.process
        self.credential_validation_controller = credential
        maintenance.refresh_storage()
        if self.image_analysis_controller is not None:
            self.image_analysis_controller.bind_installation_page(page)
        return page

    def _build_grabber(self):
        from booruflow.presentation.pyside6.grabber_controller import GrabberController
        from booruflow.presentation.pyside6.grabber_page import GrabberPage
        from booruflow.presentation.pyside6.grabber_tools_page import GrabberToolsPage

        review_page = self._build_review()
        page = GrabberPage(self.catalog, self._settings, self.capabilities.grabber.available)
        controller = GrabberController(
            self.catalog, page, self.settings_repository, self._credentials,
            self.log, self.task_manager, self,
        )
        page.create_requested.connect(controller.create)
        page.load_requested.connect(controller.load)
        page.launch_requested.connect(controller.launch)
        page.previous_requested.connect(controller.previous)
        controller.load(silent=True)
        self.grabber_page = page
        self.grabber_controller = controller
        self.grabber_process = controller.process
        tools_page = GrabberToolsPage(self.catalog, review_page, page)
        self.grabber_tools_page = tools_page
        return tools_page

    def _build_tasks(self):
        from booruflow.presentation.pyside6.task_page import TaskPage

        page = TaskPage(self.catalog, self.task_manager)
        self.task_page = page
        return page

    def _log_gelbooru_database_diagnostic(self, settings: dict[str, object]) -> None:
        """Log the configured catalogue pair once, without probing neighbouring files."""
        from booruflow.infrastructure.gelbooru_aliases import inspect_alias_catalog

        tag_database = gelbooru_tag_database(settings)
        alias_status = inspect_alias_catalog(gelbooru_alias_database(settings))
        self.log(
            "Gelbooru databases: "
            f"tags={tag_database if tag_database is not None else '<none>'} "
            f"aliases={alias_status.path if alias_status.path is not None else '<none>'} "
            f"aliases_available={str(alias_status.available).lower()} "
            f"active_aliases={alias_status.active_aliases if alias_status.active_aliases is not None else 0}"
        )
        if not alias_status.available:
            self.log(
                "Alias catalogue unavailable: "
                f"configured_path={alias_status.path if alias_status.path is not None else '<none>'}; "
                f"reason={alias_status.reason}; literal fallback enabled"
            )

    def navigate_to(self, index: int) -> None:
        if 0 <= index < self.pages.count():
            self.navigate_to_key(self.NAVIGATION_KEYS[index])

    def navigate_to_key(self, key: str) -> None:
        if key not in self.NAVIGATION_KEYS:
            return
        route_key = self.LEGACY_NAVIGATION_ALIASES.get(key, key)
        row = self._visible_navigation_row(route_key)
        if row >= 0:
            if self.navigation.currentRow() == row:
                self._show_page(route_key)
            else:
                self.navigation.setCurrentRow(row)
        else:
            # Legacy pages remain addressable by code without reappearing in primary navigation.
            self._show_page(route_key)
        if key == "review":
            self.feature_lifecycle.request("review")
            self._run_when_ready("grabber", self._show_grabber_builder)

    def _visible_navigation_row(self, key: str) -> int:
        for row in range(self.navigation.count()):
            if self.navigation.item(row).data(Qt.ItemDataRole.UserRole) == key:
                return row
        return -1

    def _show_page(self, key: str) -> None:
        lifecycle_state = self.feature_lifecycle.state(key)
        host = self.page_hosts.get(key)
        if host is not None and lifecycle_state is not FeatureState.READY:
            # Activate the existing loading/failure surface synchronously,
            # before this host is exposed and before lifecycle queueing.
            host.set_feature_state(lifecycle_state, self.feature_lifecycle.error(key))
        self.pages.setCurrentIndex(self.NAVIGATION_KEYS.index(key))
        if lifecycle_state is not FeatureState.READY:
            status_state = (
                "failed" if lifecycle_state is FeatureState.FAILED else "loading"
            )
            self.status_controller.set_page_state(key, status_state)
        self.feature_lifecycle.request(key)
        if self.image_analysis_controller is not None:
            self.image_analysis_controller.set_page_active(key == "image_analysis")
        self._update_status(key)

    def _show_grabber_builder(self) -> None:
        if self.grabber_tools_page is not None:
            self.grabber_tools_page.show_builder()

    def _show_grabber_launcher(self) -> None:
        if self.grabber_tools_page is not None:
            self.grabber_tools_page.show_launcher()

    def _prepare_wiki(self, tag: str) -> None:
        self.navigate_to_key("wiki")
        self._run_when_ready("wiki", lambda: self.wiki_page.set_tag(tag))

    def _open_organization_tag(self, tag: str) -> None:
        if self.feature_lifecycle.state("organization") is FeatureState.READY:
            self.organization_page._navigate_to_tag(tag)
            self.navigate_to_key("organization")
            return
        self._pending_organization_tag = tag
        self.navigate_to_key("organization")

    def _navigation_changed(self, index: int) -> None:
        if index < 0:
            return
        key = self.navigation.item(index).data(Qt.ItemDataRole.UserRole)
        if key:
            self._show_page(str(key))

    def _ensure_organization_loaded(self) -> None:
        self.feature_lifecycle.request("organization")

    def _load_organization(self) -> None:
        if self.organization_page is None:
            raise RuntimeError("Organization page is not constructed")
        started = startup_profile.now_ns()
        self.organization_page.document = self.taxonomy_repository.load()
        self.organization_page.reload(False)
        self._organization_loaded = True
        startup_profile.duration("Taxonomy lazy load", started)

    def complete_deferred_startup(self) -> None:
        """Begin serialized opportunistic warmups after the first exposed window."""
        if self._deferred_startup_complete:
            return
        self._deferred_startup_complete = True
        if (
            self._image_analysis_feature_done is not None
            and self.image_analysis_controller is not None
        ):
            self.image_analysis_controller.start_worker()
        self.feature_lifecycle.start_warmups()

    def _prepare_organization(self, completed, failed) -> None:
        try:
            if self.organization_page is None:
                self._build_and_install_feature("organization", self._build_organization)
            self._load_organization()
        except Exception as exc:  # noqa: BLE001 - feature boundary
            failed(exc)
        else:
            completed()

    def _prepare_cleanup(self, completed, failed) -> None:
        try:
            if self.cleanup_page is None:
                self._build_and_install_feature("cleanup", self._build_cleanup)
        except Exception as exc:  # noqa: BLE001 - feature boundary
            failed(exc)
        else:
            completed()

    def _prepare_similar_artists(self, completed, failed) -> None:
        try:
            if self.similar_artists_controller is None:
                self._build_and_install_feature(
                    "similar_artists", self._build_similar_artists
                )
        except Exception as exc:  # noqa: BLE001 - feature construction boundary
            failed(exc)
            return
        assert self.similar_artists_controller is not None
        assert self.image_analysis_controller is not None

        def load_catalog() -> None:
            self.similar_artists_controller.activate_async(completed, failed)

        if self.similar_artists_controller.maintenance_required():
            self.image_analysis_controller.run_exclusive_database_operation(
                self.similar_artists_controller.run_feature_maintenance,
                load_catalog,
                failed,
            )
            return
        load_catalog()

    def _prepare_image_analysis(self, completed, failed) -> None:
        try:
            if self.image_analysis_controller is None:
                self._build_and_install_feature(
                    "image_analysis", self._build_image_analysis
                )
        except Exception as exc:  # noqa: BLE001 - feature construction boundary
            failed(exc)
            return
        assert self.image_analysis_controller is not None
        if self.image_analysis_controller.worker_startup_state == "ready":
            completed()
            return
        self._image_analysis_feature_done = completed
        self._image_analysis_feature_failed = failed
        if self._deferred_startup_complete:
            self.image_analysis_controller.start_worker()

    def _image_analysis_feature_state_changed(self, state: str, detail: str) -> None:
        if self._image_analysis_feature_done is None:
            return
        if state == "ready":
            completed = self._image_analysis_feature_done
            self._image_analysis_feature_done = None
            self._image_analysis_feature_failed = None
            completed()
        elif state in {"failed", "startup_timeout", "unavailable"}:
            failed = self._image_analysis_feature_failed
            self._image_analysis_feature_done = None
            self._image_analysis_feature_failed = None
            if failed is not None:
                failed(detail or state)

    def _feature_state_changed(self, key: str, state: str, error: str) -> None:
        host = self.page_hosts.get(key)
        if host is not None:
            host.set_feature_state(FeatureState(state), error)
        status_state = {
            FeatureState.LOADING.value: "loading",
            FeatureState.READY.value: "ready",
            FeatureState.FAILED.value: "failed",
        }.get(state)
        if status_state is not None:
            self.status_controller.set_page_state(key, status_state)
        if (
            key == "organization"
            and state == FeatureState.READY.value
            and self._pending_organization_tag is not None
        ):
            tag, self._pending_organization_tag = self._pending_organization_tag, None
            self.organization_page._navigate_to_tag(tag)
        if state == FeatureState.READY.value:
            callbacks = self._ready_callbacks.pop(key, [])
            for callback in callbacks:
                callback()

    def _run_when_ready(self, key: str, callback) -> None:
        if self.feature_lifecycle.state(key) is FeatureState.READY:
            callback()
            return
        self._ready_callbacks.setdefault(key, []).append(callback)
        self.feature_lifecycle.request(key)

    def _ensure_embedded_gelbooru(self) -> None:
        if self.embedded_gelbooru_session_factory is not None:
            return
        self._initialize_embedded_gelbooru()

    def _initialize_embedded_gelbooru(self) -> None:
        if self.embedded_gelbooru_session_factory is not None:
            return
        if QThread.currentThread() is not self.thread():
            raise RuntimeError("Embedded QtWebEngine must be initialized on the GUI thread")
        from booruflow.infrastructure.embedded_gelbooru import (
            EmbeddedGelbooruBridge,
            EmbeddedGelbooruProfile,
            EmbeddedGelbooruSessionFactory,
        )

        started = startup_profile.now_ns()
        profile = EmbeddedGelbooruProfile(self.project_root / "var", self, log=self.log)
        bridge = EmbeddedGelbooruBridge(
            profile, self, pre_save_delay_seconds=0.0, log=self.log
        )
        self.embedded_gelbooru_profile = profile
        self.embedded_gelbooru_bridge = bridge
        self.embedded_gelbooru_session_factory = EmbeddedGelbooruSessionFactory(bridge)
        startup_profile.duration("Embedded QtWebEngine lazy initialization", started)

    def _prepare_embedded_webengine(self, completed, failed) -> None:
        try:
            self._initialize_embedded_gelbooru()
        except Exception as exc:  # noqa: BLE001 - feature boundary
            failed(exc)
        else:
            completed()

    def _update_status(self, key: str | None = None) -> None:
        if key is None:
            row = self.navigation.currentRow()
            key = (
                str(self.navigation.item(row).data(Qt.ItemDataRole.UserRole))
                if row >= 0
                else "home"
            )
        self.status_controller.activate_page(key)

    def change_language(self, code: str) -> None:
        if self.catalog.set_language(code) == code:
            self.retranslate()

    def retranslate(self) -> None:
        self.setWindowTitle(self.catalog.text("app.title"))
        for index, (kind, key) in enumerate(self.VISIBLE_NAVIGATION):
            item = self.navigation.item(index)
            label_key = f"nav.group.{key}" if kind == "group" else f"nav.{key}"
            label = self.catalog.text(label_key)
            item.setText(label)
            item.setToolTip("" if kind == "group" else label)
        self._update_navigation_width()
        for page in self.content_pages:
            if hasattr(page, "retranslate"):
                page.retranslate()
        for host in self.page_hosts.values():
            host.retranslate()
        if self.gelbooru_session_dialog is not None:
            self.gelbooru_session_dialog.retranslate()
        self.log_button.setText(
            self.catalog.text("log.hide")
            if self.log_view.isVisible()
            else self.catalog.text("log.show")
        )
        self.clear_log_button.setText(self.catalog.text("log.clear"))
        self.debug_log_toggle.setText(self.catalog.text("log.debug"))
        self.status_controller.retranslate()
        self._update_status()

    def _update_navigation_width(self) -> None:
        """Size the sidebar once from its translated visible content."""
        text_width = 0
        group_width = 0
        for row in range(self.navigation.count()):
            item = self.navigation.item(row)
            if item.data(Qt.ItemDataRole.UserRole):
                width = QFontMetrics(self.navigation.font()).horizontalAdvance(item.text())
                text_width = max(text_width, width)
            else:
                width = QFontMetrics(item.font()).horizontalAdvance(item.text())
                group_width = max(group_width, width)
        # icon + icon/text gap + horizontal padding + frame/selection breathing room
        page_width = self.navigation.iconSize().width() + 12 + text_width + 28
        width = max(group_width + 28, page_width)
        self.navigation.setFixedWidth(max(176, min(width, 320)))

    def _credentials(self) -> dict[str, object]:
        return self.credentials_repository.load() if self.credentials_repository else {}

    def _review_results_to_grabber(self, entries: tuple[tuple[str, str], ...]) -> None:
        self.navigate_to_key("grabber")
        def populate() -> None:
            self._show_grabber_launcher()
            self.grabber_page.tags.setPlainText(
                "\n".join(f"{site}\t{tag}" for site, tag in entries)
            )
            self.log(self.catalog.text("review.sent_grabber", count=len(entries)))

        self._run_when_ready("grabber", populate)

    def _recycle_cleanup(self) -> None:
        paths, size, report = self.cleanup_controller.recycle_preview()
        if not paths:
            return
        answer = QMessageBox.question(
            self,
            self.catalog.text("cleanup.confirm_title"),
            self.catalog.text(
                "cleanup.confirm",
                count=len(paths),
                size=size / (1024 * 1024),
                report=report,
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.log(self.catalog.text("cleanup.cancelled"))
            return
        self.cleanup_controller.recycle(paths)

    def _review_organization_tags(self, tags: tuple[str, ...]) -> None:
        board = str(self.organization_page.board.currentData())
        self.navigate_to_key("review")

        def populate() -> None:
            self.review_page.queries.setPlainText("\n".join(tags))
            index = self.review_page.site.findData((board,))
            if index >= 0:
                self.review_page.site.setCurrentIndex(index)
            self.log(self.catalog.text("organization.sent_review", count=len(tags)))

        self._run_when_ready("review", populate)

    def _confirm_taxonomy_update(self, preview: dict, summary: dict) -> None:
        answer = QMessageBox.question(
            self,
            self.catalog.text("organization.update"),
            self.catalog.text(
                "organization.update_confirm",
                total=summary.get("total", 0),
                added=summary.get("added", 0),
                removed=summary.get("removed", 0),
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.organization_coordinator.cancel_preview()
            return
        self.organization_coordinator.accept_preview(preview)

    def _save_options(self, settings: dict, credentials: dict) -> None:
        try:
            if self.settings_repository:
                self.settings_repository.save(settings)
            if self.credentials_repository:
                self.credentials_repository.save(credentials)
        except OSError as exc:
            self.log(self.catalog.text("log.options_failed", error=exc))
            self.status_controller.show_message(
                "options", self.catalog.text("status.message.save_failed"), 6_000
            )
            self.log_view.show()
            self.log_button.setText(self.catalog.text("log.hide"))
            return
        self._settings = dict(settings)
        if self.options_page is not None:
            self.options_page._settings = dict(settings)
            self.options_page._update_grabber_status()
        from booruflow.infrastructure.grabber import grabber_availability

        grabber = grabber_availability(str(settings.get("grabber_executable", "")))
        self.capabilities = ApplicationCapabilities(grabber=grabber)
        self.dashboard_page.grabber = grabber
        self.dashboard_page.retranslate()
        if self.grabber_page is not None:
            self.grabber_page.set_available(grabber.available)
            if grabber.available and self.grabber_controller is not None:
                self.grabber_controller.load(silent=True)
        if self.review_page is not None:
            self.review_page.settings = settings
        if self.tagging_page is not None:
            self.tagging_page.settings = dict(settings)
        if self.image_analysis_controller is not None:
            self.image_analysis_controller.apply_settings(settings)
        database_path = gelbooru_tag_database(settings)
        database = str(database_path) if database_path else ""
        e621_database = str(settings.get("e621_database", ""))
        if self.tag_browser_page is not None:
            self.tag_browser_page.set_databases(
                {
                    "gelbooru": Path(database) if database else None,
                    "e621": Path(e621_database) if e621_database else None,
                }
            )
            self.tag_browser_page.set_alias_databases(
                {"gelbooru": gelbooru_alias_database(settings), "e621": None}
            )
        if self.wiki_page is not None:
            self.wiki_page.tag_database_path = Path(database) if database else None
        if getattr(self, "wiki_audit_page", None) is not None:
            self.wiki_audit_page.tag_database = Path(database) if database else None
            self.wiki_audit_page.alias_database = gelbooru_alias_database(settings)
        self.browser_launcher.update_settings(settings)
        self._select_publish_backend(str(settings.get("gelbooru_publish_backend", "embedded")))
        self.status_controller.show_message(
            "options", self.catalog.text("status.message.saved"), 5_000
        )
        self.log(self.catalog.text("log.options_saved"))

    def _activate_database_path(self, site: str, path: Path) -> None:
        """Persist a validated updater output, never a staging or failed path."""
        if self.settings_repository is None:
            return
        settings = self.settings_repository.load()
        key = "gelbooru_tag_database" if site == "gelbooru" else "e621_database"
        settings[key] = str(path)
        self.settings_repository.save(settings)
        self._settings = dict(settings)
        if self.options_page is not None:
            self.options_page._settings = dict(settings)
        if self.image_analysis_controller is not None:
            self.image_analysis_controller.apply_settings(settings)
        if self.tagging_page is not None:
            self.tagging_page.settings = dict(settings)
        if self.review_page is not None:
            self.review_page.settings = dict(settings)
        if self.tag_browser_page is not None:
            self.tag_browser_page.set_databases(
                {
                    "gelbooru": gelbooru_tag_database(settings),
                    "e621": Path(str(settings.get("e621_database", "")))
                    if str(settings.get("e621_database", "")) else None,
                }
            )
        if self.wiki_page is not None:
            self.wiki_page.tag_database_path = gelbooru_tag_database(settings)
        if getattr(self, "wiki_audit_page", None) is not None:
            self.wiki_audit_page.tag_database = gelbooru_tag_database(settings)

    def toggle_log(self) -> None:
        visible = not self.log_view.isVisible()
        self.log_view.setVisible(visible)
        self.log_button.setText(self.catalog.text("log.hide" if visible else "log.show"))

    def log(self, message: str) -> None:
        formatted = self._run_log.format(message)
        self._log_history.append(formatted)
        if len(self._log_history) > 2_000:
            del self._log_history[:-2_000]
        self._run_log.write(formatted)
        if hasattr(self, "log_view"):
            if self._show_debug_logs or "[DEBUG]" not in formatted:
                self.log_view.appendPlainText(formatted)
        else:
            self._early_logs.append(formatted)

    def log_threadsafe(self, message: str) -> None:
        self.diagnostic_log_requested.emit(message)

    def _set_debug_logs_visible(self, visible: bool) -> None:
        self._show_debug_logs = bool(visible)
        self._refresh_log_view()

    def _refresh_log_view(self) -> None:
        self.log_view.setPlainText("\n".join(
            line for line in self._log_history
            if self._show_debug_logs or "[DEBUG]" not in line
        ))
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _clear_visible_logs(self) -> None:
        self._log_history.clear()
        self._early_logs.clear()
        self.log_view.clear()

    def closeEvent(self, event) -> None:
        if self.tagging_controller is not None and not self.tagging_controller.shutdown():
            event.ignore()
            return
        if (
            self.auto_organize_controller is not None
            and not self.auto_organize_controller.shutdown()
        ):
            event.ignore()
            return
        if (
            self.folder_artists_controller is not None
            and not self.folder_artists_controller.shutdown()
        ):
            event.ignore()
            return
        if self.similar_artists_controller is not None:
            self.similar_artists_controller.shutdown()
        if self.image_analysis_controller is not None:
            self.image_analysis_controller.shutdown()
        if (
            self.options_maintenance_controller is not None
            and not self.options_maintenance_controller.shutdown()
        ):
            event.ignore()
            return
        if (
            self.options_session_test_worker is not None
            and self.options_session_test_worker.isRunning()
        ):
            self.options_session_test_worker.requestInterruption()
            self.options_session_test_worker.wait(5_000)
        self.browser_launcher.close()
        if self.embedded_gelbooru_bridge is not None:
            self.embedded_gelbooru_bridge.cancel()
        super().closeEvent(event)

    def _test_browser(self, settings: dict) -> None:
        previous = self.browser_launcher.settings
        self.browser_launcher.update_settings(settings)
        self.browser_launcher.open("https://gelbooru.com/")
        self.browser_launcher.update_settings(previous)

    def _save_tagging_settings(self, values: dict[str, object]) -> None:
        if self.settings_repository is None:
            return
        allowed = {
            "tagging_site", "tagging_query", "tagging_pages", "tagging_start",
            "tagging_minimum", "tagging_maximum",
        }
        settings = self.settings_repository.load()
        settings.update({key: value for key, value in values.items() if key in allowed})
        self.settings_repository.save(settings)
        self._settings.update({key: settings[key] for key in allowed if key in settings})

    def _save_cleanup_blacklist(self, path: str) -> None:
        if self.settings_repository is None:
            return
        settings = self.settings_repository.load()
        settings["blacklist_file"] = path
        self.settings_repository.save(settings)
        self._settings = dict(settings)
        if self.review_page is not None:
            self.review_page.settings["blacklist_file"] = path

    def _start_hydra_operation(self, command: str) -> None:
        if self.hydra_model_process is None or self.options_page is None:
            return
        if self.hydra_model_process.state() != QProcess.ProcessState.NotRunning:
            return
        self.options_page.set_model_operation_running(
            True, self.catalog.text("cleanup.hydra_running")
        )
        self.hydra_model_process.setWorkingDirectory(str(self.project_root))
        self.hydra_model_process.start(
            self.python_executable,
            ["-m", "booruflow.cli.hydra_model", command, "--root", str(self.project_root)],
        )

    def _hydra_operation_finished(self, code: int, _status) -> None:
        if self.hydra_model_process is None or self.options_page is None:
            return
        from booruflow.application.hydra_model_manager import migrated_hydra_settings

        output = bytes(self.hydra_model_process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        ).strip()
        if code != 0:
            detail = output.splitlines()[-1] if output else f"code {code}"
            self.options_page.set_model_operation_running(
                False, self.catalog.text("cleanup.hydra_failed", error=detail)
            )
            return
        if self.settings_repository is not None:
            settings, changed = migrated_hydra_settings(
                self.settings_repository.load(), self.project_root
            )
            if changed:
                self.settings_repository.save(settings)
                self._settings = dict(settings)
                if self.options_page is not None:
                    self.options_page._settings = dict(settings)
                if self.image_analysis_controller is not None:
                    self.image_analysis_controller.apply_settings(settings)
        self.options_page.set_model_operation_running(False)
        if self.options_maintenance_controller is not None:
            self.options_maintenance_controller.refresh_storage()

    def _remove_hydra(self) -> None:
        if self.options_page is None:
            return
        from booruflow.application.hydra_model_manager import (
            hydra_directory,
            inspect_hydra,
            remove_hydra,
        )
        from booruflow.application.model_inventory import format_size
        from booruflow.infrastructure.retro_cleanup import send_to_recycle_bin

        target = hydra_directory(self.project_root)
        installation = inspect_hydra(target)
        if installation.state == "absent":
            if self.options_maintenance_controller is not None:
                self.options_maintenance_controller.refresh_storage()
            return
        answer = QMessageBox.question(
            self,
            self.catalog.text("cleanup.hydra_remove_title"),
            self.catalog.text(
                "cleanup.hydra_remove_confirm", size=format_size(installation.size)
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.settings_repository is not None:
            settings = self.settings_repository.load()
            settings["image_analysis_hydra_enabled"] = False
            self.settings_repository.save(settings)
            self._settings = dict(settings)
            if self.options_page is not None:
                self.options_page._settings = dict(settings)
            if self.image_analysis_controller is not None:
                self.image_analysis_controller.apply_settings(settings)
            process = (
                self.image_analysis_controller.process
                if self.image_analysis_controller is not None
                else None
            )
            if (
                process is not None
                and process.state() != QProcess.ProcessState.NotRunning
                and not process.waitForFinished(5_000)
            ):
                self.options_page.set_model_operation_running(
                    False,
                    self.catalog.text(
                        "cleanup.hydra_failed", error="ImageAnalysis worker did not stop"
                    ),
                )
                return
        try:
            remove_hydra(
                self.project_root, confirmed=True, recycler=send_to_recycle_bin
            )
        except OSError as exc:
            self.options_page.set_model_operation_running(
                False, self.catalog.text("cleanup.hydra_failed", error=exc)
            )
            return
        if self.options_maintenance_controller is not None:
            self.options_maintenance_controller.refresh_storage()
        self.options_page.hydra_status_label.setText(
            self.catalog.text("cleanup.hydra_removed")
        )

    def _build_gelbooru_publisher(self):
        """Build the selected publisher in the worker without exposing web cookies."""
        from booruflow.application.batch_publisher import PUBLISH_DELAY_SECONDS, BatchPublisher
        from booruflow.application.publish_preparation import PublishPreparationService
        from booruflow.infrastructure.embedded_gelbooru import (
            EmbeddedGelbooruEditTransport,
            LazyGelbooruEditTransport,
        )
        from booruflow.infrastructure.gelbooru_browser_transport import (
            BrowserGelbooruEditTransport,
        )
        from booruflow.infrastructure.image_analysis_repository import (
            ImageAnalysisRepository,
        )
        from booruflow.infrastructure.image_sources import GelbooruPostProvider

        database = (
            self.image_analysis_controller.database
            if self.image_analysis_controller is not None
            else self.project_root / "var" / "state" / "image_analysis.sqlite"
        )
        analysis_settings = (
            self.image_analysis_controller.settings
            if self.image_analysis_controller is not None
            else self._settings
        )
        repository = ImageAnalysisRepository(database)
        credentials = self._credentials().get("gelbooru", {})
        credentials = credentials if isinstance(credentials, dict) else {}
        provider = GelbooruPostProvider(
            str(credentials.get("user_id", "")), str(credentials.get("api_key", ""))
        )
        factory = self._active_gelbooru_session_factory()
        if factory is None:
            raise RuntimeError(self.catalog.text("tagging.publish.disabled"))
        preparation = PublishPreparationService(
            repository, provider, log=self.log_threadsafe,
            alias_database=gelbooru_alias_database(analysis_settings),
        )
        selected_transport = (
            EmbeddedGelbooruEditTransport(
                diagnostic_only=self._embedded_form_diagnostic_enabled(),
                http_diagnostic=self._embedded_http_diagnostic_enabled(),
            )
            if self.publish_backend == "embedded"
            else BrowserGelbooruEditTransport()
        )
        transport = LazyGelbooruEditTransport(factory, selected_transport)
        return BatchPublisher(
            repository, preparation, transport, object(),
            delay_seconds=PUBLISH_DELAY_SECONDS,
            log=self.log_threadsafe,
        )

    def _build_tagging_publisher(self):
        """Build independent site publishers over one durable batch repository."""
        from booruflow.application.batch_publisher import (
            PUBLISH_DELAY_SECONDS,
            BatchPublisher,
            MixedSiteBatchPublisher,
        )
        from booruflow.application.publish_preparation import PublishPreparationService
        from booruflow.infrastructure.e621_client import E621Client
        from booruflow.infrastructure.e621_publish_transport import E621PublishTransport
        from booruflow.infrastructure.embedded_gelbooru import (
            EmbeddedGelbooruEditTransport,
            LazyGelbooruEditTransport,
        )
        from booruflow.infrastructure.gelbooru_browser_transport import (
            BrowserGelbooruEditTransport,
        )
        from booruflow.infrastructure.image_analysis_repository import (
            ImageAnalysisRepository,
        )
        from booruflow.infrastructure.image_sources import (
            E621PostProvider,
            GelbooruPostProvider,
        )

        database = (
            self.image_analysis_controller.database
            if self.image_analysis_controller is not None
            else self.project_root / "var" / "state" / "image_analysis.sqlite"
        )
        analysis_settings = (
            self.image_analysis_controller.settings
            if self.image_analysis_controller is not None
            else self._settings
        )
        repository = ImageAnalysisRepository(database)
        publishers = {}
        factory = self._active_gelbooru_session_factory()
        if factory is not None:
            gel_credentials = self._credentials().get("gelbooru", {})
            gel_credentials = gel_credentials if isinstance(gel_credentials, dict) else {}
            gel_provider = GelbooruPostProvider(
                str(gel_credentials.get("user_id", "")),
                str(gel_credentials.get("api_key", "")),
            )
            gel_preparation = PublishPreparationService(
                repository,
                gel_provider,
                log=self.log_threadsafe,
                alias_database=gelbooru_alias_database(analysis_settings),
            )
            selected_transport = (
                EmbeddedGelbooruEditTransport(
                    diagnostic_only=self._embedded_form_diagnostic_enabled(),
                    http_diagnostic=self._embedded_http_diagnostic_enabled(),
                )
                if self.publish_backend == "embedded"
                else BrowserGelbooruEditTransport()
            )
            publishers["gelbooru"] = BatchPublisher(
                repository,
                gel_preparation,
                LazyGelbooruEditTransport(factory, selected_transport),
                object(),
                delay_seconds=PUBLISH_DELAY_SECONDS,
                log=self.log_threadsafe,
            )

        e621_credentials = self._credentials().get("e621", {})
        e621_credentials = e621_credentials if isinstance(e621_credentials, dict) else {}
        username = str(e621_credentials.get("user_id", "")).strip()
        api_key = str(e621_credentials.get("api_key", "")).strip()
        if username and api_key:
            e621_client = E621Client(username, api_key)
            e621_preparation = PublishPreparationService(
                repository,
                E621PostProvider(client=e621_client),
                site="e621",
                log=self.log_threadsafe,
            )
            publishers["e621"] = BatchPublisher(
                repository,
                e621_preparation,
                E621PublishTransport(e621_client),
                object(),
                site="e621",
                delay_seconds=1.0,
                log=self.log_threadsafe,
            )
        if not publishers:
            repository.close()
            raise RuntimeError(self.catalog.text("tagging.publish.disabled"))
        return MixedSiteBatchPublisher(repository, publishers)

    def _build_e621_validation_client(self):
        """Build the shared read-only credential probe used by the batch UI."""
        from booruflow.infrastructure.e621_client import E621Client

        credentials = self._credentials().get("e621", {})
        credentials = credentials if isinstance(credentials, dict) else {}
        username = str(credentials.get("user_id", "")).strip()
        api_key = str(credentials.get("api_key", "")).strip()
        if not username or not api_key:
            return None
        return E621Client(username, api_key)

    def _active_gelbooru_session_factory(self):
        if self.publish_backend == "embedded":
            self._ensure_embedded_gelbooru()
            return self.embedded_gelbooru_session_factory
        if self.publish_backend == "cdp":
            if self.gelbooru_session_factory is None:
                from booruflow.infrastructure.gelbooru_browser_transport import (
                    BrowserGelbooruSessionFactory,
                )

                self.gelbooru_session_factory = BrowserGelbooruSessionFactory(
                    self.browser_launcher
                )
            return self.gelbooru_session_factory
        return None

    def _prepare_publication_backend(self) -> None:
        """Create GUI-thread-only publication infrastructure after user confirmation."""
        if self.publish_backend == "embedded":
            self._ensure_embedded_gelbooru()
        elif self.publish_backend == "cdp":
            self._active_gelbooru_session_factory()

    def _embedded_form_diagnostic_enabled(self) -> bool:
        return bool(
            self.publish_backend == "embedded"
            and self._embedded_form_diagnostic_active
        )

    def _set_embedded_form_diagnostic_active(self, enabled: bool) -> None:
        self._embedded_form_diagnostic_active = bool(enabled)

    def _embedded_http_diagnostic_enabled(self) -> bool:
        return bool(
            self.publish_backend == "embedded"
            and self._embedded_http_diagnostic_active
        )

    def _set_embedded_http_diagnostic_active(self, enabled: bool) -> None:
        self._embedded_http_diagnostic_active = bool(enabled)

    def _select_publish_backend(self, backend: str) -> None:
        self.publish_backend = backend if backend in {"embedded", "cdp", "disabled"} else "embedded"

    def _open_gelbooru_session(self) -> None:
        if self.publish_backend == "cdp":
            try:
                self._active_gelbooru_session_factory().open()
                self.log("Gelbooru session open: backend=browser-cdp")
            except Exception as exc:  # noqa: BLE001 - visible UI boundary
                message = self.catalog.text("tagging.session.error", error=exc)
                self.options_page.show_embedded_session_test_result(message)
                self.log(message)
            return
        if self.publish_backend == "disabled":
            self.options_page.show_embedded_session_test_result(
                self.catalog.text("tagging.publish.disabled")
            )
            return
        self._ensure_embedded_gelbooru()
        from booruflow.infrastructure.embedded_gelbooru import GelbooruSessionDialog

        if self.gelbooru_session_dialog is None:
            self.gelbooru_session_dialog = GelbooruSessionDialog(
                self.embedded_gelbooru_profile, self.catalog, self, log=self.log
            )
            self.gelbooru_session_dialog.manual_diagnostic.toggled.connect(
                self._set_embedded_form_diagnostic_active
            )
            self.gelbooru_session_dialog.http_diagnostic_state_changed.connect(
                self._set_embedded_http_diagnostic_active
            )
        self.gelbooru_session_dialog.open_url(
            "https://gelbooru.com/index.php?page=account&s=home", new_tab=True
        )
        self.gelbooru_session_dialog.show()
        self.gelbooru_session_dialog.raise_()
        self.gelbooru_session_dialog.activateWindow()

    def _open_internal_booru_url(self, url: str) -> None:
        self._ensure_embedded_gelbooru()
        from booruflow.infrastructure.embedded_gelbooru import GelbooruSessionDialog

        if self.gelbooru_session_dialog is None:
            self.gelbooru_session_dialog = GelbooruSessionDialog(
                self.embedded_gelbooru_profile, self.catalog, self, log=self.log
            )
            self.gelbooru_session_dialog.manual_diagnostic.toggled.connect(
                self._set_embedded_form_diagnostic_active
            )
            self.gelbooru_session_dialog.http_diagnostic_state_changed.connect(
                self._set_embedded_http_diagnostic_active
            )
        self.gelbooru_session_dialog.open_url(url, new_tab=True)
        self.gelbooru_session_dialog.show()
        self.gelbooru_session_dialog.raise_()
        self.gelbooru_session_dialog.activateWindow()

    def _test_gelbooru_session(self) -> None:
        from booruflow.presentation.pyside6.tagging_controller import SessionTestWorker

        if (
            self.options_session_test_worker is not None
            and self.options_session_test_worker.isRunning()
        ):
            return
        factory = self._active_gelbooru_session_factory()
        if factory is None:
            self.options_page.show_embedded_session_test_result(
                self.catalog.text("tagging.publish.disabled")
            )
            return
        backend = "browser-cdp" if self.publish_backend == "cdp" else "embedded"
        self.log(f"Gelbooru session test: backend={backend}")
        self.options_page.set_embedded_session_test_running(True)
        self.options_session_test_worker = SessionTestWorker(factory)
        self.options_session_test_worker.completed.connect(
            lambda result, selected=backend: self._session_test_finished(selected, result)
        )
        self.options_session_test_worker.start()

    def _session_test_finished(self, backend: str, result: str) -> None:
        state, _, detail = result.partition(":")
        message = self.catalog.text(f"tagging.session.{state}", error=detail)
        self.options_page.show_embedded_session_test_result(message)
        self.log(f"Gelbooru {backend} session: result={state}")

    def _reset_embedded_session(self) -> None:
        if QMessageBox.question(
            self, self.catalog.text("options.publisher_reset_title"),
            self.catalog.text("options.publisher_reset_confirm"),
        ) != QMessageBox.StandardButton.Yes:
            return
        self._ensure_embedded_gelbooru()
        self.embedded_gelbooru_profile.reset_session()
        QMessageBox.information(
            self, self.catalog.text("options.publisher_reset_title"),
            self.catalog.text("options.publisher_reset_done")
        )

    def _reset_browser_profile(self) -> None:
        if QMessageBox.question(
            self,
            self.catalog.text("options.browser_reset_title"),
            self.catalog.text("options.browser_reset_confirm"),
        ) != QMessageBox.StandardButton.Yes:
            return
        if self.browser_launcher.reset_dedicated_profile():
            QMessageBox.information(self, self.catalog.text("options.browser_reset_title"), self.catalog.text("options.browser_reset_done"))
        else:
            QMessageBox.warning(self, self.catalog.text("options.browser_reset_title"), self.catalog.text("options.browser_reset_missing"))
