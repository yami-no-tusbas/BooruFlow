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
    QPlainTextEdit,
    QPushButton,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from booruflow.application.capabilities import ApplicationCapabilities
from booruflow.application.ports import SettingsRepository
from booruflow.application.review import ReviewRequest, build_review_commands
from booruflow.infrastructure.localization import LanguageCatalog, translate_legacy_log
from booruflow.presentation.pyside6.icons import navigation_icon
from booruflow.presentation.pyside6.cleanup_controller import CleanupRecycleWorker, CleanupScanWorker
from booruflow.presentation.pyside6.cleanup_page import CleanupPage
from booruflow.presentation.pyside6.options_page import OptionsPage
from booruflow.presentation.pyside6.pages import DashboardPage, PlaceholderPage
from booruflow.presentation.pyside6.review_controller import (
    ReviewCountWorker,
    ReviewProcessController,
)
from booruflow.presentation.pyside6.review_page import ReviewPage
from booruflow.presentation.pyside6.tagging_controller import TaggingWorker
from booruflow.presentation.pyside6.tagging_page import TaggingPage


class MainWindow(QMainWindow):
    NAVIGATION_KEYS = (
        "home", "review", "tagging", "organization", "cleanup", "options", "grabber"
    )

    def __init__(
        self,
        capabilities: ApplicationCapabilities,
        catalog: LanguageCatalog,
        parent: QWidget | None = None,
        settings_repository: SettingsRepository | None = None,
        credentials_repository: SettingsRepository | None = None,
        project_root: Path | None = None,
        python_executable: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.capabilities = capabilities
        self.catalog = catalog
        self.settings_repository = settings_repository
        self.credentials_repository = credentials_repository
        self.project_root = project_root or Path.cwd()
        self.python_executable = python_executable or sys.executable
        self.count_worker: ReviewCountWorker | None = None
        self.tagging_worker: TaggingWorker | None = None
        self.cleanup_worker: CleanupScanWorker | None = None
        self.recycle_worker: CleanupRecycleWorker | None = None
        self.cleanup_matches: list = []
        self.cleanup_report = ""
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
        dashboard = DashboardPage(catalog, capabilities.grabber)
        dashboard.navigate_requested.connect(self.navigate_to)
        self.pages.addWidget(dashboard)
        self.review_page = ReviewPage(catalog, settings)
        self.review_page.start_requested.connect(self._start_review)
        self.review_page.stop_requested.connect(self._stop_review)
        self.review_page.count_requested.connect(self._count_queries)
        self.pages.addWidget(self.review_page)
        self.tagging_page = TaggingPage(catalog, settings)
        self.tagging_page.start_requested.connect(self._start_tagging)
        self.tagging_page.stop_requested.connect(self._stop_tagging)
        self.pages.addWidget(self.tagging_page)
        self.pages.addWidget(PlaceholderPage(catalog, "organization", "page.organization"))
        self.cleanup_page = CleanupPage(catalog)
        self.cleanup_page.scan_requested.connect(self._start_cleanup)
        self.cleanup_page.stop_requested.connect(self._stop_cleanup)
        self.cleanup_page.recycle_requested.connect(self._recycle_cleanup)
        self.pages.addWidget(self.cleanup_page)
        options = OptionsPage(catalog, settings, credentials)
        options.save_requested.connect(self._save_options)
        options.language_changed.connect(self.change_language)
        self.pages.addWidget(options)
        self.pages.addWidget(
            PlaceholderPage(catalog, "grabber", "page.grabber", availability=capabilities.grabber)
        )

        self.review_controller = ReviewProcessController(self)
        self.review_controller.output.connect(self._review_output)
        self.review_controller.progress.connect(self.review_page.set_progress)
        self.review_controller.site_started.connect(self._review_site_started)
        self.review_controller.finished.connect(self._review_finished)

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
        self.log(self.catalog.text("log.started"))
        if not capabilities.grabber.available:
            self.log(self.catalog.text("log.grabber_unavailable", reason=capabilities.grabber.reason))

    def navigate_to(self, index: int) -> None:
        if 0 <= index < self.pages.count():
            self.navigation.setCurrentRow(index)

    def _navigation_changed(self, index: int) -> None:
        if index >= 0:
            self.pages.setCurrentIndex(index)
            self._update_status()

    def _update_status(self) -> None:
        index = self.navigation.currentRow()
        key = self.NAVIGATION_KEYS[index if index >= 0 else 0]
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
        for index in range(self.pages.count()):
            page = self.pages.widget(index)
            if hasattr(page, "retranslate"):
                page.retranslate()
        self.log_button.setText(
            self.catalog.text("log.hide") if self.log_view.isVisible() else self.catalog.text("log.show")
        )
        self.clear_log_button.setText(self.catalog.text("log.clear"))
        self._update_status()

    def _credentials(self) -> dict[str, object]:
        return self.credentials_repository.load() if self.credentials_repository else {}

    def _start_review(self, request: ReviewRequest) -> None:
        credentials = self._credentials()
        gel = credentials.get("gelbooru", {})
        if "gelbooru" in request.sites and (
            not isinstance(gel, dict) or not gel.get("user_id") or not gel.get("api_key")
        ):
            self.review_page.state.setText(self.catalog.text("review.credentials_missing"))
            return
        commands = build_review_commands(
            request, self.project_root, self.python_executable, credentials
        )
        self.review_page.set_running(True)
        self.log(self.catalog.text("review.log_start", count=len(request.queries)))
        try:
            self.review_controller.start(commands)
        except RuntimeError as exc:
            self.review_page.set_running(False)
            self.log(str(exc))

    def _stop_review(self) -> None:
        self.review_page.state.setText(self.catalog.text("review.stopping"))
        if self.count_worker and self.count_worker.isRunning():
            self.count_worker.requestInterruption()
        else:
            self.review_controller.stop()

    def _review_site_started(self, site: str) -> None:
        self.log(self.catalog.text("review.site_started", site=site))

    def _review_output(self, chunk: str) -> None:
        for line in chunk.splitlines():
            if line.strip():
                self.log(translate_legacy_log(line, self.catalog.code))

    def _review_finished(self, success: bool, outputs: list[str]) -> None:
        self.review_page.set_running(False)
        self.review_page.state.setText(
            self.catalog.text("review.finished" if success else "review.interrupted")
        )
        if success and outputs:
            self.log(self.catalog.text("review.outputs", paths="; ".join(outputs)))

    def _count_queries(self, queries: tuple[str, ...]) -> None:
        gel = self._credentials().get("gelbooru", {})
        if not isinstance(gel, dict) or not gel.get("user_id") or not gel.get("api_key"):
            self.review_page.state.setText(self.catalog.text("review.credentials_missing"))
            return
        self.review_page.set_running(True)
        self.count_worker = ReviewCountWorker(
            queries, str(gel["user_id"]), str(gel["api_key"])
        )
        self.count_worker.progress.connect(self.review_page.set_count_progress)
        self.count_worker.completed.connect(self._count_finished)
        self.count_worker.start()

    def _count_finished(self, results: list, errors: list) -> None:
        self.review_page.set_running(False)
        for query, count in results:
            self.log(self.catalog.text("review.count_result", query=query, count=count))
        if results:
            total = sum(int(count) for _query, count in results)
            self.review_page.state.setText(
                self.catalog.text("review.count_total", count=len(results), total=total)
            )
        for query, error in errors:
            self.log(self.catalog.text("review.count_error", query=query, error=error))
        if self.count_worker:
            self.count_worker.deleteLater()
            self.count_worker = None

    def _start_tagging(self, request) -> None:
        gel = self._credentials().get("gelbooru", {})
        if not isinstance(gel, dict) or not gel.get("user_id") or not gel.get("api_key"):
            self.tagging_page.state.setText(self.catalog.text("review.credentials_missing"))
            return
        self.tagging_page.set_running(True)
        self.log(self.catalog.text("tagging.log_start", query=request.query))
        self.tagging_worker = TaggingWorker(request, str(gel["user_id"]), str(gel["api_key"]))
        self.tagging_worker.progress.connect(self.tagging_page.set_progress)
        self.tagging_worker.completed.connect(self._tagging_finished)
        self.tagging_worker.start()

    def _stop_tagging(self) -> None:
        if self.tagging_worker:
            self.tagging_worker.requestInterruption()
            self.tagging_page.state.setText(self.catalog.text("tagging.stopping"))

    def _tagging_finished(
        self, posts: list, examined: int, next_page: int, reached_end: bool, error: str, stopped: bool
    ) -> None:
        self.tagging_page.set_running(False)
        self.tagging_page.spins["start"].setValue(max(1, next_page))
        self.tagging_page.show_results(posts)
        if error:
            self.tagging_page.state.setText(self.catalog.text("tagging.failed", error=error))
            self.log(self.catalog.text("tagging.failed", error=error))
        elif stopped:
            self.tagging_page.state.setText(self.catalog.text("tagging.stopped", examined=examined))
        elif reached_end and not posts:
            self.tagging_page.state.setText(self.catalog.text("tagging.end", examined=examined))
        else:
            self.tagging_page.state.setText(
                self.catalog.text("tagging.finished", examined=examined, retained=len(posts))
            )
        if self.tagging_worker:
            self.tagging_worker.deleteLater()
            self.tagging_worker = None

    def _cleanup_paths(self) -> tuple[Path, Path]:
        settings = self.settings_repository.load() if self.settings_repository else {}
        grabber = Path(str(settings.get("grabber_directory", "")))
        output = Path(str(settings.get("output_root", self.project_root / "var" / "results")))
        return grabber / "blacklist.txt", output

    def _start_cleanup(self, roots: tuple[Path, ...]) -> None:
        blacklist, output = self._cleanup_paths()
        if not blacklist.is_file():
            self.cleanup_page.state.setText(self.catalog.text("cleanup.blacklist_missing", path=blacklist))
            self.log(self.catalog.text("cleanup.blacklist_missing", path=blacklist))
            return
        self.cleanup_page.set_running(True)
        self.cleanup_matches = []
        self.log(self.catalog.text("cleanup.log_start", count=len(roots)))
        self.cleanup_worker = CleanupScanWorker(roots, blacklist, output)
        self.cleanup_worker.progress.connect(self.cleanup_page.set_progress)
        self.cleanup_worker.completed.connect(self._cleanup_finished)
        self.cleanup_worker.start()

    def _stop_cleanup(self) -> None:
        if self.cleanup_worker:
            self.cleanup_worker.requestInterruption()
            self.cleanup_page.state.setText(self.catalog.text("cleanup.stopping"))

    def _cleanup_finished(
        self, files: int, matches: list, report: str, ignored_compound: int,
        ignored_non_tag: int, error: str,
    ) -> None:
        self.cleanup_page.set_running(False)
        if error:
            self.cleanup_page.state.setText(self.catalog.text("cleanup.failed", error=error))
            self.log(self.catalog.text("cleanup.failed", error=error))
        else:
            self.cleanup_matches = matches
            self.cleanup_report = report
            self.cleanup_page.show_matches(matches)
            unique = len({match.path for match in matches})
            self.cleanup_page.state.setText(
                self.catalog.text("cleanup.finished", files=files, matches=unique)
            )
            self.log(self.catalog.text(
                "cleanup.report", path=report, compound=ignored_compound, non_tag=ignored_non_tag
            ))
        if self.cleanup_worker:
            self.cleanup_worker.deleteLater()
            self.cleanup_worker = None

    def _recycle_cleanup(self) -> None:
        paths = tuple(sorted({match.path for match in self.cleanup_matches}))
        if not paths:
            return
        size = sum(path.stat().st_size for path in paths if path.is_file())
        answer = QMessageBox.question(
            self,
            self.catalog.text("cleanup.confirm_title"),
            self.catalog.text(
                "cleanup.confirm", count=len(paths), size=size / (1024 * 1024), report=self.cleanup_report
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.log(self.catalog.text("cleanup.cancelled"))
            return
        self.cleanup_page.scan_button.setEnabled(False)
        self.cleanup_page.recycle_button.setEnabled(False)
        self.cleanup_page.state.setText(self.catalog.text("cleanup.recycling", count=len(paths)))
        self.recycle_worker = CleanupRecycleWorker(paths)
        self.recycle_worker.completed.connect(self._recycle_finished)
        self.recycle_worker.start()

    def _recycle_finished(self, success: bool, message: str) -> None:
        self.log(message)
        self.cleanup_page.scan_button.setEnabled(True)
        self.cleanup_page.recycle_button.setEnabled(not success)
        self.cleanup_page.state.setText(
            self.catalog.text("cleanup.recycle_done" if success else "cleanup.recycle_failed")
        )
        if success:
            self.cleanup_matches = []
        if self.recycle_worker:
            self.recycle_worker.deleteLater()
            self.recycle_worker = None

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
        self.status_label.setText(self.catalog.text("status.saved"))
        self.log(self.catalog.text("log.options_saved"))

    def toggle_log(self) -> None:
        visible = not self.log_view.isVisible()
        self.log_view.setVisible(visible)
        self.log_button.setText(self.catalog.text("log.hide" if visible else "log.show"))

    def log(self, message: str) -> None:
        self.log_view.appendPlainText(f"[{datetime.now():%H:%M:%S}] {message}")
