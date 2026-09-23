"""Keep portable database extraction off the Qt UI thread."""

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from booruflow.application.bundled_database_bootstrap import (
    BootstrapResult,
    prepare_bundled_database,
)


class DatabaseBootstrapWorker(QThread):
    started_database = Signal(str)
    progress = Signal(str, int, int)
    prepared = Signal(str, object)

    def __init__(self, root: Path, targets: dict[str, Path], parent=None):
        super().__init__(parent)
        self.root = root
        self.targets = targets

    def run(self) -> None:
        for kind, destination in self.targets.items():
            self.started_database.emit(kind)
            try:
                result = prepare_bundled_database(
                    self.root, kind, destination,
                    progress=lambda name, current, total: self.progress.emit(name, current, total),
                )
            except Exception as exc:  # noqa: BLE001 - worker boundary reports failure
                result = BootstrapResult("invalid", str(exc))
            self.prepared.emit(kind, result)
