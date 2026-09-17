"""Single, explicit configuration paths for the two Gelbooru catalogues."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

GELBOORU_TAG_DATABASE_KEY = "gelbooru_tag_database"
GELBOORU_ALIAS_DATABASE_KEY = "gelbooru_alias_database"
LEGACY_GELBOORU_TAG_DATABASE_KEY = "gelbooru_database"


def configured_path(settings: Mapping[str, object], key: str) -> Path | None:
    value = str(settings.get(key, "")).strip()
    return Path(value) if value else None


def gelbooru_tag_database(settings: Mapping[str, object]) -> Path | None:
    """Return only the explicitly selected tag catalogue.

    The legacy key is a read-only migration compatibility path; it never causes
    a directory scan or a filename-based fallback.
    """
    return configured_path(settings, GELBOORU_TAG_DATABASE_KEY) or configured_path(
        settings, LEGACY_GELBOORU_TAG_DATABASE_KEY
    )


def gelbooru_alias_database(settings: Mapping[str, object]) -> Path | None:
    """Return only the explicitly configured, independent alias catalogue."""
    return configured_path(settings, GELBOORU_ALIAS_DATABASE_KEY)


def migrate_database_settings(settings: Mapping[str, object], project_root: Path) -> tuple[dict[str, object], bool]:
    """Add stable explicit keys without guessing a catalogue on disk."""
    migrated = dict(settings)
    changed = False
    if not str(migrated.get(GELBOORU_TAG_DATABASE_KEY, "")).strip():
        legacy = str(migrated.get(LEGACY_GELBOORU_TAG_DATABASE_KEY, "")).strip()
        if legacy:
            migrated[GELBOORU_TAG_DATABASE_KEY] = legacy
            changed = True
    if not str(migrated.get(GELBOORU_ALIAS_DATABASE_KEY, "")).strip():
        migrated[GELBOORU_ALIAS_DATABASE_KEY] = str(
            project_root / "data" / "databases" / "gelbooru_aliases.db"
        )
        changed = True
    return migrated, changed
