"""Asynchronous, read-only maintenance data for Options."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from booruflow.application.hydra_model_manager import legacy_hydra_directory
from booruflow.application.model_inventory import hydra_status, inventory_models, model_totals
from booruflow.infrastructure.localization import LanguageCatalog


class StorageInventoryWorker(QThread):
    completed = Signal(object, object, str)

    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.project_root = project_root

    def run(self) -> None:
        try:
            totals = model_totals(inventory_models(self.project_root))
            state, size, _message = hydra_status(self.project_root)
            hydra = {
                "state": state,
                "size": size,
                "legacy": legacy_hydra_directory(self.project_root).is_dir(),
            }
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.completed.emit({}, {}, str(exc))
        else:
            self.completed.emit(totals, hydra, "")


class OptionsMaintenanceController(QObject):
    def __init__(
        self,
        project_root: Path,
        catalog: LanguageCatalog,
        page,
        log,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.catalog = catalog
        self.page = page
        self.log = log
        self.worker: StorageInventoryWorker | None = None

    def refresh_storage(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self.page.set_storage_running(True)
        worker = StorageInventoryWorker(self.project_root)
        worker.completed.connect(self._storage_ready)
        worker.finished.connect(lambda value=worker: self._discard(value))
        self.worker = worker
        worker.start()

    def _storage_ready(self, totals: dict, hydra: dict, error: str) -> None:
        self.page.set_storage_running(False)
        if error:
            message = self.catalog.text("options.storage_failed")
            self.page.page_status.show_message(message, timeout_ms=6_000, log=True)
            self.log(f"[ERROR] [Options] Storage inventory failed: {error}")
            return
        self.page.show_storage(totals, hydra)

    def _discard(self, worker: StorageInventoryWorker) -> None:
        if self.worker is worker:
            self.worker = None
        worker.deleteLater()

    def shutdown(self) -> bool:
        """Let the short read-only inventory finish before Qt destroys its thread."""
        if self.worker is None or not self.worker.isRunning():
            return True
        self.worker.requestInterruption()
        return self.worker.wait(5_000)
