"""Workers for taxonomy persistence and authoritative wiki updates."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from booruflow.application.ports import SettingsRepository
from booruflow.application.taxonomy import TaxonomyRepository
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.task_manager import TaskManager


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
    completed = Signal(object, object, str)

    def __init__(self, repository: TaxonomyRepository, document: dict) -> None:
        super().__init__()
        self.repository = repository
        self.document = document

    def run(self) -> None:
        try:
            from booruflow.infrastructure.wiki_tag_importer import import_catalogues

            imported = import_catalogues(progress=self.progress.emit)
            preview, summary = self.repository.merged_preview(self.document, imported)
            self.completed.emit(preview, summary, "")
        except Exception as exc:  # noqa: BLE001 - worker boundary reports import failures
            self.completed.emit({}, {}, str(exc))


class TagDetailsWorker(QThread):
    completed = Signal(int, object)

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

    def run(self) -> None:
        from booruflow.infrastructure.tag_details import fetch_tag_details

        details = fetch_tag_details(
            self.board,
            self.tag,
            self.cache_path,
            self.user_id,
            self.api_key,
            self.tag_database_path,
            self.wiki_url,
        )
        self.completed.emit(self.generation, details)


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
        if error:
            message = self.catalog.text("organization.failed", error=error)
            self.page.state.setText(message)
            self.log(message)
            task_state = "failed"
        else:
            self.page.state.setText(self.catalog.text("organization.saved"))
            self.log(self.catalog.text("organization.backup", path=backup))
            task_state = "completed"
        if self.task_manager and self.task_id:
            self.task_manager.finish(self.task_id, task_state, self.page.state.text())
            self.task_id = None
        self._discard_taxonomy_worker()

    def load_details(self, board: str, tag: str, wiki_url: str = "") -> None:
        self.details_generation += 1
        gelbooru = self.credentials().get("gelbooru", {})
        settings = self.settings_repository.load() if self.settings_repository else {}
        database_value = str(settings.get(f"{board}_database", ""))
        worker = TagDetailsWorker(
            self.details_generation,
            board,
            tag,
            self.project_root / "var" / "cache" / "tag_details.json",
            str(gelbooru.get("user_id", "")) if isinstance(gelbooru, dict) else "",
            str(gelbooru.get("api_key", "")) if isinstance(gelbooru, dict) else "",
            Path(database_value) if database_value else None,
            wiki_url,
        )
        self.details_workers.append(worker)
        worker.completed.connect(self.details_ready)
        worker.finished.connect(lambda value=worker: self._discard_details_worker(value))
        worker.start()

    def details_ready(self, generation: int, details: dict) -> None:
        if generation != self.details_generation:
            return
        self.page.show_tag_details(details)
        errors = details.get("errors", [])
        if errors and not details.get("online"):
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
        self.page.set_busy(True)
        if self.task_manager:
            self.task_id = self.task_manager.start(
                "taxonomy_update", self.catalog.text("organization.updating")
            )
        self.page.state.setText(self.catalog.text("organization.updating"))
        worker = WikiImportWorker(self.repository, self.page.document)
        worker.progress.connect(self.update_progress)
        worker.completed.connect(self.update_ready)
        self.taxonomy_worker = worker
        worker.start()

    def update_progress(self, value: str) -> None:
        self.page.state.setText(str(value))
        self.log(str(value))
        if self.task_manager and self.task_id:
            self.task_manager.progress(self.task_id, 0, 0, "import", str(value))

    def update_ready(self, preview: dict, summary: dict, error: str) -> None:
        if error:
            self.page.set_busy(False)
            message = self.catalog.text("organization.failed", error=error)
            self.page.state.setText(message)
            self.log(message)
            if self.task_manager and self.task_id:
                self.task_manager.finish(self.task_id, "failed", message)
                self.task_id = None
            self._discard_taxonomy_worker()
            return
        self._discard_taxonomy_worker()
        self.preview_ready.emit(preview, summary)

    def accept_preview(self, preview: dict) -> None:
        self.page.document = preview
        self.page.reload()
        self.save(preview)

    def cancel_preview(self) -> None:
        self.page.set_busy(False)
        self.page.state.setText(self.catalog.text("organization.cancelled"))
        if self.task_manager and self.task_id:
            self.task_manager.finish(self.task_id, "cancelled", self.page.state.text())
            self.task_id = None

    def _discard_taxonomy_worker(self) -> None:
        if self.taxonomy_worker:
            self.taxonomy_worker.deleteLater()
            self.taxonomy_worker = None

    def _discard_details_worker(self, worker: TagDetailsWorker) -> None:
        if worker in self.details_workers:
            self.details_workers.remove(worker)
        worker.deleteLater()
