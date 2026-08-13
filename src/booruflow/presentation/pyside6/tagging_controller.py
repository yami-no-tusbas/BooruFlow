"""Qt worker for Gelbooru tagging review."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal

from booruflow.application.tagging import TaggingRequest
from booruflow.infrastructure.gelbooru_tagging import GelbooruTaggingScanner
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.task_manager import TaskManager


class TaggingWorker(QThread):
    progress = Signal(int, int, int, int, int)
    completed = Signal(list, int, int, bool, str, bool)

    def __init__(self, request: TaggingRequest, user_id: str, api_key: str) -> None:
        super().__init__()
        self.request = request
        self.user_id = user_id
        self.api_key = api_key

    def run(self) -> None:
        try:
            posts, examined, next_page, reached_end = GelbooruTaggingScanner().scan(
                self.request,
                self.user_id,
                self.api_key,
                cancelled=self.isInterruptionRequested,
                progress=lambda *values: self.progress.emit(*values),
            )
            self.completed.emit(
                posts, examined, next_page, reached_end, "", self.isInterruptionRequested()
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary reports scanner failures
            self.completed.emit([], 0, self.request.start_page, False, str(exc), False)


class TaggingController(QObject):
    def __init__(
        self,
        catalog: LanguageCatalog,
        page,
        credentials: Callable[[], dict[str, object]],
        log: Callable[[str], None],
        task_manager: TaskManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.page = page
        self.credentials = credentials
        self.log = log
        self.task_manager = task_manager
        self.task_id: str | None = None
        self.worker: TaggingWorker | None = None

    def start(self, request: TaggingRequest) -> None:
        gelbooru = self.credentials().get("gelbooru", {})
        if (
            not isinstance(gelbooru, dict)
            or not gelbooru.get("user_id")
            or not gelbooru.get("api_key")
        ):
            self.page.state.setText(self.catalog.text("review.credentials_missing"))
            return
        self.page.set_running(True)
        if self.task_manager:
            self.task_id = self.task_manager.start(
                "tagging", self.catalog.text("nav.tagging"), request.query
            )
        self.log(self.catalog.text("tagging.log_start", query=request.query))
        self.worker = TaggingWorker(
            request,
            str(gelbooru["user_id"]),
            str(gelbooru["api_key"]),
        )
        self.worker.progress.connect(self.progress)
        self.worker.completed.connect(self.finished)
        self.worker.start()

    def stop(self) -> None:
        if self.worker:
            self.worker.requestInterruption()
            self.page.state.setText(self.catalog.text("tagging.stopping"))

    def progress(self, page: int, current: int, total: int, examined: int, retained: int) -> None:
        self.page.set_progress(page, current, total, examined, retained)
        if self.task_manager and self.task_id:
            self.task_manager.progress(
                self.task_id, current, total, f"page {page}", f"{examined} / {retained}"
            )

    def finished(
        self,
        posts: list,
        examined: int,
        next_page: int,
        reached_end: bool,
        error: str,
        stopped: bool,
    ) -> None:
        self.page.set_running(False)
        self.page.spins["start"].setValue(max(1, next_page))
        self.page.show_results(posts)
        if error:
            message = self.catalog.text("tagging.failed", error=error)
            self.page.state.setText(message)
            self.log(message)
            task_state = "failed"
        elif stopped:
            self.page.state.setText(self.catalog.text("tagging.stopped", examined=examined))
            task_state = "cancelled"
        elif reached_end and not posts:
            self.page.state.setText(self.catalog.text("tagging.end", examined=examined))
            task_state = "completed"
        else:
            self.page.state.setText(
                self.catalog.text("tagging.finished", examined=examined, retained=len(posts))
            )
            task_state = "completed"
        if self.task_manager and self.task_id:
            self.task_manager.finish(self.task_id, task_state, self.page.state.text())
            self.task_id = None
        if self.worker:
            self.worker.deleteLater()
            self.worker = None
