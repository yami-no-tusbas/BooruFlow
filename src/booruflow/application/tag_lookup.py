"""Shared local tag lookup with conservative eligibility policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from booruflow.application.tag_canonicalization import canonicalize_new_gelbooru_tag
from booruflow.application.tag_policy import is_deprecated
from booruflow.application.tagging import normalize_booru_tag
from booruflow.infrastructure.gelbooru_aliases import GelbooruAliasRepository
from booruflow.infrastructure.tag_browser import TagRow, TagSearch, exact_tags, search_tags

AUTOCOMPLETE_MIN_CHARS = 3
AUTOCOMPLETE_MIN_POST_COUNT = 5


def search_eligible_tags(
    site: str, database: Path, request: TagSearch, *, rank_for_autocomplete: bool = False
) -> list[TagRow]:
    """Run the canonical tag search and remove only confirmed deprecated rows."""
    return [
        row for row in search_tags(database, request, rank_for_autocomplete=rank_for_autocomplete)
        if not is_deprecated(site, row.category)
    ]


def lookup_tags(
    site: str,
    database: Path,
    text: str,
    *,
    category: int | None = None,
    limit: int = 20,
) -> list[TagRow]:
    """Return popular, non-deprecated tag suggestions for a text query."""
    exact = search_eligible_tags(
        site,
        database,
        TagSearch(text=text, mode="exact", category=category, limit=1),
    )
    popular = search_eligible_tags(
        site,
        database,
        TagSearch(
            text=text,
            mode="contains",
            category=category,
            minimum_count=AUTOCOMPLETE_MIN_POST_COUNT,
            limit=limit,
        ),
        rank_for_autocomplete=True,
    )
    seen: set[str] = set()
    return [
        row for row in [*exact, *popular]
        if not (row.name.casefold() in seen or seen.add(row.name.casefold()))
    ][:limit]


def exact_tag(site: str, database: Path, text: str) -> TagRow | None:
    rows = search_eligible_tags(site, database, TagSearch(text=text, mode="exact", limit=1))
    return rows[0] if rows else None


def eligible_tag(site: str, row: TagRow | None) -> bool:
    return row is not None and not is_deprecated(site, row.category)


@dataclass(frozen=True, slots=True)
class TagLookupSuggestion:
    value: str
    alias_source: str | None = None
    category: int | None = field(default=None, compare=False)
    post_count: int = field(default=0, compare=False)


def lookup_gelbooru_suggestions(
    tag_database: Path,
    alias_database: Path | None,
    text: str,
    *,
    category: int | None = None,
    limit: int = 20,
) -> list[TagLookupSuggestion]:
    """Find eligible tags, including active alias sources absent from ``tags``."""
    suggestions: list[TagLookupSuggestion] = []
    query = normalize_booru_tag(text).casefold()
    candidate_limit = max(limit, min(100, limit * 5))
    candidates: list[TagLookupSuggestion] = []
    alias_sources: list[str] = []
    alias_repository: GelbooruAliasRepository | None = None
    if alias_database is not None and alias_database.is_file():
        alias_repository = GelbooruAliasRepository(alias_database)
        alias_sources = alias_repository.active_sources_matching(text, limit=candidate_limit)
        canonical_by_source = {
            source: canonicalize_new_gelbooru_tag(source, alias_database)
            for source in alias_sources
        }
        canonical_rows = {
            row.name.casefold(): row
            for row in exact_tags(
                tag_database,
                [value.canonical_name for value in canonical_by_source.values()],
            )
            if not is_deprecated("gelbooru", row.category)
        }
        for source in alias_sources:
            canonical = canonical_by_source[source]
            row = canonical_rows.get(canonical.canonical_name.casefold())
            exact_alias = normalize_booru_tag(source).casefold() == query
            if (
                row is None
                or (category is not None and row.category != category)
                or (not exact_alias and row.post_count < AUTOCOMPLETE_MIN_POST_COUNT)
            ):
                continue
            candidates.append(TagLookupSuggestion(row.name, source, row.category, row.post_count))
    exact_rows = search_eligible_tags(
        "gelbooru",
        tag_database,
        TagSearch(text=text, mode="exact", category=category, limit=1),
    )
    rows = search_eligible_tags(
        "gelbooru",
        tag_database,
        TagSearch(
            text=text,
            mode="contains",
            category=category,
            minimum_count=AUTOCOMPLETE_MIN_POST_COUNT,
            limit=candidate_limit,
        ),
        rank_for_autocomplete=True,
    )
    raw_rows = [*exact_rows, *rows]
    active_source_names = {
        normalize_booru_tag(source).casefold() for source in alias_sources
    }
    if alias_repository is not None:
        active_source_names.update(
            alias_repository.active_source_names([row.name for row in raw_rows])
        )
    for row in raw_rows:
        if normalize_booru_tag(row.name).casefold() not in active_source_names:
            candidates.append(TagLookupSuggestion(row.name, None, row.category, row.post_count))

    def rank(item: TagLookupSuggestion) -> tuple[int, int, str, str]:
        canonical = item.value.casefold()
        alias = item.alias_source.casefold() if item.alias_source else ""
        match_quality = (
            0 if canonical == query or alias == query else
            1 if canonical.startswith(query) or alias.startswith(query) else 2
        )
        display_name = alias or canonical
        return match_quality, -item.post_count, display_name, canonical

    seen: set[str] = set()
    for suggestion in sorted(candidates, key=rank):
        key = normalize_booru_tag(suggestion.value)
        if key not in seen:
            seen.add(key)
            suggestions.append(suggestion)
        if len(suggestions) >= limit:
            break
    return suggestions


def lookup_autocomplete_tags(
    site: str,
    database: Path,
    text: str,
    *,
    alias_database: Path | None = None,
    category: int | None = None,
    limit: int = 20,
) -> list[TagRow] | list[TagLookupSuggestion]:
    """Return only popular, non-deprecated autocomplete candidates."""
    if site == "gelbooru":
        return lookup_gelbooru_suggestions(
            database,
            alias_database,
            text,
            category=category,
            limit=limit,
        )
    return lookup_tags(site, database, text, category=category, limit=limit)
