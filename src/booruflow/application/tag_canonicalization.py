"""Canonical names for newly created Gelbooru tag intentions."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from booruflow.application.tagging import normalize_booru_tag
from booruflow.infrastructure.gelbooru_aliases import resolve_gelbooru_alias


@dataclass(frozen=True, slots=True)
class CanonicalGelbooruTag:
    original_name: str
    canonical_name: str

    @property
    def was_aliased(self) -> bool:
        return self.original_name != self.canonical_name


def canonicalize_new_gelbooru_tag(
    name: str,
    alias_database: Path | None,
) -> CanonicalGelbooruTag:
    """Resolve only safe active aliases for a newly-created tag intention.

    Existing persisted tags deliberately never pass through this function.
    """
    original = normalize_booru_tag(name)
    if not original or alias_database is None:
        return CanonicalGelbooruTag(original, original)
    try:
        resolved = resolve_gelbooru_alias(original, alias_database)
    except (OSError, RuntimeError, sqlite3.Error):
        resolved = original
    return CanonicalGelbooruTag(original, normalize_booru_tag(resolved) or original)
