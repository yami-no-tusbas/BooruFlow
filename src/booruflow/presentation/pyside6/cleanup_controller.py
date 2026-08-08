"""Qt workers for read-only cleanup auditing and recoverable recycling."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from legacy.retro_cleanup import (
    iter_image_files, match_file, parse_blacklist, send_to_recycle_bin, write_report,
)


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
            report = self.output_root / "retro_cleanup" / f"audit-{datetime.now():%Y%m%d-%H%M%S}.csv"
            write_report(report, matches)
            self.completed.emit(
                count, matches, str(report), parsed.ignored_compound, parsed.ignored_non_tag, ""
            )
        except Exception as exc:
            self.completed.emit(0, [], "", 0, 0, str(exc))


class CleanupRecycleWorker(QThread):
    completed = Signal(bool, str)

    def __init__(self, paths: tuple[Path, ...]) -> None:
        super().__init__()
        self.paths = paths

    def run(self) -> None:
        self.completed.emit(*send_to_recycle_bin(self.paths))
