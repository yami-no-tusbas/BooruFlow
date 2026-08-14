"""Conservative Gelbooru copyright-alias inference for wiki audits."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from booruflow.infrastructure.gelbooru_client import fetch_page


@dataclass(frozen=True)
class CopyrightAliasResult:
    requested_tag: str
    status: str
    canonical_tag: str | None
    common_copyrights: tuple[str, ...]
    sampled_posts: int


def _post_tags(post: dict[str, Any]) -> set[str]:
    raw = post.get("tags", "")
    if isinstance(raw, str):
        return {tag for tag in raw.split() if tag}
    if isinstance(raw, list):
        return {str(tag).strip() for tag in raw if str(tag).strip()}
    return set()


def infer_copyright_alias(
    requested_tag: str,
    posts: list[dict[str, Any]],
    database_path: Path,
    *,
    minimum_posts: int = 3,
) -> CopyrightAliasResult:
    """Infer an alias only when one copyright is shared by every witness post."""
    requested = requested_tag.strip()
    tag_sets = [_post_tags(post) for post in posts]
    tag_sets = [tags for tags in tag_sets if tags]
    if len(tag_sets) < minimum_posts:
        return CopyrightAliasResult(requested, "insufficient_samples", None, (), len(tag_sets))
    if any(requested in tags for tags in tag_sets):
        return CopyrightAliasResult(requested, "requested_tag_present", None, (), len(tag_sets))

    all_names = sorted(set().union(*tag_sets))
    with sqlite3.connect(database_path) as connection:
        copyright_names: set[str] = set()
        for offset in range(0, len(all_names), 900):
            chunk = all_names[offset : offset + 900]
            placeholders = ",".join("?" for _ in chunk)
            copyright_names.update(
                name for name, in connection.execute(
                    f"SELECT name FROM tags WHERE category = 3 AND name IN ({placeholders})",
                    chunk,
                )
            )

    per_post = [tags & copyright_names for tags in tag_sets]
    common = set.intersection(*per_post) if per_post else set()
    common.discard(requested)
    candidates = tuple(sorted(common))
    if len(candidates) == 1:
        return CopyrightAliasResult(requested, "alias", candidates[0], candidates, len(tag_sets))
    if not candidates:
        return CopyrightAliasResult(requested, "no_common_copyright", None, (), len(tag_sets))
    return CopyrightAliasResult(requested, "ambiguous", None, candidates, len(tag_sets))


def resolve_copyright_alias(
    requested_tag: str,
    database_path: Path,
    user_id: str,
    api_key: str,
    *,
    sample_size: int = 20,
    fetcher: Callable[[str, int, int, str, str], tuple[list[dict[str, Any]], int]] = fetch_page,
) -> CopyrightAliasResult:
    """Fetch lightweight JSON witnesses, without downloading image data."""
    posts, _total = fetcher(requested_tag.strip(), 0, sample_size, user_id, api_key)
    return infer_copyright_alias(requested_tag, posts, database_path)

