"""Site-explicit, widget-free tag autocomplete service."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from booruflow.application.tag_lookup import (
    AUTOCOMPLETE_MIN_CHARS,
    exact_tag,
    lookup_autocomplete_tags,
)
from booruflow.application.tagging import normalize_booru_tag
from booruflow.domain.booru_sites import category_name, resolve_category

PREFIX_ROLES = {
    "char": "character",
    "copy": "copyright",
    "arti": "artist",
    "spec": "species",
}


@dataclass(frozen=True, slots=True)
class AutocompleteRequest:
    owner: str
    generation: int
    site: str
    text: str
    category_filter: str | None = None
    limit: int = 20


@dataclass(frozen=True, slots=True)
class TagSuggestion:
    site: str
    name: str
    canonical_name: str
    category: str
    post_count: int
    alias_source: str | None = None
    matched_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedAutocompleteText:
    text: str
    negative: bool
    category_role: str | None
    matched_prefix: str | None


def parse_autocomplete_text(value: str) -> ParsedAutocompleteText:
    raw = value.strip()
    negative = raw.startswith("-")
    unsigned = raw.removeprefix("-")
    prefix, separator, remainder = unsigned.partition(":")
    role = PREFIX_ROLES.get(prefix.casefold()) if separator else None
    return ParsedAutocompleteText(
        remainder if role else unsigned,
        negative,
        role,
        f"{prefix.casefold()}:" if role else None,
    )


class TagAutocompleteService:
    """Read bounded suggestions from the catalogue selected by the request site."""

    def __init__(
        self,
        databases: dict[str, Path | None],
        alias_databases: dict[str, Path | None] | None = None,
    ) -> None:
        self.databases = dict(databases)
        self.alias_databases = dict(alias_databases or {})

    def update_catalogues(
        self,
        databases: dict[str, Path | None],
        alias_databases: dict[str, Path | None] | None = None,
    ) -> None:
        self.databases = dict(databases)
        if alias_databases is not None:
            self.alias_databases = dict(alias_databases)

    def suggestions(self, request: AutocompleteRequest) -> list[TagSuggestion]:
        parsed = parse_autocomplete_text(request.text)
        if len(parsed.text) < AUTOCOMPLETE_MIN_CHARS:
            return []
        database = self.databases.get(request.site)
        if database is None or not database.is_file():
            return []
        role = request.category_filter or parsed.category_role
        category_id = resolve_category(request.site, role) if role else None
        if role and category_id is None:
            return []
        try:
            rows = lookup_autocomplete_tags(
                request.site,
                database,
                parsed.text,
                alias_database=self.alias_databases.get(request.site),
                category=category_id,
                limit=request.limit,
            )
        except (FileNotFoundError, OSError, ValueError, sqlite3.Error):
            # Missing/corrupt optional catalogues must never block an input field.
            return []
        suggestions: list[TagSuggestion] = []
        for row in rows:
            name = str(getattr(row, "value", getattr(row, "name", "")))
            suggestions.append(TagSuggestion(
                site=request.site,
                name=name,
                canonical_name=name,
                category=category_name(request.site, getattr(row, "category", None)),
                post_count=int(getattr(row, "post_count", 0)),
                alias_source=getattr(row, "alias_source", None),
                matched_prefix=parsed.matched_prefix,
            ))
        return suggestions

    def exact(self, site: str, text: str) -> TagSuggestion | None:
        database = self.databases.get(site)
        if database is None or not database.is_file():
            return None
        try:
            row = exact_tag(site, database, normalize_booru_tag(text))
        except (FileNotFoundError, OSError, ValueError, sqlite3.Error):
            return None
        if row is None:
            return None
        return TagSuggestion(
            site=site,
            name=row.name,
            canonical_name=row.name,
            category=category_name(site, row.category),
            post_count=row.post_count,
        )
