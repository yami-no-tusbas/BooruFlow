"""Workers for taxonomy persistence and authoritative wiki updates."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from time import perf_counter

from PySide6.QtCore import QObject, QThread, Signal

from booruflow.application.ports import SettingsRepository
from booruflow.application.taxonomy import TaxonomyRepository
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.task_manager import TaskManager

LOGGER = logging.getLogger(__name__)


class TaxonomySaveWorker(QThread):
    completed = Signal(str, str)

    def __init__(self, repository: TaxonomyRepository, document: dict) -> None:
        super().__init__()
        self.repository = repository
        self.document = document

    def run(self) -> None:
        try:
            backup = self.repository.save(self.document)
            self.completed.emit(str(backup or ""), "")
        except Exception as exc:  # noqa: BLE001 - worker boundary reports persistence failures
            self.completed.emit("", str(exc))


class WikiImportWorker(QThread):
    progress = Signal(str)
    completed = Signal(object, object, str, bool, int)

    def __init__(self, repository: TaxonomyRepository, document: dict) -> None:
        super().__init__()
        self.repository = repository
        self.document = document
        self.processed = 0

    def request_cancel(self) -> None:
        self.requestInterruption()

    def _progress(self, value: str) -> None:
        self.processed += 1
        self.progress.emit(value)

    def run(self) -> None:
        try:
            from booruflow.infrastructure.wiki_tag_importer import (
                WikiImportCancelled,
                import_catalogues,
            )

            imported = import_catalogues(
                progress=self._progress,
                cancelled=self.isInterruptionRequested,
            )
            preview, summary = self.repository.merged_preview(self.document, imported)
            self.completed.emit(preview, summary, "", False, self.processed)
        except WikiImportCancelled:
            self.completed.emit({}, {}, "", True, self.processed)
        except Exception as exc:  # noqa: BLE001 - worker boundary reports import failures
            self.completed.emit({}, {}, str(exc), False, self.processed)


class TagDetailsWorker(QThread):
    completed = Signal(int, object, bool, float)

    def __init__(
        self,
        generation: int,
        board: str,
        tag: str,
        cache_path,
        user_id: str = "",
        api_key: str = "",
        tag_database_path=None,
        wiki_url: str = "",
        force: bool = False,
    ) -> None:
        super().__init__()
        self.generation = generation
        self.board = board
        self.tag = tag
        self.cache_path = cache_path
        self.user_id = user_id
        self.api_key = api_key
        self.tag_database_path = tag_database_path
        self.wiki_url = wiki_url
        self.force = force

    def run(self) -> None:
        from booruflow.infrastructure.tag_details import fetch_tag_details

        started = perf_counter()
        try:
            details = fetch_tag_details(
                self.board,
                self.tag,
                self.cache_path,
                self.user_id,
                self.api_key,
                self.tag_database_path,
                self.wiki_url,
                force=self.force,
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            details = {
                "board": self.board, "tag": self.tag, "wiki_exists": None,
                "wiki_url": self.wiki_url, "errors": [str(exc)], "online": False,
            }
        self.completed.emit(self.generation, details, self.force, perf_counter() - started)


class OrganizationCoordinator(QObject):
    preview_ready = Signal(object, object)

    def __init__(
        self,
        project_root: Path,
        catalog: LanguageCatalog,
        page,
        repository: TaxonomyRepository,
        settings_repository: SettingsRepository | None,
        credentials: Callable[[], dict[str, object]],
        log: Callable[[str], None],
        task_manager: TaskManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.catalog = catalog
        self.page = page
        self.repository = repository
        self.settings_repository = settings_repository
        self.credentials = credentials
        self.log = log
        self.task_manager = task_manager
        self.task_id: str | None = None
        self.taxonomy_worker: TaxonomySaveWorker | WikiImportWorker | None = None
        self.details_generation = 0
        self.details_workers: list[TagDetailsWorker] = []

    def save(self, document: dict) -> None:
        if self.taxonomy_worker and self.taxonomy_worker.isRunning():
            return
        self.page.set_busy(True)
        self.page.page_status.set_state("working")
        self.page.page_status.set_busy(accessible_text=self.catalog.text("organization.saving"))
        if self.task_manager and not self.task_id:
            self.task_id = self.task_manager.start(
                "taxonomy_save", self.catalog.text("organization.saving")
            )
        self.page.state.setText(self.catalog.text("organization.saving"))
        self.taxonomy_worker = TaxonomySaveWorker(self.repository, document)
        self.taxonomy_worker.completed.connect(self.saved)
        self.taxonomy_worker.start()

    def saved(self, backup: str, error: str) -> None:
        self.page.set_busy(False)
        self.page.page_status.clear_progress()
        self.page.page_status.set_state("ready")
        if error:
            message = self.catalog.text("organization.failed", error=error)
            self.page.state.setText(message)
            self.log(message)
            self.page.page_status.show_message(message, timeout_ms=6_000)
            task_state = "failed"
        else:
            self.page.state.setText(self.catalog.text("organization.saved"))
            self.log(self.catalog.text("organization.backup", path=backup))
            self.page.page_status.show_message(
                self.catalog.text("organization.saved"), timeout_ms=5_000
            )
            task_state = "completed"
        if self.task_manager and self.task_id:
            self.task_manager.finish(self.task_id, task_state, self.page.state.text())
            self.task_id = None
        self._discard_taxonomy_worker()

    def load_details(self, board: str, tag: str, wiki_url: str = "") -> None:
        self.details_generation += 1
        from booruflow.infrastructure.tag_details import cached_tag_details

        cache_database = self.repository.database_path(board)
        cached = cached_tag_details(cache_database, board, tag)
        if cached is not None:
            LOGGER.debug("Wiki Browser cache hit: %s/%s", board, tag)
            self.page.show_tag_details(cached)
            self.page.set_details_busy(False)
            self.page.page_status.set_state("ready")
            self.page.page_status.show_message(
                self.catalog.text("organization.wiki_loaded_cache"), timeout_ms=4_000
            )
            return
        LOGGER.debug("Wiki Browser cache miss: %s/%s", board, tag)
        self._start_details(board, tag, wiki_url, force=False)

    def refresh_details(self, board: str, tag: str, wiki_url: str = "") -> None:
        if any(
            worker.isRunning() and worker.force and worker.board == board and worker.tag == tag
            for worker in self.details_workers
        ):
            return
        self.details_generation += 1
        self._start_details(board, tag, wiki_url, force=True)

    def _start_details(self, board: str, tag: str, wiki_url: str, *, force: bool) -> None:
        gelbooru = self.credentials().get("gelbooru", {})
        settings = self.settings_repository.load() if self.settings_repository else {}
        database_value = str(settings.get(f"{board}_database", ""))
        state = "refreshing_wiki" if force else "loading_wiki"
        self.page.set_details_busy(True)
        self.page.page_status.set_state(state)
        self.page.page_status.set_busy(accessible_text=self.catalog.text(f"organization.{state}"))
        LOGGER.debug("Wiki Browser fetch started: %s/%s force=%s", board, tag, force)
        worker = TagDetailsWorker(
            self.details_generation,
            board,
            tag,
            self.repository.database_path(board),
            str(gelbooru.get("user_id", "")) if isinstance(gelbooru, dict) else "",
            str(gelbooru.get("api_key", "")) if isinstance(gelbooru, dict) else "",
            Path(database_value) if database_value else None,
            wiki_url,
            force,
        )
        self.details_workers.append(worker)
        worker.completed.connect(self.details_ready)
        worker.finished.connect(lambda value=worker: self._discard_details_worker(value))
        worker.start()

    def details_ready(
        self, generation: int, details: dict, force: bool, duration: float
    ) -> None:
        LOGGER.debug(
            "Wiki Browser fetch finished: %s/%s force=%s duration=%.3fs",
            details.get("board", ""), details.get("tag", ""), force, duration,
        )
        if generation != self.details_generation:
            return
        self.page.set_details_busy(False)
        self.page.page_status.clear_progress()
        self.page.page_status.set_state("ready")
        self.page.show_tag_details(details)
        errors = details.get("errors", [])
        if details.get("refresh_failed"):
            self.page.page_status.show_message(
                self.catalog.text("organization.refresh_failed_cached"),
                timeout_ms=6_000,
                log=True,
            )
        elif details.get("refreshed"):
            self.page.page_status.show_message(
                self.catalog.text("organization.wiki_updated"), timeout_ms=5_000, log=True
            )
        elif details.get("wiki_exists") is False:
            self.page.page_status.show_message(
                self.catalog.text("organization.wiki_not_found"), timeout_ms=5_000
            )
        if errors:
            self.log(
                self.catalog.text(
                    "organization.details_offline_log",
                    tag=details.get("tag", ""),
                    error="; ".join(map(str, errors)),
                )
            )

    def update(self) -> None:
        if self.taxonomy_worker and self.taxonomy_worker.isRunning():
            return
        self.page.set_update_running(True)
        self.page.page_status.set_state("updating")
        self.page.page_status.set_busy(accessible_text=self.catalog.text("organization.updating"))
        if self.task_manager:
            self.task_id = self.task_manager.start(
                "taxonomy_update", self.catalog.text("organization.updating")
            )
        worker = WikiImportWorker(self.repository, self.page.document)
        worker.progress.connect(self.update_progress)
        worker.completed.connect(self.update_ready)
        self.taxonomy_worker = worker
        worker.start()

    def stop_update(self) -> None:
        worker = self.taxonomy_worker
        if not isinstance(worker, WikiImportWorker) or not worker.isRunning():
            return
        LOGGER.info("Wiki Browser update cancellation requested")
        worker.request_cancel()
        self.page.set_update_running(True, stopping=True)
        self.page.page_status.set_state("stopping")
        self.page.page_status.show_message(
            self.catalog.text("organization.stopping"), timeout_ms=0, log=True
        )

    def update_progress(self, value: str) -> None:
        self.log(str(value))
        if self.task_manager and self.task_id:
            self.task_manager.progress(self.task_id, 0, 0, "import", str(value))

    def update_ready(
        self, preview: dict, summary: dict, error: str, cancelled: bool, processed: int
    ) -> None:
        self.page.page_status.clear_progress()
        if cancelled:
            self.page.set_update_running(False)
            self.page.page_status.set_state("ready")
            message = self.catalog.text("organization.update_stopped")
            self.page.page_status.show_message(message, timeout_ms=5_000, log=True)
            self.log(self.catalog.text("organization.update_stopped_log", count=processed))
            if self.task_manager and self.task_id:
                self.task_manager.finish(self.task_id, "cancelled", message)
                self.task_id = None
            self._discard_taxonomy_worker()
            return
        if error:
            self.page.set_update_running(False)
            self.page.page_status.set_state("ready")
            message = self.catalog.text("organization.failed", error=error)
            self.log(message)
            self.page.page_status.show_message(message, timeout_ms=6_000)
            if self.task_manager and self.task_id:
                self.task_manager.finish(self.task_id, "failed", message)
                self.task_id = None
            self._discard_taxonomy_worker()
            return
        self._discard_taxonomy_worker()
        self.page.set_update_running(True, stopping=True)
        self.preview_ready.emit(preview, summary)

    def accept_preview(self, preview: dict) -> None:
        self.page.document = preview
        self.page.reload()
        self.save(preview)

    def cancel_preview(self) -> None:
        self.page.set_update_running(False)
        self.page.page_status.set_state("ready")
        message = self.catalog.text("organization.cancelled")
        self.page.page_status.show_message(
            message, timeout_ms=5_000
        )
        if self.task_manager and self.task_id:
            self.task_manager.finish(self.task_id, "cancelled", message)
            self.task_id = None

    def _discard_taxonomy_worker(self) -> None:
        if self.taxonomy_worker:
            self.taxonomy_worker.deleteLater()
            self.taxonomy_worker = None

    def _discard_details_worker(self, worker: TagDetailsWorker) -> None:
        if worker in self.details_workers:
            self.details_workers.remove(worker)
        worker.deleteLater()
