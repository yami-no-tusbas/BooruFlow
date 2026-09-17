"""QProcess controller for sequential Booru review engines."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QThread, QTimer, Signal

from booruflow.application.database_paths import gelbooru_tag_database
from booruflow.application.ports import SettingsRepository
from booruflow.application.review import EngineCommand, ReviewRequest, build_review_commands
from booruflow.infrastructure.gelbooru_client import fetch_result_count
from booruflow.infrastructure.localization import LanguageCatalog, translate_legacy_log
from booruflow.presentation.pyside6.task_manager import TaskManager

PAGE_RE = re.compile(r"Page (?:Gelbooru|e621)\s+(\d+)\s+\((\d+)/(\d+)")


class ReviewProcessController(QObject):
    output = Signal(str)
    progress = Signal(int, int, int)
    site_started = Signal(str)
    finished = Signal(bool, list)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.queue: deque[EngineCommand] = deque()
        self.outputs: list[str] = []
        self.stop_requested = False
        self.current: EngineCommand | None = None

    @property
    def running(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning or bool(self.queue)

    def start(self, commands: list[EngineCommand]) -> None:
        if self.running:
            raise RuntimeError("review process is already running")
        self.queue = deque(commands)
        self.outputs = []
        self.stop_requested = False
        self._start_next()

    def stop(self) -> None:
        self.stop_requested = True
        self.queue.clear()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()
            QTimer.singleShot(3000, self._kill_if_running)

    def _kill_if_running(self) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def _start_next(self) -> None:
        if not self.queue:
            self.finished.emit(not self.stop_requested, list(self.outputs))
            return
        self.current = self.queue.popleft()
        self.outputs.append(str(self.current.output_directory))
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        for key, value in self.current.environment.items():
            if value:
                environment.insert(key, value)
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(self.current.working_directory))
        self.site_started.emit(self.current.site)
        self.process.start(self.current.program, list(self.current.arguments))

    def _read_output(self) -> None:
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not text:
            return
        self.output.emit(text)
        for match in PAGE_RE.finditer(text):
            self.progress.emit(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    def _process_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._read_output()
        if exit_code != 0:
            self.stop_requested = True
        if self.stop_requested:
            self.finished.emit(False, list(self.outputs))
        else:
            self._start_next()

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.Crashed:
            return
        self.stop_requested = True
        self.queue.clear()
        self.output.emit(self.process.errorString())
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self.finished.emit(False, list(self.outputs))


class ReviewCountWorker(QThread):
    progress = Signal(int, int, str)
    completed = Signal(list, list)

    def __init__(self, queries: tuple[str, ...], user_id: str, api_key: str) -> None:
        super().__init__()
        self.queries = queries
        self.user_id = user_id
        self.api_key = api_key

    def run(self) -> None:
        results: list[tuple[str, int]] = []
        errors: list[tuple[str, str]] = []
        for index, query in enumerate(self.queries, start=1):
            if self.isInterruptionRequested():
                break
            try:
                count, _posts = fetch_result_count(query, self.user_id, self.api_key)
                results.append((query, count))
            except Exception as exc:  # noqa: BLE001 - one failed count must not stop the batch
                errors.append((query, str(exc)))
            self.progress.emit(index, len(self.queries), query)
        self.completed.emit(results, errors)


class ReviewAutocompleteWorker(QThread):
    completed = Signal(str, list)

    def __init__(self, token: str, databases: tuple[tuple[str, Path], ...]) -> None:
        super().__init__()
        self.token = token
        self.databases = databases

    def run(self) -> None:
        merged: dict[str, dict[str, object]] = {}
        lookup = self.token.lstrip("-").casefold()
        for site, path in self.databases:
            try:
                connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
                try:
                    rows = connection.execute(
                        "SELECT name,post_count FROM tags INDEXED BY idx_tags_name "
                        "WHERE name>=? AND name<? ORDER BY post_count DESC LIMIT 20",
                        (lookup, lookup + "\uffff"),
                    ).fetchall()
                finally:
                    connection.close()
            except (OSError, sqlite3.Error):
                continue
            for name, count in rows:
                item = merged.setdefault(str(name), {"count": 0, "sites": []})
                item["count"] = max(int(item["count"]), int(count))
                item["sites"].append(site)
        ranked = sorted(merged.items(), key=lambda item: (-int(item[1]["count"]), item[0]))[:12]
        self.completed.emit(self.token, ranked)


def folded_log_text(value: str) -> str:
    repaired = value.replace("Ã©", "é").replace("Ã¨", "è")
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", repaired).casefold()
        if not unicodedata.combining(character)
    )


@dataclass
class ReviewOutputState:
    next_page: int | None = None
    retained: int = 0
    summary: list[str] = field(default_factory=list)
    buffer: str = ""

    def consume(self, line: str, translated: str) -> None:
        folded = folded_log_text(line)
        next_match = re.search(r"prochain depart page\s+(\d+)", folded)
        if next_match:
            self.next_page = int(next_match.group(1))
        retained_match = re.search(
            r"(\d+)\s+.+?\s+retenus(?:\s+sur\s+(\d+)\s+posts cumules)?",
            folded,
        )
        e621_match = re.search(r"^(\d+)\s+.+?\s+e621 retenus", folded.strip())
        if e621_match:
            self.retained += int(e621_match.group(1))
            self.summary.append(translated)
        elif retained_match and ("posts cumul" in folded or "retenus sur" in folded):
            self.retained += int(retained_match.group(1))
            self.summary.append(translated)
        elif folded.lstrip().startswith(("filtrage :", "bilan ")):
            self.summary.append(translated)


class ReviewCoordinator(QObject):
    def __init__(
        self,
        project_root: Path,
        python_executable: str,
        catalog: LanguageCatalog,
        page,
        settings_repository: SettingsRepository | None,
        credentials: Callable[[], dict[str, object]],
        log: Callable[[str], None],
        task_manager: TaskManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.python_executable = python_executable
        self.catalog = catalog
        self.page = page
        self.settings_repository = settings_repository
        self.credentials = credentials
        self.log = log
        self.task_manager = task_manager
        self.review_task_id: str | None = None
        self.count_task_id: str | None = None
        self.process_controller = ReviewProcessController(self)
        self.process_controller.output.connect(self.output)
        self.process_controller.progress.connect(self.progress)
        self.process_controller.site_started.connect(self.site_started)
        self.process_controller.finished.connect(self.finished)
        self.count_worker: ReviewCountWorker | None = None
        self.autocomplete_workers: list[ReviewAutocompleteWorker] = []
        self.active_request: ReviewRequest | None = None
        self.output_state = ReviewOutputState()

    def start(self, request: ReviewRequest) -> None:
        if self.settings_repository:
            saved_settings = self.settings_repository.load()
            saved_settings["review_auto_continue"] = request.auto_continue
            self.settings_repository.save(saved_settings)
        credentials = self.credentials()
        gelbooru = credentials.get("gelbooru", {})
        if "gelbooru" in request.sites and (
            not isinstance(gelbooru, dict)
            or not gelbooru.get("user_id")
            or not gelbooru.get("api_key")
        ):
            self.page.state.setText(self.catalog.text("review.credentials_missing"))
            return
        commands = build_review_commands(
            request,
            self.project_root,
            self.python_executable,
            credentials,
        )
        self.active_request = request
        if self.task_manager and not self.review_task_id:
            self.review_task_id = self.task_manager.start(
                "review",
                self.catalog.text("nav.review"),
                f"{len(request.queries)} requête(s)",
            )
        self.output_state = ReviewOutputState()
        self.page.show_results([])
        self.page.set_running(True)
        self.log(self.catalog.text("review.log_start", count=len(request.queries)))
        try:
            self.process_controller.start(commands)
        except RuntimeError as exc:
            self.page.set_running(False)
            self.log(str(exc))

    def stop(self) -> None:
        self.page.state.setText(self.catalog.text("review.stopping"))
        if self.count_worker and self.count_worker.isRunning():
            self.count_worker.requestInterruption()
        else:
            self.process_controller.stop()

    def site_started(self, site: str) -> None:
        self.log(self.catalog.text("review.site_started", site=site))
        if self.task_manager and self.review_task_id:
            self.task_manager.progress(self.review_task_id, 0, 0, site)

    def progress(self, page: int, block: int, total: int) -> None:
        self.page.set_progress(page, block, total)
        if self.task_manager and self.review_task_id:
            self.task_manager.progress(
                self.review_task_id, block, total, f"page {page}"
            )

    def output(self, chunk: str) -> None:
        self.output_state.buffer += chunk
        parts = self.output_state.buffer.splitlines(keepends=True)
        self.output_state.buffer = ""
        for part in parts:
            if part.endswith(("\n", "\r")):
                self.line(part.rstrip("\r\n"))
            else:
                self.output_state.buffer = part

    def line(self, line: str) -> None:
        if not line.strip():
            return
        translated = translate_legacy_log(line, self.catalog.code)
        self.log(translated)
        self.output_state.consume(line, translated)

    def finished(self, success: bool, outputs: list[str]) -> None:
        if self.output_state.buffer:
            self.line(self.output_state.buffer)
            self.output_state.buffer = ""
        self.page.set_running(False)
        request = self.active_request
        if success and self.output_state.next_page:
            self.page.start_page.setValue(self.output_state.next_page)
        can_continue = bool(
            success
            and request
            and request.auto_continue
            and request.sites == ("gelbooru",)
            and self.output_state.retained == 0
            and self.output_state.next_page
            and self.output_state.next_page > request.start_page
        )
        if can_continue and request:
            next_page = int(self.output_state.next_page or request.start_page)
            self.page.show_summary(self.output_state.summary, completed=False)
            self.page.state.setText(
                self.page.state.text()
                + "\n"
                + self.catalog.text("review.continuing", page=next_page)
            )
            QTimer.singleShot(350, lambda: self.continue_review(request, next_page))
            return
        entries = self.result_entries(request) if success and request else []
        self.page.show_results(entries)
        if success:
            self.page.show_summary(self.output_state.summary, completed=True)
        else:
            self.page.state.setText(self.catalog.text("review.interrupted"))
        if success and outputs:
            self.log(self.catalog.text("review.outputs", paths="; ".join(outputs)))
        if self.task_manager and self.review_task_id:
            state = "completed" if success else (
                "cancelled" if self.process_controller.stop_requested else "failed"
            )
            self.task_manager.finish(self.review_task_id, state, self.page.state.text())
            self.review_task_id = None

    def continue_review(self, request: ReviewRequest, next_page: int) -> None:
        self.page.start_page.setValue(next_page)
        self.start(replace(request, start_page=next_page))

    @staticmethod
    def result_entries(request: ReviewRequest) -> list[tuple[str, str]]:
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
                        seen.add(entry)
                        entries.append(entry)
        return entries

    def count(self, queries: tuple[str, ...]) -> None:
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
            self.count_task_id = self.task_manager.start(
                "review_count", self.catalog.text("review.count"), f"{len(queries)} requête(s)"
            )
        self.count_worker = ReviewCountWorker(
            queries,
            str(gelbooru["user_id"]),
            str(gelbooru["api_key"]),
        )
        self.count_worker.progress.connect(self.count_progress)
        self.count_worker.completed.connect(self.count_finished)
        self.count_worker.start()

    def count_progress(self, current: int, total: int, query: str) -> None:
        self.page.set_count_progress(current, total, query)
        if self.task_manager and self.count_task_id:
            self.task_manager.progress(self.count_task_id, current, total, "count", query)

    def autocomplete(self, token: str) -> None:
        request_sites = tuple(self.page.site.currentData())
        settings = self.settings_repository.load() if self.settings_repository else {}
        candidates = (
            ("Gelbooru", gelbooru_tag_database(settings) or Path()),
            ("e621", Path(str(settings.get("e621_database", "")))),
        )
        databases = tuple(
            (name, path)
            for name, path in candidates
            if name.casefold() in request_sites and path.is_file()
        )
        if not databases:
            return
        worker = ReviewAutocompleteWorker(token, databases)
        self.autocomplete_workers.append(worker)
        worker.completed.connect(self.page.show_suggestions)
        worker.finished.connect(lambda value=worker: self.discard_autocomplete(value))
        worker.start()

    def discard_autocomplete(self, worker: ReviewAutocompleteWorker) -> None:
        if worker in self.autocomplete_workers:
            self.autocomplete_workers.remove(worker)
        worker.deleteLater()

    def count_finished(self, results: list, errors: list) -> None:
        self.page.set_running(False)
        for query, count in results:
            self.log(self.catalog.text("review.count_result", query=query, count=count))
        if results:
            total = sum(int(count) for _query, count in results)
            self.page.state.setText(
                self.catalog.text("review.count_total", count=len(results), total=total)
            )
        for query, error in errors:
            self.log(self.catalog.text("review.count_error", query=query, error=error))
        if self.task_manager and self.count_task_id:
            stopped = bool(self.count_worker and self.count_worker.isInterruptionRequested())
            state = "cancelled" if stopped else ("failed" if errors and not results else "completed")
            self.task_manager.finish(self.count_task_id, state, self.page.state.text())
            self.count_task_id = None
        if self.count_worker:
            self.count_worker.deleteLater()
            self.count_worker = None
