"""Shared local tag lookup with conservative eligibility policy."""

from __future__ import annotations

from pathlib import Path

from booruflow.application.tag_policy import is_deprecated
from booruflow.infrastructure.tag_browser import TagRow, TagSearch, search_tags


def search_eligible_tags(
    site: str, database: Path, request: TagSearch
) -> list[TagRow]:
    """Run the canonical tag search and remove only confirmed deprecated rows."""
    return [
        row for row in search_tags(database, request)
        if not is_deprecated(site, row.category)
    ]


def lookup_tags(
    site: str, database: Path, text: str, *, limit: int = 20
) -> list[TagRow]:
    return search_eligible_tags(
        site, database, TagSearch(text=text, mode="contains", limit=limit)
    )


def exact_tag(site: str, database: Path, text: str) -> TagRow | None:
    rows = search_eligible_tags(
        site, database, TagSearch(text=text, mode="exact", limit=1)
    )
    return rows[0] if rows else None


def eligible_tag(site: str, row: TagRow | None) -> bool:
    return row is not None and not is_deprecated(site, row.category)
