"""Main window, localization and controllers for BooruFlow."""

from __future__ import annotations

import sys
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QProcess, QSize, Qt, QTimer
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
from booruflow.application.grabber_batches import GrabberSessionStore
from booruflow.application.taxonomy import TaxonomyRepository
from booruflow.infrastructure.localization import LanguageCatalog, translate_legacy_log
from booruflow.presentation.pyside6.icons import navigation_icon
from booruflow.presentation.pyside6.cleanup_controller import CleanupRecycleWorker, CleanupScanWorker
from booruflow.presentation.pyside6.cleanup_page import CleanupPage
from booruflow.presentation.pyside6.grabber_page import GrabberPage
from booruflow.presentation.pyside6.organization_controller import (
    TagDetailsWorker, TaxonomySaveWorker, WikiImportWorker,
)
from booruflow.presentation.pyside6.organization_page import OrganizationPage
from booruflow.presentation.pyside6.options_page import OptionsPage
from booruflow.presentation.pyside6.pages import DashboardPage
from booruflow.presentation.pyside6.review_controller import (
    ReviewCountWorker,
    ReviewAutocompleteWorker,
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
        self.autocomplete_workers: list[ReviewAutocompleteWorker] = []
        self.tagging_worker: TaggingWorker | None = None
        self.cleanup_worker: CleanupScanWorker | None = None
        self.recycle_worker: CleanupRecycleWorker | None = None
        self.cleanup_matches: list = []
        self.cleanup_report = ""
        self.grabber_state: dict | None = None
        self.taxonomy_repository = TaxonomyRepository(
            self.project_root / "data" / "taxonomy" / "tag_organization.json",
            self.project_root / "data" / "databases",
        )
        self.taxonomy_worker: TaxonomySaveWorker | WikiImportWorker | None = None
        self.tag_details_generation = 0
        self.tag_details_workers: list[TagDetailsWorker] = []
        self.database_process = QProcess(self)
        self.database_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.database_process.readyReadStandardOutput.connect(self._database_output)
        self.database_process.finished.connect(self._database_finished)
        self.database_site = ""
        self.active_review_request: ReviewRequest | None = None
        self.review_next_page: int | None = None
        self.review_retained = 0
        self.review_summary: list[str] = []
        self.review_output_buffer = ""
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
        self.review_page.autocomplete_requested.connect(self._autocomplete_review)
        self.review_page.grabber_tags_requested.connect(self._review_results_to_grabber)
        self.pages.addWidget(self.review_page)
        self.tagging_page = TaggingPage(catalog, settings)
        self.tagging_page.start_requested.connect(self._start_tagging)
        self.tagging_page.stop_requested.connect(self._stop_tagging)
        self.pages.addWidget(self.tagging_page)
        self.organization_page = OrganizationPage(catalog, self.taxonomy_repository.load())
        self.organization_page.save_requested.connect(self._save_taxonomy)
        self.organization_page.update_requested.connect(self._update_taxonomy)
        self.organization_page.review_tags_requested.connect(self._review_organization_tags)
        self.organization_page.tag_details_requested.connect(self._load_tag_details)
        self.pages.addWidget(self.organization_page)
        self.cleanup_page = CleanupPage(catalog)
        self.cleanup_page.scan_requested.connect(self._start_cleanup)
        self.cleanup_page.stop_requested.connect(self._stop_cleanup)
        self.cleanup_page.recycle_requested.connect(self._recycle_cleanup)
        self.pages.addWidget(self.cleanup_page)
        options = OptionsPage(catalog, settings, credentials)
        options.save_requested.connect(self._save_options)
        options.language_changed.connect(self.change_language)
        options.database_update_requested.connect(self._update_database)
        self.options_page = options
        self.pages.addWidget(options)
        self.grabber_page = GrabberPage(catalog, settings, capabilities.grabber.available)
        self.grabber_page.create_requested.connect(self._create_grabber_session)
        self.grabber_page.load_requested.connect(self._load_grabber_session)
        self.grabber_page.launch_requested.connect(self._launch_grabber)
        self.grabber_page.previous_requested.connect(self._previous_grabber_batch)
        self.pages.addWidget(self.grabber_page)
        self.grabber_process = QProcess(self)
        self.grabber_process.finished.connect(self._grabber_finished)

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
        self._load_grabber_session(silent=True)
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
        if self.settings_repository:
            saved_settings = self.settings_repository.load()
            saved_settings["review_auto_continue"] = request.auto_continue
            self.settings_repository.save(saved_settings)
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
        self.active_review_request = request
        self.review_next_page = None
        self.review_retained = 0
        self.review_summary = []
        self.review_output_buffer = ""
        self.review_page.show_results([])
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
        self.review_output_buffer += chunk
        parts = self.review_output_buffer.splitlines(keepends=True)
        self.review_output_buffer = ""
        for part in parts:
            if part.endswith(("\n", "\r")):
                self._review_line(part.rstrip("\r\n"))
            else:
                self.review_output_buffer = part

    def _review_line(self, line: str) -> None:
        if not line.strip():
            return
        translated = translate_legacy_log(line, self.catalog.code)
        self.log(translated)
        next_match = re.search(r"prochain d[ée]part page\s+(\d+)", line, re.IGNORECASE)
        if next_match:
            self.review_next_page = int(next_match.group(1))
        retained_match = re.search(r"(\d+)\s+.+?\s+retenus(?:\s+sur\s+(\d+)\s+posts cumul[ée]s)?", line, re.IGNORECASE)
        e621_match = re.search(r"^(\d+)\s+.+?\s+e621 retenus", line.strip(), re.IGNORECASE)
        if e621_match:
            self.review_retained += int(e621_match.group(1)); self.review_summary.append(translated)
        elif retained_match and ("posts cumul" in line or "retenus sur" in line):
            self.review_retained += int(retained_match.group(1)); self.review_summary.append(translated)
        elif line.lstrip().startswith(("Filtrage :", "Bilan ")):
            self.review_summary.append(translated)

    def _review_finished(self, success: bool, outputs: list[str]) -> None:
        if self.review_output_buffer:
            self._review_line(self.review_output_buffer)
            self.review_output_buffer = ""
        self.review_page.set_running(False)
        request = self.active_review_request
        if success and self.review_next_page:
            self.review_page.start_page.setValue(int(self.review_next_page))
        can_continue = bool(
            success and request and request.auto_continue and request.sites == ("gelbooru",)
            and self.review_retained == 0 and self.review_next_page
            and self.review_next_page > request.start_page
        )
        if can_continue:
            next_page = int(self.review_next_page)
            self.review_page.show_summary(self.review_summary, completed=False)
            self.review_page.state.setText(
                self.review_page.state.text() + "\n" + self.catalog.text("review.continuing", page=next_page)
            )
            QTimer.singleShot(350, lambda: self._continue_review(request, next_page))
            return
        entries = self._review_result_entries(request) if success and request else []
        self.review_page.show_results(entries)
        if success:
            self.review_page.show_summary(self.review_summary, completed=True)
        else:
            self.review_page.state.setText(self.catalog.text("review.interrupted"))
        if success and outputs:
            self.log(self.catalog.text("review.outputs", paths="; ".join(outputs)))

    def _continue_review(self, request: ReviewRequest, next_page: int) -> None:
        self.review_page.start_page.setValue(next_page)
        self._start_review(replace(request, start_page=next_page))

    def _review_result_entries(self, request: ReviewRequest) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for site in request.sites:
            directory = request.output_root / request.entity_type / site
            for path in directory.glob("*_candidats_uniques.txt"):
                try:
                    values = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
                except OSError:
                    continue
                for value in values:
                    entry = (site, value.strip())
                    if entry[1] and entry not in seen:
                        seen.add(entry); entries.append(entry)
        return entries

    def _review_results_to_grabber(self, entries: tuple[tuple[str, str], ...]) -> None:
        self.grabber_page.tags.setPlainText("\n".join(f"{site}\t{tag}" for site, tag in entries))
        self.navigate_to(6)
        self.log(self.catalog.text("review.sent_grabber", count=len(entries)))

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

    def _autocomplete_review(self, token: str) -> None:
        request_sites = tuple(self.review_page.site.currentData())
        settings = self.settings_repository.load() if self.settings_repository else {}
        candidates = (
            ("Gelbooru", Path(str(settings.get("gelbooru_database", "")))),
            ("e621", Path(str(settings.get("e621_database", "")))),
        )
        databases = tuple((name, path) for name, path in candidates if name.casefold() in request_sites and path.is_file())
        if not databases:
            return
        worker = ReviewAutocompleteWorker(token, databases)
        self.autocomplete_workers.append(worker)
        worker.completed.connect(self.review_page.show_suggestions)
        worker.finished.connect(lambda value=worker: self._discard_autocomplete(value))
        worker.start()

    def _discard_autocomplete(self, worker: ReviewAutocompleteWorker) -> None:
        if worker in self.autocomplete_workers:
            self.autocomplete_workers.remove(worker)
        worker.deleteLater()

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

    def _grabber_store(self) -> GrabberSessionStore | None:
        settings = self.settings_repository.load() if self.settings_repository else {}
        directory = Path(str(settings.get("grabber_directory", "")).strip())
        if not (directory / "Grabber.exe").is_file():
            self.grabber_page.state.setText(self.catalog.text("grabber.missing", path=directory))
            return None
        return GrabberSessionStore(directory)

    @staticmethod
    def _tag_file(path: Path) -> set[str]:
        try:
            return {line.strip() for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines() if line.strip()}
        except OSError:
            return set()

    def _create_grabber_session(self, request) -> None:
        store = self._grabber_store()
        if not store:
            return
        gel = self._credentials().get("gelbooru", {})
        needs_gel = any(site == "gelbooru" for site, _tag in request.entries)
        if needs_gel and (not isinstance(gel, dict) or not gel.get("user_id") or not gel.get("api_key")):
            self.grabber_page.state.setText(self.catalog.text("review.credentials_missing")); return
        unavailable = self._tag_file(store.directory / "blacklist.txt") | self._tag_file(store.directory / "ignore.txt")
        try:
            self.grabber_state, skipped = store.create(
                request,
                str(gel.get("user_id", "")) if isinstance(gel, dict) else "",
                str(gel.get("api_key", "")) if isinstance(gel, dict) else "",
                unavailable,
            )
        except (OSError, ValueError) as exc:
            self.grabber_page.state.setText(self.catalog.text("grabber.invalid", error=exc)); return
        self.grabber_page.show_session(self.grabber_state)
        self.log(self.catalog.text("grabber.created", batches=len(self.grabber_state["files"]), tags=self.grabber_state["total_tags"], skipped=skipped))

    def _load_grabber_session(self, silent: bool = False) -> None:
        store = self._grabber_store()
        if not store:
            return
        self.grabber_state = store.load()
        self.grabber_page.show_session(self.grabber_state)
        if self.grabber_state and not silent:
            self.log(self.catalog.text("grabber.loaded", path=self.grabber_state.get("session_dir", "")))

    def _launch_grabber(self) -> None:
        store = self._grabber_store()
        if not store or not self.grabber_state or self.grabber_process.state() != QProcess.ProcessState.NotRunning:
            return
        try:
            store.activate(self.grabber_state)
        except OSError as exc:
            self.grabber_page.state.setText(self.catalog.text("grabber.invalid", error=exc)); return
        self.grabber_process.setWorkingDirectory(str(store.directory))
        self.grabber_process.start(str(store.directory / "Grabber.exe"), [])
        self.grabber_page.state.setText(self.catalog.text("grabber.running"))
        self.log(self.catalog.text("grabber.started", batch=int(self.grabber_state.get("current", 0)) + 1))

    def _grabber_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        store = self._grabber_store()
        if not store or not self.grabber_state:
            return
        if exit_code != 0:
            self.grabber_page.state.setText(self.catalog.text("grabber.failed", code=exit_code)); return
        try:
            self.grabber_state, remaining = store.finish_current_if_empty(self.grabber_state)
        except (OSError, ValueError, TypeError) as exc:
            self.grabber_page.state.setText(self.catalog.text("grabber.invalid", error=exc)); return
        self.grabber_page.show_session(self.grabber_state)
        if remaining:
            self.log(self.catalog.text("grabber.paused", count=remaining))
        elif int(self.grabber_state.get("current", 0)) < len(self.grabber_state.get("files", [])):
            self.log(self.catalog.text("grabber.next"))
            self._launch_grabber()
        else:
            self.log(self.catalog.text("grabber.session_complete"))

    def _previous_grabber_batch(self) -> None:
        store = self._grabber_store()
        if store and self.grabber_state:
            self.grabber_state = store.previous(self.grabber_state)
            self.grabber_page.show_session(self.grabber_state)
            self.log(self.catalog.text("grabber.previous_done"))

    def _save_taxonomy(self, document: dict) -> None:
        if self.taxonomy_worker and self.taxonomy_worker.isRunning():
            return
        self.organization_page.set_busy(True)
        self.organization_page.state.setText(self.catalog.text("organization.saving"))
        self.taxonomy_worker = TaxonomySaveWorker(self.taxonomy_repository, document)
        self.taxonomy_worker.completed.connect(self._taxonomy_saved)
        self.taxonomy_worker.start()

    def _review_organization_tags(self, tags: tuple[str, ...]) -> None:
        self.review_page.queries.setPlainText("\n".join(tags))
        board = str(self.organization_page.board.currentData())
        index = self.review_page.site.findData((board,))
        if index >= 0: self.review_page.site.setCurrentIndex(index)
        self.navigate_to(1)
        self.log(self.catalog.text("organization.sent_review", count=len(tags)))

    def _load_tag_details(self, board: str, tag: str, wiki_url: str = "") -> None:
        self.tag_details_generation += 1
        gel = self._credentials().get("gelbooru", {})
        settings = self.settings_repository.load() if self.settings_repository else {}
        database_value = str(settings.get(f"{board}_database", ""))
        worker = TagDetailsWorker(
            self.tag_details_generation,
            board,
            tag,
            self.project_root / "var" / "cache" / "tag_details.json",
            str(gel.get("user_id", "")) if isinstance(gel, dict) else "",
            str(gel.get("api_key", "")) if isinstance(gel, dict) else "",
            Path(database_value) if database_value else None,
            wiki_url,
        )
        self.tag_details_workers.append(worker)
        worker.completed.connect(self._tag_details_ready)
        worker.finished.connect(lambda value=worker: self._discard_tag_details_worker(value))
        worker.start()

    def _tag_details_ready(self, generation: int, details: dict) -> None:
        if generation != self.tag_details_generation:
            return
        self.organization_page.show_tag_details(details)
        errors = details.get("errors", [])
        if errors and not details.get("online"):
            self.log(self.catalog.text(
                "organization.details_offline_log", tag=details.get("tag", ""), error="; ".join(map(str, errors))
            ))

    def _discard_tag_details_worker(self, worker: TagDetailsWorker) -> None:
        if worker in self.tag_details_workers:
            self.tag_details_workers.remove(worker)
        worker.deleteLater()

    def _taxonomy_saved(self, backup: str, error: str) -> None:
        self.organization_page.set_busy(False)
        if error:
            self.organization_page.state.setText(self.catalog.text("organization.failed", error=error))
            self.log(self.catalog.text("organization.failed", error=error))
        else:
            self.organization_page.state.setText(self.catalog.text("organization.saved"))
            self.log(self.catalog.text("organization.backup", path=backup))
        if self.taxonomy_worker:
            self.taxonomy_worker.deleteLater(); self.taxonomy_worker = None

    def _update_taxonomy(self) -> None:
        if self.taxonomy_worker and self.taxonomy_worker.isRunning():
            return
        self.organization_page.set_busy(True)
        self.organization_page.state.setText(self.catalog.text("organization.updating"))
        worker = WikiImportWorker(self.taxonomy_repository, self.organization_page.document)
        worker.progress.connect(lambda value: self.organization_page.state.setText(str(value)))
        worker.progress.connect(lambda value: self.log(str(value)))
        worker.completed.connect(self._taxonomy_update_ready)
        self.taxonomy_worker = worker
        worker.start()

    def _taxonomy_update_ready(self, preview: dict, summary: dict, error: str) -> None:
        if error:
            self.organization_page.set_busy(False)
            self.organization_page.state.setText(self.catalog.text("organization.failed", error=error))
            self.log(self.catalog.text("organization.failed", error=error))
            if self.taxonomy_worker: self.taxonomy_worker.deleteLater(); self.taxonomy_worker = None
            return
        answer = QMessageBox.question(
            self,
            self.catalog.text("organization.update"),
            self.catalog.text(
                "organization.update_confirm", total=summary.get("total", 0),
                added=summary.get("added", 0), removed=summary.get("removed", 0),
            ),
        )
        if self.taxonomy_worker: self.taxonomy_worker.deleteLater(); self.taxonomy_worker = None
        if answer != QMessageBox.StandardButton.Yes:
            self.organization_page.set_busy(False)
            self.organization_page.state.setText(self.catalog.text("organization.cancelled"))
            return
        self.organization_page.document = preview
        self.organization_page.reload()
        self._save_taxonomy(preview)

    def _update_database(self, site: str, destination: str) -> None:
        if self.database_process.state() != QProcess.ProcessState.NotRunning:
            self.log(self.catalog.text("options.database_already_running")); return
        path = Path(destination)
        if not destination:
            path = self.project_root / "data" / "databases" / ("gelbooru_tags.db" if site == "gelbooru" else "e621_tags.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        environment = self.database_process.processEnvironment()
        if environment.isEmpty():
            from PySide6.QtCore import QProcessEnvironment
            environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        if site == "gelbooru":
            gel = self._credentials().get("gelbooru", {})
            if not isinstance(gel, dict) or not gel.get("user_id") or not gel.get("api_key"):
                self.options_page.database_status.setText(self.catalog.text("review.credentials_missing")); return
            environment.insert("GELBOORU_USER_ID", str(gel["user_id"]))
            environment.insert("GELBOORU_API_KEY", str(gel["api_key"]))
            environment.insert("GELBOORU_TAG_DB", str(path))
            arguments = ("-u", str(self.project_root / "legacy" / "gelbooru_tags_updater.py"))
        else:
            arguments = (
                "-u", str(self.project_root / "legacy" / "e621_tags_importer.py"),
                "--db", str(path), "--cache-dir", str(self.project_root / "var" / "cache" / "e621_exports"),
            )
        self.database_site = site
        self.database_process.setProcessEnvironment(environment)
        self.database_process.setWorkingDirectory(str(self.project_root))
        self.options_page.set_database_running(True, site)
        self.log(self.catalog.text("options.database_start", site=site, path=path))
        self.database_process.start(self.python_executable, list(arguments))

    def _database_output(self) -> None:
        chunk = bytes(self.database_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in chunk.splitlines():
            if line.strip(): self.log(translate_legacy_log(line, self.catalog.code))

    def _database_finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        self._database_output()
        self.options_page.set_database_running(False)
        key = "options.database_finished" if code == 0 else "options.database_failed"
        message = self.catalog.text(key, site=self.database_site, code=code)
        self.options_page.database_status.setText(message); self.log(message)

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
