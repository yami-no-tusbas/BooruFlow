"""Qt controller for recoverable Imgbrd-Grabber sessions."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QProcess

from booruflow.application.grabber_batches import GrabberSessionStore
from booruflow.application.ports import SettingsRepository
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.task_manager import TaskManager


class GrabberController(QObject):
    def __init__(
        self,
        catalog: LanguageCatalog,
        page,
        settings_repository: SettingsRepository | None,
        credentials: Callable[[], dict[str, object]],
        log: Callable[[str], None],
        task_manager: TaskManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.page = page
        self.settings_repository = settings_repository
        self.credentials = credentials
        self.log = log
        self.task_manager = task_manager
        self.task_id: str | None = None
        self.state: dict | None = None
        self.process = QProcess(self)
        self.process.finished.connect(self.finished)

    def store(self) -> GrabberSessionStore | None:
        settings = self.settings_repository.load() if self.settings_repository else {}
        directory = Path(str(settings.get("grabber_directory", "")).strip())
        if not (directory / "Grabber.exe").is_file():
            self.page.state.setText(self.catalog.text("grabber.missing", path=directory))
            return None
        return GrabberSessionStore(directory)

    @staticmethod
    def tag_file(path: Path) -> set[str]:
        try:
            return {
                line.strip()
                for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
                if line.strip()
            }
        except OSError:
            return set()

    def create(self, request) -> None:
        store = self.store()
        if not store:
            return
        gelbooru = self.credentials().get("gelbooru", {})
        needs_gelbooru = any(site == "gelbooru" for site, _tag in request.entries)
        if needs_gelbooru and (
            not isinstance(gelbooru, dict)
            or not gelbooru.get("user_id")
            or not gelbooru.get("api_key")
        ):
            self.page.state.setText(self.catalog.text("review.credentials_missing"))
            return
        unavailable = self.tag_file(store.directory / "blacklist.txt") | self.tag_file(
            store.directory / "ignore.txt"
        )
        try:
            self.state, skipped = store.create(
                request,
                str(gelbooru.get("user_id", "")) if isinstance(gelbooru, dict) else "",
                str(gelbooru.get("api_key", "")) if isinstance(gelbooru, dict) else "",
                unavailable,
            )
        except (OSError, ValueError) as exc:
            self.page.state.setText(self.catalog.text("grabber.invalid", error=exc))
            return
        self.page.show_session(self.state)
        self.log(
            self.catalog.text(
                "grabber.created",
                batches=len(self.state["files"]),
                tags=self.state["total_tags"],
                skipped=skipped,
            )
        )

    def load(self, silent: bool = False) -> None:
        store = self.store()
        if not store:
            return
        self.state = store.load()
        self.page.show_session(self.state)
        if self.state and not silent:
            self.log(self.catalog.text("grabber.loaded", path=self.state.get("session_dir", "")))

    def launch(self) -> None:
        store = self.store()
        if not store or not self.state or self.process.state() != QProcess.ProcessState.NotRunning:
            return
        try:
            store.activate(self.state)
        except OSError as exc:
            self.page.state.setText(self.catalog.text("grabber.invalid", error=exc))
            return
        self.process.setWorkingDirectory(str(store.directory))
        if self.task_manager and not self.task_id:
            self.task_id = self.task_manager.start(
                "grabber", self.catalog.text("nav.grabber"), str(self.state.get("session_dir", ""))
            )
        if self.task_manager and self.task_id:
            self.task_manager.progress(
                self.task_id,
                int(self.state.get("current", 0)),
                len(self.state.get("files", [])),
                "batch",
            )
        self.process.start(str(store.directory / "Grabber.exe"), [])
        self.page.state.setText(self.catalog.text("grabber.running"))
        self.log(self.catalog.text("grabber.started", batch=int(self.state.get("current", 0)) + 1))

    def finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        store = self.store()
        if not store or not self.state:
            return
        if exit_code != 0:
            self.page.state.setText(self.catalog.text("grabber.failed", code=exit_code))
            if self.task_manager and self.task_id:
                self.task_manager.finish(self.task_id, "failed", self.page.state.text())
                self.task_id = None
            return
        try:
            self.state, remaining = store.finish_current_if_empty(self.state)
        except (OSError, ValueError, TypeError) as exc:
            self.page.state.setText(self.catalog.text("grabber.invalid", error=exc))
            if self.task_manager and self.task_id:
                self.task_manager.finish(self.task_id, "failed", self.page.state.text())
                self.task_id = None
            return
        self.page.show_session(self.state)
        if remaining:
            self.log(self.catalog.text("grabber.paused", count=remaining))
            if self.task_manager and self.task_id:
                self.task_manager.progress(
                    self.task_id,
                    int(self.state.get("current", 0)),
                    len(self.state.get("files", [])),
                    "review",
                    self.catalog.text("grabber.paused", count=remaining),
                )
        elif int(self.state.get("current", 0)) < len(self.state.get("files", [])):
            self.log(self.catalog.text("grabber.next"))
            self.launch()
        else:
            self.log(self.catalog.text("grabber.session_complete"))
            if self.task_manager and self.task_id:
                self.task_manager.finish(
                    self.task_id, "completed", self.catalog.text("grabber.session_complete")
                )
                self.task_id = None

    def previous(self) -> None:
        store = self.store()
        if store and self.state:
            self.state = store.previous(self.state)
            self.page.show_session(self.state)
            self.log(self.catalog.text("grabber.previous_done"))
