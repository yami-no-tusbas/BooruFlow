"""Qt process controller for local Booru tag database updates."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from booruflow.application.database_paths import gelbooru_alias_database
from booruflow.application.download_progress import DownloadProgress, parse_download_line
from booruflow.infrastructure.localization import LanguageCatalog, translate_legacy_log
from booruflow.presentation.pyside6.task_manager import TaskManager
from booruflow.presentation.pyside6.ui_logging import log_event
from booruflow.runtime import frozen_module_command


class DatabaseUpdateController(QObject):
    activity_changed = Signal(str, str, str, int, int)

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
        alias_page=None,
        database_activated: Callable[[str, Path], None] | None = None,
        status: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.python_executable = python_executable
        self.catalog = catalog
        self.options_page = options_page
        self.tag_browser_page = tag_browser_page
        self.alias_page = alias_page or options_page
        self.database_activated = database_activated
        self.status = status or (lambda _message, _level: None)
        self.credentials = credentials
        self.log = log
        self.task_manager = task_manager
        self.task_id: str | None = None
        self.stop_requested = False
        self.site = ""
        self.destination: Path | None = None
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self._process_error)
        self._pending_output = ""
        self._last_error = ""
        self._download_progress = DownloadProgress()
        self._cancel_timer = QTimer(self)
        self._cancel_timer.setSingleShot(True)
        self._cancel_timer.timeout.connect(self._cancel_fallback)

    def _emit_activity(
        self, state: str, message: str = "", current: int = -1, total: int = -1,
    ) -> None:
        operation = "aliases" if self.site.startswith("aliases:") else "database"
        self.activity_changed.emit(operation, state, message, current, total)

    def start_full(self, site: str, destination: str) -> None:
        self.start(site, destination, force_full=True)

    def start(self, site: str, destination: str, *, force_full: bool = False) -> None:
        action = "Full database rebuild" if force_full else "Database update"
        self.log(log_event("Database", f"{action} requested", context=site))
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.log(self.catalog.text("options.database_already_running"))
            return

        if not destination:
            self.options_page.database_status.setText(self.catalog.text("options.database_path_required"))
            return
        path = Path(destination)
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
            arguments = frozen_module_command("booruflow.cli.gelbooru_tags_update", [
                "--mode", "rebuild" if force_full else "update",
                "--db",
                str(path),
            ])
            manifest_url = str(self.options_page._settings.get("snapshot_manifest_url", "")).strip()
            if not force_full and not path.exists() and manifest_url:
                arguments = frozen_module_command("booruflow.cli.gelbooru_snapshot", [
                    "--manifest-url", manifest_url, "--kind", "tags", "--db", str(path),
                ])
        else:
            arguments = frozen_module_command("booruflow.cli.e621_tags_update", [
                "--db",
                str(path),
                "--cache-dir",
                str(self.project_root / "var" / "cache" / "e621_exports"),
            ])

        self.site = site
        self.destination = path
        self.stop_requested = False
        self._pending_output = ""
        self._last_error = ""
        self._download_progress = DownloadProgress()
        if self.task_manager:
            self.task_id = self.task_manager.start(
                "database_update", self.catalog.text("options.database_start", site=site, path=path)
            )
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(self.project_root))
        self.options_page.set_database_running(True, site)
        self.log(self.catalog.text("options.database_start", site=site, path=path))
        self._emit_activity("starting", self.catalog.text("wizard.activity_starting"))
        self.status(f"Updating {site} database", "PROGRESS")
        self.process.start(self.python_executable, list(arguments))

    def start_aliases(self, mode: str, destination: str) -> None:
        self.log(log_event("Aliases", f"Alias update requested (mode={mode})"))
        if self.process.state() != QProcess.ProcessState.NotRunning:
            message = self.catalog.text("options.database_already_running")
            self.log(message)
            self.activity_changed.emit("aliases", "failed", message, -1, -1)
            return
        if not destination:
            message = self.catalog.text("options.alias_database_path_required")
            self.options_page.database_status.setText(message)
            self.activity_changed.emit("aliases", "failed", message, -1, -1)
            return
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        self.site = f"aliases:{mode}"
        self.stop_requested = False
        self._pending_output = ""
        self._last_error = ""
        if self.task_manager:
            self.task_id = self.task_manager.start(
                "database_update", self.catalog.text("options.alias_start", mode=mode, path=path)
            )
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(self.project_root))
        self.options_page.set_database_running(True, self.site)
        if hasattr(self.alias_page, "set_alias_running"):
            self.alias_page.set_alias_running(True)
        self.log(self.catalog.text("options.alias_start", mode=mode, path=path))
        self._emit_activity("starting", self.catalog.text("wizard.activity_starting"))
        self.status("Updating Gelbooru aliases", "PROGRESS")
        manifest_url = str(self.options_page._settings.get("snapshot_manifest_url", "")).strip()
        if not path.exists() and manifest_url and mode == "incremental":
            command = frozen_module_command("booruflow.cli.gelbooru_snapshot", [
                "--manifest-url", manifest_url, "--kind", "aliases", "--db", str(path),
            ])
        else:
            command = frozen_module_command("booruflow.cli.gelbooru_aliases_update", [
                "--db", str(path), "--mode", mode,
            ])
        self.process.start(self.python_executable, command)

    def stop(self) -> None:
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self.log(log_event("Aliases" if self.site.startswith("aliases:") else "Database", "Cancellation requested"))
        self.stop_requested = True
        stopping = (
            "Stopping aliases update..."
            if self.site.startswith("aliases:")
            else "Stopping Gelbooru database rebuild..."
        )
        self.log(stopping)
        self.options_page.database_status.setText(self.catalog.text("options.database_stopping"))
        self._emit_activity("stopping", self.catalog.text("wizard.activity_stopping"))
        self.status(stopping, "PROGRESS")
        self.process.write(b"STOP\n")
        self.process.waitForBytesWritten(500)
        self._cancel_timer.start(10_000)

    def _cancel_fallback(self) -> None:
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self.log(log_event(
            "Aliases" if self.site.startswith("aliases:") else "Database",
            "Cooperative cancellation timed out; terminating helper",
            level="ERROR",
        ))
        self.status(
            "Database rebuild could not be stopped — see log", "ERROR"
        )
        self.process.terminate()

    def read_output(self) -> None:
        chunk = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._pending_output += chunk
        lines = self._pending_output.split("\n")
        self._pending_output = lines.pop()
        for line in lines:
            line = self._redact(line.strip())
            if line.strip():
                if line.startswith("ALIAS_SUMMARY "):
                    values = dict(re.findall(r"(\w+)=([^ ]+)", line))
                    if hasattr(self.alias_page, "set_alias_summary"):
                        self.alias_page.set_alias_summary(values)
                if line.startswith(("Pages ", "Aliases ")):
                    self.options_page.database_status.setText(line)
                downloaded = parse_download_line(line)
                if downloaded:
                    self.options_page.database_status.setText(
                        self._download_progress.update(*downloaded)
                    )
                if line.startswith("ERROR"):
                    self._last_error = line
                    self._emit_activity("failed", line)
                elif line.startswith("Update pages"):
                    match = re.search(r"tags ([\d,]+).*after_id ([\d,]+)", line)
                    self._emit_activity(
                        "running", line,
                        int(match.group(1).replace(",", "")) if match else -1,
                        -1,
                    )
                elif line.startswith("Pages "):
                    match = re.search(r"ID ([\d,]+)/([\d,]+)", line)
                    self._emit_activity(
                        "running", line,
                        int(match.group(1).replace(",", "")) if match else -1,
                        int(match.group(2).replace(",", "")) if match else -1,
                    )
                elif line.startswith("Aliases "):
                    self._emit_activity("running", line)
                if line.startswith(("CANCEL_ACK", "ALIAS_CANCEL_ACK")):
                    self.log(log_event(
                        "Aliases" if self.site.startswith("aliases:") else "Database",
                        "Cancellation acknowledged by importer",
                    ))
                if line.startswith((
                    "Existing database checkpoint:",
                    "Incremental update starting",
                    "New rebuild starting",
                    "Resuming staging database",
                )):
                    self.log(log_event("Database", line))
                if line.startswith("WARNING: Tag name collision during incremental update:"):
                    self.log(log_event("Database", line, level="WARNING"))
                self.log(translate_legacy_log(line, self.catalog.code))

    def _redact(self, line: str) -> str:
        gelbooru = self.credentials().get("gelbooru", {})
        key = gelbooru.get("api_key", "") if isinstance(gelbooru, dict) else ""
        return line.replace(str(key), "[redacted]") if key else line

    def _process_error(self, error) -> None:
        detail = self._redact(self.process.errorString())
        self._last_error = detail
        self._emit_activity("failed", detail)
        self.options_page.database_status.setText(detail)
        self.log(log_event("Database", f"Database helper error: {detail}", level="ERROR"))
        self.status("Database update failed — see log", "ERROR")
        if error == QProcess.ProcessError.FailedToStart:
            self.options_page.set_database_running(False)
            self.options_page.database_status.setText(detail)
            if hasattr(self.alias_page, "set_alias_running"):
                self.alias_page.set_alias_running(False)
            if self.task_manager and self.task_id:
                self.task_manager.finish(self.task_id, "failed", detail)
                self.task_id = None

    def finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        self._cancel_timer.stop()
        self.read_output()
        if self._pending_output.strip():
            self._last_error = self._redact(self._pending_output.strip())
            self.log(self._last_error)
            self._pending_output = ""
        self.options_page.set_database_running(False)
        if hasattr(self.alias_page, "set_alias_running"):
            self.alias_page.set_alias_running(False)
        cancelled = self.stop_requested and code != 0
        key = "options.database_finished" if code == 0 else "options.database_failed"
        message = (
            "Database rebuild cancelled at checkpoint — staging database preserved"
            if cancelled and not self.site.startswith("aliases:")
            else "Alias update cancelled — checkpoint preserved"
            if cancelled
            else self.catalog.text(key, site=self.site, code=code)
        )
        if code != 0 and self._last_error:
            message = f"{message}: {self._last_error}"
        self._emit_activity(
            "cancelled" if cancelled else ("completed" if code == 0 else "failed"),
            message,
        )
        self.options_page.database_status.setText(message)
        self.log(message)
        if cancelled:
            self.log(log_event(
                "Aliases" if self.site.startswith("aliases:") else "Database",
                message,
            ))
            self.status("Operation cancelled", "NORMAL")
        elif code == 0:
            self.status("Database update completed", "NORMAL")
        else:
            self.log(log_event("Database", f"Helper exited with code {code}", level="ERROR"))
            self.status("Database update failed — see log", "ERROR")
        if (
            code == 0
            and not self.site.startswith("aliases:")
            and self.destination is not None
            and self.database_activated is not None
        ):
            self.database_activated(self.site, self.destination)
        if self.task_manager and self.task_id:
            state = "cancelled" if self.stop_requested else ("completed" if code == 0 else "failed")
            self.task_manager.finish(self.task_id, state, message)
            self.task_id = None

    def _refresh_database_paths(self) -> None:
        if self.tag_browser_page is None:
            return
        self.tag_browser_page.set_databases(
            {
                "gelbooru": Path(self.options_page.gelbooru_database.edit.text()),
                "e621": Path(self.options_page.e621_database.edit.text()),
            }
        )
        self.tag_browser_page.set_alias_databases(
            {"gelbooru": gelbooru_alias_database(self.options_page._settings), "e621": None}
        )
