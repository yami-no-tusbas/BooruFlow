"""Detection and safe search-window launching for voidtools Everything."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path


def _registry_app_paths() -> tuple[Path, ...]:
    if sys.platform != "win32":
        return ()
    try:
        import winreg
    except ImportError:
        return ()
    candidates: list[Path] = []
    subkey = r"Software\Microsoft\Windows\CurrentVersion\App Paths\Everything.exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for access in (winreg.KEY_READ, winreg.KEY_READ | winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, subkey, 0, access) as key:
                    value, _kind = winreg.QueryValueEx(key, None)
            except OSError:
                continue
            candidates.append(Path(str(value).strip().strip('"')))
    return tuple(candidates)


def find_everything_executable(configured: str = "") -> Path | None:
    """Resolve an explicit preference, PATH/App Paths, then standard installs."""
    if configured.strip():
        preferred = Path(configured.strip().strip('"'))
        if preferred.is_file():
            return preferred.resolve()

    candidates: list[Path] = []
    discovered = shutil.which("Everything.exe") or shutil.which("Everything")
    if discovered:
        candidates.append(Path(discovered))
    candidates.extend(_registry_app_paths())
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(variable, "").strip()
        if base:
            candidates.append(Path(base) / "Everything" / "Everything.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def everything_artist_query(artist: str) -> str:
    """Build a case-insensitive filename regex anchored to ``artist - ``."""
    if not artist.strip():
        raise ValueError("artist must not be empty")
    pattern = re.escape(artist.strip()) + re.escape(" - ")
    # regex* makes the remainder literal Everything search syntax, so artist
    # characters cannot become Everything operators. The regex itself remains active.
    return f"no-case:no-path:regex*:^{pattern}"


def build_everything_command(executable: Path, root: Path, artist: str) -> list[str]:
    return [
        str(executable),
        "-path",
        str(Path(root).resolve()),
        "-search",
        everything_artist_query(artist),
    ]


def launch_everything(command: Sequence[str], *, popen=subprocess.Popen):
    return popen(list(command), close_fds=True)


def configured_everything(settings: Mapping[str, object]) -> Path | None:
    return find_everything_executable(str(settings.get("everything_executable", "")))
