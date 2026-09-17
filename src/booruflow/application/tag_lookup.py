"""Shared local tag lookup with conservative eligibility policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from booruflow.application.tag_canonicalization import canonicalize_new_gelbooru_tag
from booruflow.application.tag_policy import is_deprecated
from booruflow.application.tagging import normalize_booru_tag
from booruflow.infrastructure.gelbooru_aliases import GelbooruAliasRepository
from booruflow.infrastructure.tag_browser import TagRow, TagSearch, search_tags


def search_eligible_tags(site: str, database: Path, request: TagSearch) -> list[TagRow]:
    """Run the canonical tag search and remove only confirmed deprecated rows."""
    return [row for row in search_tags(database, request) if not is_deprecated(site, row.category)]


def lookup_tags(site: str, database: Path, text: str, *, limit: int = 20) -> list[TagRow]:
    return search_eligible_tags(site, database, TagSearch(text=text, mode="contains", limit=limit))


def exact_tag(site: str, database: Path, text: str) -> TagRow | None:
    rows = search_eligible_tags(site, database, TagSearch(text=text, mode="exact", limit=1))
    return rows[0] if rows else None


def eligible_tag(site: str, row: TagRow | None) -> bool:
    return row is not None and not is_deprecated(site, row.category)


@dataclass(frozen=True, slots=True)
class TagLookupSuggestion:
    value: str
    alias_source: str | None = None


def lookup_gelbooru_suggestions(
    tag_database: Path, alias_database: Path | None, text: str, *, limit: int = 20
) -> list[TagLookupSuggestion]:
    """Find eligible tags, including active alias sources absent from ``tags``."""
    suggestions: list[TagLookupSuggestion] = []
    seen: set[str] = set()
    if alias_database is not None and alias_database.is_file():
        for source in GelbooruAliasRepository(alias_database).active_sources_matching(text, limit=limit):
            canonical = canonicalize_new_gelbooru_tag(source, alias_database)
            row = exact_tag("gelbooru", tag_database, canonical.canonical_name)
            if row is None:
                continue
            key = normalize_booru_tag(row.name)
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(TagLookupSuggestion(row.name, source))
            if len(suggestions) >= limit:
                return suggestions
    for row in lookup_tags("gelbooru", tag_database, text, limit=limit):
        key = normalize_booru_tag(row.name)
        if key not in seen:
            seen.add(key)
            suggestions.append(TagLookupSuggestion(row.name))
        if len(suggestions) >= limit:
            break
    return suggestions
