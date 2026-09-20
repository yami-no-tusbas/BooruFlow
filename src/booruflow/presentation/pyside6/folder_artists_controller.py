"""QThread orchestration for folder artist scanning and Everything launching."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from booruflow.application.ports import SettingsRepository
from booruflow.infrastructure.everything import (
    build_everything_command,
    find_everything_executable,
    launch_everything,
)
from booruflow.infrastructure.folder_artists import scan_folder_artists
from booruflow.infrastructure.localization import LanguageCatalog


class FolderArtistScanWorker(QThread):
    progress = Signal(int)
    completed = Signal(object, str)

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root

    def run(self) -> None:
        try:
            result = scan_folder_artists(
                self.root,
                cancelled=self.isInterruptionRequested,
                progress=self.progress.emit,
            )
            self.completed.emit(result, "")
        except Exception as exc:  # noqa: BLE001 - worker boundary reports scan failures
            self.completed.emit(None, str(exc))


class FolderArtistsController(QObject):
    def __init__(
        self,
        catalog: LanguageCatalog,
        page,
        settings_repository: SettingsRepository | None,
        log: Callable[[str], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.page = page
        self.settings_repository = settings_repository
        self.log = log
        self.worker: FolderArtistScanWorker | None = None
        self.scanned_root: Path | None = None

    def start(self, value: str) -> None:
        if self.worker and self.worker.isRunning():
            return
        root = Path(value)
        if not root.is_dir():
            self.page.state.setText(self.catalog.text("folder_artists.folder_missing"))
            return
        self.scanned_root = None
        self.page.clear_results()
        self.page.set_running(True)
        self.page.state.setText(self.catalog.text("folder_artists.scanning", images=0))
        self._save_setting("folder_artists_directory", str(root.resolve()))
        self.worker = FolderArtistScanWorker(root.resolve())
        self.worker.progress.connect(self.page.set_progress)
        self.worker.completed.connect(self._finished)
        self.worker.start()

    def stop(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.page.state.setText(self.catalog.text("folder_artists.stopping"))

    def folder_changed(self, _value: str) -> None:
        self.scanned_root = None
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()

    def _finished(self, result: object, error: str) -> None:
        self.page.set_running(False)
        if error or result is None:
            message = self.catalog.text("folder_artists.failed", error=error)
        elif result.cancelled:
            message = self.catalog.text("folder_artists.cancelled")
        else:
            self.scanned_root = result.root
            self.page.show_scan(result)
            message = self.catalog.text("folder_artists.finished", artists=len(result.artists))
        self.page.state.setText(message)
        self.log(message)
        if self.worker:
            self.worker.deleteLater()
            self.worker = None

    def open_artist(self, artist: str) -> None:
        if self.scanned_root is None or not self.scanned_root.is_dir():
            self.page.state.setText(self.catalog.text("folder_artists.rescan_required"))
            return
        settings = self.settings_repository.load() if self.settings_repository else {}
        executable = find_everything_executable(str(settings.get("everything_executable", "")))
        if executable is None:
            selected = self.page.choose_everything()
            executable = find_everything_executable(selected) if selected else None
            if executable is None:
                self.page.state.setText(self.catalog.text("folder_artists.everything_missing"))
                return
            self._save_setting("everything_executable", str(executable))
        command = build_everything_command(executable, self.scanned_root, artist)
        try:
            launch_everything(command)
        except OSError as exc:
            self.page.state.setText(self.catalog.text("folder_artists.everything_failed", error=exc))
            return
        self.page.state.setText(self.catalog.text("folder_artists.opened", artist=artist))

    def _save_setting(self, key: str, value: str) -> None:
        if self.settings_repository is None:
            return
        settings = self.settings_repository.load()
        settings[key] = value
        self.settings_repository.save(settings)

    def shutdown(self) -> bool:
        if not self.worker or not self.worker.isRunning():
            return True
        self.worker.requestInterruption()
        return self.worker.wait(2_000)
