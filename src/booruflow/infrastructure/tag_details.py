"""Wiki definitions and small post samples for taxonomy inspection."""

from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from booruflow.infrastructure.wiki_page_cache import WikiPageCache, WikiPageRecord
from booruflow.infrastructure.wiki_tag_importer import (
    WikiPageNotFoundError,
    fetch_wiki_page_details,
)


def _request_json(url: str, referer: str) -> object:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "BooruFlow/0.1", "Referer": referer},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


GELBOORU_META_FALLBACK = {
    "highres", "absurdres", "incredibly_absurdres", "lowres", "commentary",
    "commentary_request", "translated", "translation_request", "check_translation",
    "bad_id", "duplicate", "revision", "paid_reward",
}


def _recurring_tags(posts: list[dict], current_tag: str, extractor, excluded: set[str] | None = None) -> list[dict]:
    counts: Counter[str] = Counter()
    current = current_tag.casefold()
    excluded_folded = {value.casefold() for value in (excluded or set())}
    for post in posts:
        unique = {
            value for value in extractor(post)
            if value and value.casefold() != current and value.casefold() not in excluded_folded
        }
        counts.update(unique)
    return [
        {"tag": tag, "count": count}
        for tag, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))[:20]
    ]


def _gelbooru_post_tags(post: dict) -> list[str]:
    values = post.get("tags", post.get("tag_string", ""))
    if isinstance(values, str):
        return values.split()
    return [str(value) for value in values] if isinstance(values, list) else []


def _e621_post_tags(post: dict) -> list[str]:
    values = post.get("tags", {})
    if isinstance(values, str):
        return values.split()
    if isinstance(values, dict):
        return [
            str(tag) for category, group in values.items()
            if str(category).casefold() != "meta" and isinstance(group, list)
            for tag in group
        ]
    return []


def _gelbooru_meta_tags(database_path: Path | None) -> set[str]:
    result = set(GELBOORU_META_FALLBACK)
    if not database_path or not database_path.is_file():
        return result
    connection = None
    try:
        connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
        result.update(str(row[0]) for row in connection.execute("SELECT name FROM tags WHERE category = 5"))
    except sqlite3.Error:
        pass
    finally:
        if connection is not None: connection.close()
    return result


def _gelbooru_samples(tag: str, user_id: str, api_key: str, database_path: Path | None = None) -> dict:
    parameters = {
        "page": "dapi", "s": "post", "q": "index", "json": "1",
        "limit": "100", "tags": tag,
    }
    if user_id: parameters["user_id"] = user_id
    if api_key: parameters["api_key"] = api_key
    payload = _request_json(
        "https://gelbooru.com/index.php?" + urllib.parse.urlencode(parameters),
        "https://gelbooru.com/",
    )
    posts = payload if isinstance(payload, list) else payload.get("post", []) if isinstance(payload, dict) else []
    samples = [
        {
            "id": int(post.get("id", 0)),
            "preview_url": str(post.get("preview_url") or post.get("sample_url") or ""),
            "post_url": f"https://gelbooru.com/index.php?page=post&s=view&id={int(post.get('id', 0))}",
        }
        for post in posts[:6] if isinstance(post, dict)
    ]
    valid_posts = [post for post in posts if isinstance(post, dict)]
    return {
        "samples": samples,
        "sample_size": len(valid_posts),
        "recurring": _recurring_tags(valid_posts, tag, _gelbooru_post_tags, _gelbooru_meta_tags(database_path)),
    }


def _e621_samples(tag: str) -> dict:
    payload = _request_json(
        "https://e621.net/posts.json?" + urllib.parse.urlencode({"limit": 100, "tags": tag}),
        "https://e621.net/",
    )
    posts = payload.get("posts", []) if isinstance(payload, dict) else []
    samples = []
    for post in posts[:6]:
        preview = post.get("preview", {}) if isinstance(post, dict) else {}
        post_id = int(post.get("id", 0)) if isinstance(post, dict) else 0
        samples.append({
            "id": post_id,
            "preview_url": str(preview.get("url") or "") if isinstance(preview, dict) else "",
            "post_url": f"https://e621.net/posts/{post_id}",
        })
    valid_posts = [post for post in posts if isinstance(post, dict)]
    return {"samples": samples, "sample_size": len(valid_posts), "recurring": _recurring_tags(valid_posts, tag, _e621_post_tags)}


