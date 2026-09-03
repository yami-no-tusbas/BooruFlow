"""Filesystem-only detection for an optional Grabber installation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from booruflow.domain import ToolAvailability


@dataclass(frozen=True, slots=True)
class GrabberInstallation:
    directory: Path | None

    @property
    def executable(self) -> Path | None:
        return self.directory / "Grabber.exe" if self.directory else None

    def availability(self) -> ToolAvailability:
        executable = self.executable
        if executable and executable.is_file():
            return ToolAvailability(True)
        if self.directory is None:
            return ToolAvailability(False, "Grabber has not been configured.")
        return ToolAvailability(False, f"Grabber.exe was not found in {self.directory}")

