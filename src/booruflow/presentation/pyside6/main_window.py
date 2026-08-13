"""Main window, localization and controllers for BooruFlow."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
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

from booruflow.application.capabilities import ApplicationCapabilities
from booruflow.application.ports import SettingsRepository
from booruflow.application.tasks import MemoryTaskRepository, TaskRepository
from booruflow.application.taxonomy import TaxonomyRepository
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.cleanup_controller import CleanupController
from booruflow.presentation.pyside6.cleanup_page import CleanupPage
from booruflow.presentation.pyside6.database_update_controller import DatabaseUpdateController
from booruflow.presentation.pyside6.grabber_controller import GrabberController
from booruflow.presentation.pyside6.grabber_page import GrabberPage
from booruflow.presentation.pyside6.icons import navigation_icon
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
from booruflow.presentation.pyside6.tag_browser_page import TagBrowserPage
from booruflow.presentation.pyside6.tagging_controller import TaggingController
from booruflow.presentation.pyside6.tagging_page import TaggingPage
from booruflow.presentation.pyside6.task_manager import TaskManager
from booruflow.presentation.pyside6.task_page import TaskPage
from booruflow.presentation.pyside6.wiki_page import WikiPage


class MainWindow(QMainWindow):
    NAVIGATION_KEYS = (
        "home",
        "review",
        "tagging",
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
    ) -> None:
        super().__init__(parent)
        self.capabilities = capabilities
        self.catalog = catalog
        self.settings_repository = settings_repository
        self.credentials_repository = credentials_repository
        self.task_manager = TaskManager(task_repository or MemoryTaskRepository(), self)
        self.project_root = project_root or Path.cwd()
        self.python_executable = python_executable or sys.executable
        self.taxonomy_repository = TaxonomyRepository(
            self.project_root / "data" / "taxonomy" / "tag_organization.json",
            self.project_root / "data" / "databases",
        )
        settings = settings_repository.load() if settings_repository else {}
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
        dashboard.navigate_requested.connect(self.navigate_to)
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
        self.tagging_page = TaggingPage(catalog, settings)
        self.tagging_controller = TaggingController(
            self.catalog,
            self.tagging_page,
            self._credentials,
            self.log,
            self.task_manager,
            self,
        )
        self.tagging_page.start_requested.connect(self.tagging_controller.start)
        self.tagging_page.stop_requested.connect(self.tagging_controller.stop)
        add_page(self.tagging_page)
        self.organization_page = OrganizationPage(catalog, self.taxonomy_repository.load())
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
        database_value = str(settings.get("gelbooru_database", ""))
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
        )
        self.wiki_page.organization_tag_requested.connect(self._open_organization_tag)
        add_page(self.wiki_page)
        self.cleanup_page = CleanupPage(catalog)
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
        )
        self.database_process = self.database_controller.process
        options.database_update_requested.connect(self.database_controller.start)
        options.database_stop_requested.connect(self.database_controller.stop)
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
        self.clear_log_button = QPushButton()
        self.clear_log_button.clicked.connect(self.log_view.clear)
        status_bar.addPermanentWidget(self.log_button)
        status_bar.addPermanentWidget(self.clear_log_button)

        self.navigation.currentRowChanged.connect(self._navigation_changed)
        self.navigation.setCurrentRow(0)
        self.retranslate()
        self.grabber_controller.load(silent=True)
        self.log(self.catalog.text("log.started"))
        if not capabilities.grabber.available:
            self.log(
                self.catalog.text("log.grabber_unavailable", reason=capabilities.grabber.reason)
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
        database = str(settings.get("gelbooru_database", ""))
        e621_database = str(settings.get("e621_database", ""))
        self.tag_browser_page.set_databases(
            {
                "gelbooru": Path(database) if database else None,
                "e621": Path(e621_database) if e621_database else None,
            }
        )
        self.wiki_page.tag_database_path = Path(database) if database else None
        self.status_label.setText(self.catalog.text("status.saved"))
        self.log(self.catalog.text("log.options_saved"))

    def toggle_log(self) -> None:
        visible = not self.log_view.isVisible()
        self.log_view.setVisible(visible)
        self.log_button.setText(self.catalog.text("log.hide" if visible else "log.show"))

    def log(self, message: str) -> None:
        self.log_view.appendPlainText(
            f"[{datetime.now():%H:%M:%S}] {message}"  # noqa: DTZ005 - local UI clock
        )
