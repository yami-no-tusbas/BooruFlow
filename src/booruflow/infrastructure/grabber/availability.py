"""Filesystem-only detection for an optional Grabber installation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from shutil import which

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


def resolve_grabber_executable(configured: object = "") -> Path | None:
    """Resolve the exact executable shared by Home, Options and Grabber Tools."""
    value = str(configured or "").strip()
    if value:
        return Path(value)
    discovered = which("Grabber.exe")
    return Path(discovered) if discovered else None


def grabber_availability(configured: object = "") -> ToolAvailability:
    executable = resolve_grabber_executable(configured)
    if executable is None:
        return ToolAvailability(False, "Grabber has not been configured.")
    if executable.is_file():
        return ToolAvailability(True)
    return ToolAvailability(False, f"Grabber executable was not found: {executable}")
