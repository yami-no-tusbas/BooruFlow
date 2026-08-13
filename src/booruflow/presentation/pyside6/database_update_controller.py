"""Qt process controller for local Booru tag database updates."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment

from booruflow.infrastructure.localization import LanguageCatalog, translate_legacy_log
from booruflow.presentation.pyside6.task_manager import TaskManager


class DatabaseUpdateController(QObject):
    def __init__(
        self,
        project_root: Path,
        python_executable: str,
        catalog: LanguageCatalog,
        options_page,
        tag_browser_page,
        credentials: Callable[[], dict[str, object]],
        log: Callable[[str], None],
        task_manager: TaskManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.python_executable = python_executable
        self.catalog = catalog
        self.options_page = options_page
        self.tag_browser_page = tag_browser_page
        self.credentials = credentials
        self.log = log
        self.task_manager = task_manager
        self.task_id: str | None = None
        self.stop_requested = False
        self.site = ""
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.finished)

    def start(self, site: str, destination: str) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.log(self.catalog.text("options.database_already_running"))
            return

        path = Path(destination)
        if not destination:
            filename = "gelbooru_tags.db" if site == "gelbooru" else "e621_tags.db"
            path = self.project_root / "data" / "databases" / filename
            row = (
                self.options_page.gelbooru_database
                if site == "gelbooru"
                else self.options_page.e621_database
            )
            row.edit.setText(str(path))
            self._refresh_database_paths()
        path.parent.mkdir(parents=True, exist_ok=True)

        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        if site == "gelbooru":
            gelbooru = self.credentials().get("gelbooru", {})
            if (
                not isinstance(gelbooru, dict)
                or not gelbooru.get("user_id")
                or not gelbooru.get("api_key")
            ):
                self.options_page.database_status.setText(
                    self.catalog.text("review.credentials_missing")
                )
                return
            environment.insert("GELBOORU_USER_ID", str(gelbooru["user_id"]))
            environment.insert("GELBOORU_API_KEY", str(gelbooru["api_key"]))
            arguments = (
                "-u",
                "-m",
                "booruflow.cli.gelbooru_tags_update",
                "--db",
                str(path),
            )
        else:
            arguments = (
                "-u",
                "-m",
                "booruflow.cli.e621_tags_update",
                "--db",
                str(path),
                "--cache-dir",
                str(self.project_root / "var" / "cache" / "e621_exports"),
            )

        self.site = site
        self.stop_requested = False
        if self.task_manager:
            self.task_id = self.task_manager.start(
                "database_update", self.catalog.text("options.database_start", site=site, path=path)
            )
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(self.project_root))
        self.options_page.set_database_running(True, site)
        self.log(self.catalog.text("options.database_start", site=site, path=path))
        self.process.start(self.python_executable, list(arguments))

    def stop(self) -> None:
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self.log(self.catalog.text("options.database_stopping"))
        self.options_page.database_status.setText(self.catalog.text("options.database_stopping"))
        self.process.terminate()
        self.stop_requested = True

    def read_output(self) -> None:
        chunk = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in chunk.splitlines():
            if line.strip():
                self.log(translate_legacy_log(line, self.catalog.code))

    def finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        self.read_output()
        self.options_page.set_database_running(False)
        key = "options.database_finished" if code == 0 else "options.database_failed"
        message = self.catalog.text(key, site=self.site, code=code)
        self.options_page.database_status.setText(message)
        self.log(message)
        if self.task_manager and self.task_id:
            state = "cancelled" if self.stop_requested else ("completed" if code == 0 else "failed")
            self.task_manager.finish(self.task_id, state, message)
            self.task_id = None

    def _refresh_database_paths(self) -> None:
        self.tag_browser_page.set_databases(
            {
                "gelbooru": Path(self.options_page.gelbooru_database.edit.text()),
                "e621": Path(self.options_page.e621_database.edit.text()),
            }
        )
