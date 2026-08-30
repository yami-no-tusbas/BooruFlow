"""Qt workers for read-only cleanup auditing and recoverable recycling."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from booruflow.application.ports import SettingsRepository
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.retro_cleanup import (
    iter_image_files,
    match_file,
    parse_blacklist,
    send_to_recycle_bin,
    write_report,
)
from booruflow.presentation.pyside6.task_manager import TaskManager


class CleanupScanWorker(QThread):
    progress = Signal(int, int)
    completed = Signal(int, list, str, int, int, str)

    def __init__(self, roots: tuple[Path, ...], blacklist: Path, output_root: Path) -> None:
        super().__init__()
        self.roots = roots
        self.blacklist = blacklist
        self.output_root = output_root

    def run(self) -> None:
        try:
            parsed = parse_blacklist(
                self.blacklist.read_text(encoding="utf-8-sig", errors="replace").splitlines()
            )
            matches = []
            count = 0
            for count, path in enumerate(iter_image_files(self.roots), start=1):
                if self.isInterruptionRequested():
                    break
                matches.extend(match_file(path, parsed, "all"))
                if count == 1 or count % 250 == 0:
                    self.progress.emit(count, len(matches))
            report = (
                self.output_root / "retro_cleanup" / f"audit-{datetime.now():%Y%m%d-%H%M%S}.csv"  # noqa: DTZ005
            )
            write_report(report, matches)
            self.completed.emit(
                count, matches, str(report), parsed.ignored_compound, parsed.ignored_non_tag, ""
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary reports unexpected failures
            self.completed.emit(0, [], "", 0, 0, str(exc))


class CleanupRecycleWorker(QThread):
    completed = Signal(bool, str)

    def __init__(self, paths: tuple[Path, ...]) -> None:
        super().__init__()
        self.paths = paths

    def run(self) -> None:
        self.completed.emit(*send_to_recycle_bin(self.paths))


class CleanupController(QObject):
    def __init__(
        self,
        project_root: Path,
        catalog: LanguageCatalog,
        page,
        settings_repository: SettingsRepository | None,
        log: Callable[[str], None],
        task_manager: TaskManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.catalog = catalog
        self.page = page
        self.settings_repository = settings_repository
        self.log = log
        self.task_manager = task_manager
        self.task_id: str | None = None
        self.scan_worker: CleanupScanWorker | None = None
        self.recycle_worker: CleanupRecycleWorker | None = None
        self.matches: list = []
        self.report = ""

    def paths(self) -> tuple[Path, Path]:
        settings = self.settings_repository.load() if self.settings_repository else {}
        blacklist = Path(str(settings.get("blacklist_file", "")))
        output = Path(str(settings.get("output_root", self.project_root / "var" / "results")))
        return blacklist, output

    def start(self, roots: tuple[Path, ...]) -> None:
        blacklist, output = self.paths()
        if not blacklist.is_file():
            message = self.catalog.text("cleanup.blacklist_missing", path=blacklist)
            self.page.state.setText(message)
            self.log(message)
            return
        self.page.set_running(True)
        if self.task_manager:
            self.task_id = self.task_manager.start(
                "cleanup", self.catalog.text("nav.cleanup"), f"{len(roots)} dossier(s)"
            )
        self.matches = []
        self.log(self.catalog.text("cleanup.log_start", count=len(roots)))
        self.scan_worker = CleanupScanWorker(roots, blacklist, output)
        self.scan_worker.progress.connect(self.progress)
        self.scan_worker.completed.connect(self.scan_finished)
        self.scan_worker.start()

    def stop(self) -> None:
        if self.scan_worker:
            self.scan_worker.requestInterruption()
            self.page.state.setText(self.catalog.text("cleanup.stopping"))

    def progress(self, files: int, matches: int) -> None:
        self.page.set_progress(files, matches)
        if self.task_manager and self.task_id:
            self.task_manager.progress(
                self.task_id, files, 0, "audit", f"{matches} correspondance(s)"
            )

    def scan_finished(
        self,
        files: int,
        matches: list,
        report: str,
        ignored_compound: int,
        ignored_non_tag: int,
        error: str,
    ) -> None:
        self.page.set_running(False)
        if error:
            message = self.catalog.text("cleanup.failed", error=error)
            self.page.state.setText(message)
            self.log(message)
            task_state = "failed"
        else:
            self.matches = matches
            self.report = report
            self.page.show_matches(matches)
            unique = len({match.path for match in matches})
            self.page.state.setText(
                self.catalog.text("cleanup.finished", files=files, matches=unique)
            )
            self.log(
                self.catalog.text(
                    "cleanup.report",
                    path=report,
                    compound=ignored_compound,
                    non_tag=ignored_non_tag,
                )
            )
            stopped = bool(self.scan_worker and self.scan_worker.isInterruptionRequested())
            task_state = "cancelled" if stopped else "completed"
        if self.task_manager and self.task_id:
            self.task_manager.finish(self.task_id, task_state, self.page.state.text())
            self.task_id = None
        if self.scan_worker:
            self.scan_worker.deleteLater()
            self.scan_worker = None

    def recycle_preview(self) -> tuple[tuple[Path, ...], int, str]:
        paths = tuple(sorted({match.path for match in self.matches}))
        size = sum(path.stat().st_size for path in paths if path.is_file())
        return paths, size, self.report

    def recycle(self, paths: tuple[Path, ...]) -> None:
        self.page.scan_button.setEnabled(False)
        self.page.recycle_button.setEnabled(False)
        self.page.state.setText(self.catalog.text("cleanup.recycling", count=len(paths)))
        self.recycle_worker = CleanupRecycleWorker(paths)
        self.recycle_worker.completed.connect(self.recycle_finished)
        self.recycle_worker.start()

    def recycle_finished(self, success: bool, message: str) -> None:
        self.log(message)
        self.page.scan_button.setEnabled(True)
        self.page.recycle_button.setEnabled(not success)
        self.page.state.setText(
            self.catalog.text("cleanup.recycle_done" if success else "cleanup.recycle_failed")
        )
        if success:
            self.matches = []
        if self.recycle_worker:
            self.recycle_worker.deleteLater()
            self.recycle_worker = None
