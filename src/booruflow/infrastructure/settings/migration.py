"""Small, non-destructive migrations for application settings."""

from __future__ import annotations

from pathlib import Path


def migrate_blacklist_setting(settings: dict[str, object]) -> tuple[dict[str, object], bool]:
    """Migrate legacy folder contents only when each target is unambiguous."""

    migrated = dict(settings)
    legacy_value = str(migrated.get("grabber_directory", "")).strip()
    if not legacy_value:
        return migrated, False
    legacy_directory = Path(legacy_value)
    blacklist = legacy_directory / "blacklist.txt"
    executable = legacy_directory / "Grabber.exe"
    changed = False
    if not str(migrated.get("blacklist_file", "")).strip() and blacklist.is_file():
        migrated["blacklist_file"] = str(blacklist)
        changed = True
    if not str(migrated.get("grabber_executable", "")).strip() and executable.is_file():
        migrated["grabber_executable"] = str(executable)
        changed = True
    if "blacklist_file" in migrated and migrated.pop("grabber_directory", None) is not None:
        changed = True
    return migrated, changed