def _display_content(record: WikiPageRecord) -> str:
    body = record.content
    if record.content_format == "dtext":
        body = re.sub(r"\[\[[^]|]+\|([^]]+)]]", r"\1", body)
        body = re.sub(r"\[\[([^]]+)]]", r"\1", body)
        body = re.sub(r"\[/?[^]]+]", "", body)
        body = re.sub(r"(?m)^h\d\.\s*", "", body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body[:6000]


def cached_tag_details(cache_database: Path, board: str, tag: str) -> dict | None:
    record = WikiPageCache(cache_database).get(board, tag)
    return _record_details(record, cache_hit=True) if record else None


def _record_details(
    record: WikiPageRecord,
    *,
    cache_hit: bool,
    refresh_failed: bool = False,
    errors: list[str] | None = None,
) -> dict:
    return {
        "board": record.site,
        "tag": record.tag,
        "definition": _display_content(record),
        "wiki_url": record.source_url,
        "wiki_tags": list(record.referenced_tags),
        "wiki_exists": True,
        "wiki_id": record.wiki_id,
        "tag_type": record.tag_type,
        "author": record.author,
        "remote_updated_at": record.remote_updated_at,
        "version": record.version,
        "cached_at": record.cached_at,
        "samples": list(record.samples),
        "recurring": list(record.recurring),
        "sample_size": record.sample_size,
        "cache_hit": cache_hit,
        "refresh_failed": refresh_failed,
        "online": not cache_hit,
        "errors": list(errors or []),
    }


def fetch_tag_details(
    board: str,
    tag: str,
    cache_database: Path,
    user_id: str = "",
    api_key: str = "",
    tag_database_path: Path | None = None,
    wiki_url: str = "",
    *,
    force: bool = False,
) -> dict:
    """Load one page cache-first, or refresh it without risking the old cache."""
    cache = WikiPageCache(cache_database)
    previous = cache.get(board, tag)
    if previous is not None and not force:
        return _record_details(previous, cache_hit=True)

    try:
        page = fetch_wiki_page_details(board, tag, wiki_url)
    except WikiPageNotFoundError:
        remote_url = wiki_url or (
            "https://e621.net/wiki_pages/show_or_new?"
            + urllib.parse.urlencode({"title": tag})
            if board == "e621"
            else "https://gelbooru.com/index.php?"
            + urllib.parse.urlencode({"page": "wiki", "s": "list", "search": tag})
        )
        return {
            "board": board,
            "tag": tag,
            "wiki_url": remote_url,
            "wiki_exists": False,
            "cache_hit": False,
            "online": True,
            "errors": [],
            "samples": [],
            "recurring": [],
            "sample_size": 0,
        }
    except Exception as exc:  # noqa: BLE001 - remote/parser boundary
        if previous is not None:
            return _record_details(
                previous, cache_hit=True, refresh_failed=True, errors=[str(exc)]
            )
        return {
            "board": board,
            "tag": tag,
            "wiki_url": wiki_url,
            "wiki_exists": None,
            "cache_hit": False,
            "online": False,
            "errors": [str(exc)],
            "samples": [],
            "recurring": [],
            "sample_size": 0,
        }

    errors: list[str] = []
    sample_data = {
        "samples": list(previous.samples) if previous else [],
        "recurring": list(previous.recurring) if previous else [],
        "sample_size": previous.sample_size if previous else 0,
    }
    if not force:
        try:
            sample_data = (
                _e621_samples(tag)
                if board == "e621"
                else _gelbooru_samples(tag, user_id, api_key, tag_database_path)
            )
        except Exception as exc:  # noqa: BLE001 - independent sample source
            errors.append(str(exc))
    record = cache.put(WikiPageRecord(
        site=board,
        tag=tag,
        content=str(page["content"]),
        source_url=str(page["source_url"]),
        content_format=str(page.get("content_format", "plain")),
        wiki_id=page.get("wiki_id"),
        tag_type=page.get("tag_type"),
        author=page.get("author"),
        remote_updated_at=page.get("remote_updated_at"),
        version=page.get("version"),
        referenced_tags=tuple(page.get("referenced_tags", [])),
        samples=tuple(sample_data.get("samples", [])),
        recurring=tuple(sample_data.get("recurring", [])),
        sample_size=int(sample_data.get("sample_size", 0)),
    ))
    details = _record_details(record, cache_hit=False, errors=errors)
    details["refreshed"] = force
    details["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    return details
