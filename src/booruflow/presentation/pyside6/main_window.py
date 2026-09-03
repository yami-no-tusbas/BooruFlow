"""Main window, localization and controllers for BooruFlow."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from booruflow.application.batch_publisher import PUBLISH_DELAY_SECONDS
from booruflow.application.capabilities import ApplicationCapabilities
from booruflow.application.database_paths import gelbooru_alias_database, gelbooru_tag_database
from booruflow.application.ports import SettingsRepository
from booruflow.application.tasks import MemoryTaskRepository, TaskRepository
from booruflow.application.taxonomy import TaxonomyRepository
from booruflow.infrastructure.browser_launcher import BrowserLauncher
from booruflow.infrastructure.embedded_gelbooru import (
    EmbeddedGelbooruBridge,
    EmbeddedGelbooruEditTransport,
    EmbeddedGelbooruProfile,
    EmbeddedGelbooruSessionFactory,
    GelbooruSessionDialog,
    LazyGelbooruEditTransport,
)
from booruflow.infrastructure.gelbooru_aliases import inspect_alias_catalog
from booruflow.infrastructure.gelbooru_browser_transport import (
    BrowserGelbooruEditTransport,
    BrowserGelbooruSessionFactory,
)
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import E621PostProvider, GelbooruPostProvider
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.auto_organize_controller import AutoOrganizeController
from booruflow.presentation.pyside6.auto_organize_page import AutoOrganizePage
from booruflow.presentation.pyside6.cleanup_controller import CleanupController
from booruflow.presentation.pyside6.cleanup_page import CleanupPage
from booruflow.presentation.pyside6.credential_validation_controller import (
    CredentialValidationController,
)
from booruflow.presentation.pyside6.database_update_controller import DatabaseUpdateController
from booruflow.presentation.pyside6.grabber_controller import GrabberController
from booruflow.presentation.pyside6.grabber_page import GrabberPage
from booruflow.presentation.pyside6.icons import navigation_icon
from booruflow.presentation.pyside6.image_analysis_controller import ImageAnalysisController
from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
from booruflow.presentation.pyside6.options_page import OptionsPage
from booruflow.presentation.pyside6.organization_controller import (
    OrganizationCoordinator,
)
from booruflow.presentation.pyside6.organization_page import OrganizationPage
from booruflow.presentation.pyside6.pages import DashboardPage, ScrollablePageHost
from booruflow.presentation.pyside6.review_controller import (
    ReviewCoordinator,
)
from booruflow.presentation.pyside6.review_page import ReviewPage
from booruflow.presentation.pyside6.similar_artists_controller import SimilarArtistsController
from booruflow.presentation.pyside6.similar_artists_page import SimilarArtistsPage
from booruflow.presentation.pyside6.tag_browser_page import TagBrowserPage
from booruflow.presentation.pyside6.tagging_controller import SessionTestWorker, TaggingController
from booruflow.presentation.pyside6.tagging_page import TaggingPage
from booruflow.presentation.pyside6.task_manager import TaskManager
from booruflow.presentation.pyside6.task_page import TaskPage
from booruflow.presentation.pyside6.ui_logging import RunLog
from booruflow.presentation.pyside6.wiki_page import WikiPage


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
        "auto_organize",
        "similar_artists",
        "organization",
        "tag_browser",
        "wiki",
        "cleanup",
        "options",
        "grabber",
        "tasks",
    )

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
        super().__init__(parent)
        self._early_logs: list[str] = []
        self._log_history: list[str] = []
        self._show_debug_logs = False
        self._embedded_form_diagnostic_active = False
        self._embedded_http_diagnostic_active = False
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
        settings = settings_repository.load() if settings_repository else {}
        self.browser_launcher = BrowserLauncher(self.project_root / "var", settings)
        self.gelbooru_session_factory = BrowserGelbooruSessionFactory(self.browser_launcher)
        self.embedded_gelbooru_profile = EmbeddedGelbooruProfile(
            self.project_root / "var", self, log=self.log
        )
        self.embedded_gelbooru_bridge = EmbeddedGelbooruBridge(
            self.embedded_gelbooru_profile, self,
            pre_save_delay_seconds=0.0,
            log=self.log
        )
        self.embedded_gelbooru_session_factory = EmbeddedGelbooruSessionFactory(
            self.embedded_gelbooru_bridge
        )
        self.gelbooru_session_dialog: GelbooruSessionDialog | None = None
        self.internal_browser_launcher = _InternalBooruLauncher(self)
        self.publish_backend = str(settings.get("gelbooru_publish_backend", "embedded"))
        self.options_session_test_worker: SessionTestWorker | None = None
        credentials = credentials_repository.load() if credentials_repository else {}
        self.resize(1120, 760)
        self.setMinimumSize(860, 600)

        self.navigation = QListWidget()
        self.navigation.setFixedWidth(230)
        self.navigation.setIconSize(QSize(30, 30))
        self.navigation.setSpacing(2)
        for key in self.NAVIGATION_KEYS:
            item = QListWidgetItem(navigation_icon(key), "")
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize(210, 46))
            self.navigation.addItem(item)

        self.pages = QStackedWidget()
        self.content_pages: list[QWidget] = []

        def add_page(page: QWidget) -> None:
            self.content_pages.append(page)
            self.pages.addWidget(ScrollablePageHost(page))

        dashboard = DashboardPage(catalog, capabilities.grabber)
        dashboard.navigate_requested.connect(self.navigate_to_key)
        add_page(dashboard)
        self.review_page = ReviewPage(catalog, settings)
        self.review_coordinator = ReviewCoordinator(
            self.project_root,
            self.python_executable,
            self.catalog,
            self.review_page,
            self.settings_repository,
            self._credentials,
            self.log,
            self.task_manager,
            self,
        )
        self.review_controller = self.review_coordinator.process_controller
        self.review_page.start_requested.connect(self.review_coordinator.start)
        self.review_page.stop_requested.connect(self.review_coordinator.stop)
        self.review_page.count_requested.connect(self.review_coordinator.count)
        self.review_page.autocomplete_requested.connect(self.review_coordinator.autocomplete)
        self.review_page.grabber_tags_requested.connect(self._review_results_to_grabber)
        add_page(self.review_page)
        self.tagging_page = TaggingPage(catalog, settings, self.internal_browser_launcher)
        self.tagging_controller = TaggingController(
            self.catalog,
            self.tagging_page,
            self._credentials,
            self.log,
            self.task_manager,
            self,
            publisher_factory=self._build_tagging_publisher,
            session_factory_provider=self._active_gelbooru_session_factory,
            publication_backend_provider=lambda: self.publish_backend,
            e621_validation_factory=self._build_e621_validation_client,
            diagnostic_mode_provider=self._embedded_form_diagnostic_enabled,
            http_diagnostic_mode_provider=self._embedded_http_diagnostic_enabled,
        )
        self.tagging_page.start_requested.connect(self.tagging_controller.start)
        self.tagging_page.stop_requested.connect(self.tagging_controller.stop)
        self.tagging_page.query_saved.connect(self._save_tagging_query)
        add_page(self.tagging_page)
        self.image_analysis_page = ImageAnalysisPage(catalog)
        self.image_analysis_controller = ImageAnalysisController(
            self.project_root, self.python_executable, self.image_analysis_page,
            settings, self._credentials, self.log, start_image_worker, self,
        )
        self.tagging_controller.bind_image_analysis(self.image_analysis_controller)
        add_page(self.image_analysis_page)
        self.auto_organize_page = AutoOrganizePage(catalog)
        self.auto_organize_controller = AutoOrganizeController(
            self.project_root, self.auto_organize_page, self.log, self,
            gelbooru_tag_database(settings),
            credential_provider=self._credentials,
        )
        self.auto_organize_page.analyze_requested.connect(self.auto_organize_controller.analyze)
        self.auto_organize_page.stop_requested.connect(self.auto_organize_controller.stop)
        self.auto_organize_page.execute_requested.connect(self.auto_organize_controller.execute)
        add_page(self.auto_organize_page)
        self.similar_artists_page = SimilarArtistsPage(catalog)
        self.similar_artists_controller = SimilarArtistsController(
            self.project_root, self.similar_artists_page, self.image_analysis_controller,
            self.log, self.task_manager, self, browser_launcher=self.internal_browser_launcher,
        )
        add_page(self.similar_artists_page)
        self.organization_page = OrganizationPage(catalog, self.taxonomy_repository.load(), self.internal_browser_launcher)
        self.organization_coordinator = OrganizationCoordinator(
            self.project_root,
            self.catalog,
            self.organization_page,
            self.taxonomy_repository,
            self.settings_repository,
            self._credentials,
            self.log,
            self.task_manager,
            self,
        )
        self.organization_page.save_requested.connect(self.organization_coordinator.save)
        self.organization_page.update_requested.connect(self.organization_coordinator.update)
        self.organization_page.review_tags_requested.connect(self._review_organization_tags)
        self.organization_page.tag_details_requested.connect(
            self.organization_coordinator.load_details
        )
        self.organization_page.wiki_draft_requested.connect(self._prepare_wiki)
        self.organization_coordinator.preview_ready.connect(self._confirm_taxonomy_update)
        add_page(self.organization_page)
        database_path = gelbooru_tag_database(settings)
        database_value = str(database_path) if database_path else ""
        e621_database_value = str(settings.get("e621_database", ""))
        self.tag_browser_page = TagBrowserPage(
            catalog,
            {
                "gelbooru": Path(database_value) if database_value else None,
                "e621": Path(e621_database_value) if e621_database_value else None,
            },
        )
        add_page(self.tag_browser_page)
        self.wiki_page = WikiPage(
            catalog,
            self.project_root / "var" / "wiki_drafts",
            Path(database_value) if database_value else None,
            self.settings_repository,
            self.internal_browser_launcher,
        )
        self.wiki_page.organization_tag_requested.connect(self._open_organization_tag)
        add_page(self.wiki_page)
        self.cleanup_page = CleanupPage(catalog, settings, self.project_root)
        self.cleanup_controller = CleanupController(
            self.project_root,
            self.catalog,
            self.cleanup_page,
            self.settings_repository,
            self.log,
            self.task_manager,
            self,
        )
        self.cleanup_page.scan_requested.connect(self.cleanup_controller.start)
        self.cleanup_page.stop_requested.connect(self.cleanup_controller.stop)
        self.cleanup_page.recycle_requested.connect(self._recycle_cleanup)
        self.cleanup_page.blacklist_changed.connect(self._save_cleanup_blacklist)
        add_page(self.cleanup_page)
        options = OptionsPage(catalog, settings, credentials)
        options.save_requested.connect(self._save_options)
        options.language_changed.connect(self.change_language)
        self.options_page = options
        add_page(options)
        self.database_controller = DatabaseUpdateController(
            self.project_root,
            self.python_executable,
            self.catalog,
            self.options_page,
            self.tag_browser_page,
            self._credentials,
            self.log,
            self.task_manager,
            self,
            alias_page=self.tagging_page,
            database_activated=self._activate_database_path,
        )
        self.database_process = self.database_controller.process
        options.database_update_requested.connect(self.database_controller.start)
        self.tagging_page.alias_update_requested.connect(self.database_controller.start_aliases)
        self.tagging_page.alias_stop_requested.connect(self.database_controller.stop)
        options.database_stop_requested.connect(self.database_controller.stop)
        self.credential_validation_controller = CredentialValidationController(
            options, log=self.log, parent=self
        )
        options.credentials_test_requested.connect(self.credential_validation_controller.start)
        options.browser_test_requested.connect(self._test_browser)
        options.browser_reset_requested.connect(self._reset_browser_profile)
        options.publication_backend_changed.connect(self._select_publish_backend)
        options.embedded_session_open_requested.connect(self._open_gelbooru_session)
        options.embedded_session_test_requested.connect(self._test_gelbooru_session)
        options.embedded_session_reset_requested.connect(self._reset_embedded_session)
        self.grabber_page = GrabberPage(catalog, settings, capabilities.grabber.available)
        self.grabber_controller = GrabberController(
            self.catalog,
            self.grabber_page,
            self.settings_repository,
            self._credentials,
            self.log,
            self.task_manager,
            self,
        )
        self.grabber_process = self.grabber_controller.process
        self.grabber_page.create_requested.connect(self.grabber_controller.create)
        self.grabber_page.load_requested.connect(self.grabber_controller.load)
        self.grabber_page.launch_requested.connect(self.grabber_controller.launch)
        self.grabber_page.previous_requested.connect(self.grabber_controller.previous)
        add_page(self.grabber_page)
        self.task_page = TaskPage(self.catalog, self.task_manager)
        add_page(self.task_page)

        workspace = QSplitter(Qt.Orientation.Horizontal)
        workspace.setChildrenCollapsible(False)
        workspace.addWidget(self.navigation)
        workspace.addWidget(self.pages)
        workspace.setStretchFactor(1, 1)
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
        self.status_label = QLabel()
        self.status_label.setContentsMargins(4, 2, 12, 2)
        status_bar.addWidget(self.status_label, 1)
        self.log_button = QPushButton()
        self.log_button.clicked.connect(self.toggle_log)
        self.debug_log_toggle = QCheckBox("Afficher DEBUG")
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
        self.grabber_controller.load(silent=True)
        self.log(self.catalog.text("log.started"))
        self._log_gelbooru_database_diagnostic(settings)
        if not capabilities.grabber.available:
            self.log(
                self.catalog.text("log.grabber_unavailable", reason=capabilities.grabber.reason)
            )

    def _log_gelbooru_database_diagnostic(self, settings: dict[str, object]) -> None:
        """Log the configured catalogue pair once, without probing neighbouring files."""
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
            self.navigation.setCurrentRow(index)

    def navigate_to_key(self, key: str) -> None:
        if key in self.NAVIGATION_KEYS:
            self.navigate_to(self.NAVIGATION_KEYS.index(key))

    def _prepare_wiki(self, tag: str) -> None:
        self.wiki_page.set_tag(tag)
        self.navigate_to_key("wiki")

    def _open_organization_tag(self, tag: str) -> None:
        self.navigate_to_key("organization")
        self.organization_page._navigate_to_tag(tag)

    def _navigation_changed(self, index: int) -> None:
        if index >= 0:
            self.pages.setCurrentIndex(index)
            if hasattr(self,"image_analysis_controller"):
                self.image_analysis_controller.set_page_active(self.NAVIGATION_KEYS[index]=="image_analysis")
            self._update_status()

    def _update_status(self) -> None:
        index = self.navigation.currentRow()
        key = self.NAVIGATION_KEYS[max(index, 0)]
        self.status_label.setText(
            self.catalog.text("status.ready", page=self.catalog.text(f"nav.{key}"))
        )

    def change_language(self, code: str) -> None:
        if self.catalog.set_language(code) == code:
            self.retranslate()

    def retranslate(self) -> None:
        self.setWindowTitle(self.catalog.text("app.title"))
        for index, key in enumerate(self.NAVIGATION_KEYS):
            label = self.catalog.text(f"nav.{key}")
            self.navigation.item(index).setText(label)
            self.navigation.item(index).setToolTip(label)
        for page in self.content_pages:
            if hasattr(page, "retranslate"):
                page.retranslate()
        if self.gelbooru_session_dialog is not None:
            self.gelbooru_session_dialog.retranslate()
        self.log_button.setText(
            self.catalog.text("log.hide")
            if self.log_view.isVisible()
            else self.catalog.text("log.show")
        )
        self.clear_log_button.setText(self.catalog.text("log.clear"))
        self._update_status()

    def _credentials(self) -> dict[str, object]:
        return self.credentials_repository.load() if self.credentials_repository else {}

    def _review_results_to_grabber(self, entries: tuple[tuple[str, str], ...]) -> None:
        self.grabber_page.tags.setPlainText("\n".join(f"{site}\t{tag}" for site, tag in entries))
        self.navigate_to_key("grabber")
        self.log(self.catalog.text("review.sent_grabber", count=len(entries)))

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
        self.review_page.queries.setPlainText("\n".join(tags))
        board = str(self.organization_page.board.currentData())
        index = self.review_page.site.findData((board,))
        if index >= 0:
            self.review_page.site.setCurrentIndex(index)
        self.navigate_to(1)
        self.log(self.catalog.text("organization.sent_review", count=len(tags)))

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
            self.status_label.setText(self.catalog.text("status.save_failed"))
            self.log(self.catalog.text("log.options_failed", error=exc))
            self.log_view.show()
            self.log_button.setText(self.catalog.text("log.hide"))
            return
        self.review_page.settings = settings
        self.tagging_page.settings = dict(settings)
        self.image_analysis_controller.apply_settings(settings)
        database_path = gelbooru_tag_database(settings)
        database = str(database_path) if database_path else ""
        e621_database = str(settings.get("e621_database", ""))
        self.tag_browser_page.set_databases(
            {
                "gelbooru": Path(database) if database else None,
                "e621": Path(e621_database) if e621_database else None,
            }
        )
        self.wiki_page.tag_database_path = Path(database) if database else None
        self.browser_launcher.update_settings(settings)
        self._select_publish_backend(str(settings.get("gelbooru_publish_backend", "embedded")))
        self.status_label.setText(self.catalog.text("status.saved"))
        self.log(self.catalog.text("log.options_saved"))

    def _activate_database_path(self, site: str, path: Path) -> None:
        """Persist a validated updater output, never a staging or failed path."""
        if self.settings_repository is None:
            return
        settings = self.settings_repository.load()
        key = "gelbooru_tag_database" if site == "gelbooru" else "e621_database"
        settings[key] = str(path)
        self.settings_repository.save(settings)
        self.options_page._settings = dict(settings)
        self.image_analysis_controller.apply_settings(settings)
        self.tagging_page.settings = dict(settings)
        self.review_page.settings = dict(settings)
        self.tag_browser_page.set_databases(
            {
                "gelbooru": gelbooru_tag_database(settings),
                "e621": Path(str(settings.get("e621_database", "")))
                if str(settings.get("e621_database", "")) else None,
            }
        )
        self.wiki_page.tag_database_path = gelbooru_tag_database(settings)

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
        if not self.auto_organize_controller.shutdown():
            event.ignore()
            return
        self.similar_artists_controller.shutdown()
        self.image_analysis_controller.shutdown()
        self.browser_launcher.close()
        self.embedded_gelbooru_bridge.cancel()
        super().closeEvent(event)

    def _test_browser(self, settings: dict) -> None:
        previous = self.browser_launcher.settings
        self.browser_launcher.update_settings(settings)
        self.browser_launcher.open("https://gelbooru.com/")
        self.browser_launcher.update_settings(previous)

    def _save_tagging_query(self, query: str) -> None:
        if self.settings_repository is None: return
        settings = self.settings_repository.load()
        settings["tagging_query"] = query
        self.settings_repository.save(settings)

    def _save_cleanup_blacklist(self, path: str) -> None:
        if self.settings_repository is None:
            return
        settings = self.settings_repository.load()
        settings["blacklist_file"] = path
        self.settings_repository.save(settings)
        self.review_page.settings["blacklist_file"] = path

    def _build_gelbooru_publisher(self):
        """Build the selected publisher in the worker without exposing web cookies."""
        from booruflow.application.batch_publisher import BatchPublisher
        from booruflow.application.publish_preparation import PublishPreparationService

        repository = ImageAnalysisRepository(self.image_analysis_controller.database)
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
            alias_database=gelbooru_alias_database(self.image_analysis_controller.settings),
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
            BatchPublisher,
            MixedSiteBatchPublisher,
        )
        from booruflow.application.publish_preparation import PublishPreparationService
        from booruflow.infrastructure.e621_client import E621Client
        from booruflow.infrastructure.e621_publish_transport import E621PublishTransport

        repository = ImageAnalysisRepository(self.image_analysis_controller.database)
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
                alias_database=gelbooru_alias_database(self.image_analysis_controller.settings),
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
            return self.embedded_gelbooru_session_factory
        if self.publish_backend == "cdp":
            return self.gelbooru_session_factory
        return None

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
                self.gelbooru_session_factory.open()
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
