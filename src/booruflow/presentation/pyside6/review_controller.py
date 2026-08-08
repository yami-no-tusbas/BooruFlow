"""QProcess controller for sequential Booru review engines."""

from __future__ import annotations

import re
import sqlite3
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QThread, QTimer, Signal

from booruflow.application.review import EngineCommand


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
        from legacy.gelbooru_artistes_par_tags_ignore import fetch_result_count

        results: list[tuple[str, int]] = []
        errors: list[tuple[str, str]] = []
        for index, query in enumerate(self.queries, start=1):
            if self.isInterruptionRequested():
                break
            try:
                count, _posts = fetch_result_count(query, self.user_id, self.api_key)
                results.append((query, count))
            except Exception as exc:
                errors.append((query, str(exc)))
            self.progress.emit(index, len(self.queries), query)
        self.completed.emit(results, errors)


class ReviewAutocompleteWorker(QThread):
    completed = Signal(str, list)

    def __init__(self, token: str, databases: tuple[tuple[str, Path], ...]) -> None:
        super().__init__(); self.token = token; self.databases = databases

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
