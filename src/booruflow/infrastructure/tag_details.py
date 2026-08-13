"""Wiki definitions and small post samples for taxonomy inspection."""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from booruflow.infrastructure.wiki_tag_importer import tag_definition_details


class TagDetailsCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, board: str, tag: str) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            value = data.get(board, {}).get(tag, {})
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def save(self, board: str, tag: str, details: dict) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict): data = {}
        except (OSError, ValueError, TypeError):
            data = {}
        data.setdefault(board, {})[tag] = details
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


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


def fetch_tag_details(
    board: str, tag: str, cache_path: Path, user_id: str = "", api_key: str = "",
    tag_database_path: Path | None = None, wiki_url: str = "",
) -> dict:
    cache = TagDetailsCache(cache_path)
    cached = cache.load(board, tag)
    details = dict(cached)
    errors: list[str] = []
    online = False
    try:
        definition, resolved_wiki_url, wiki_tags = tag_definition_details(board, tag, wiki_url)
        details.update({"definition": definition, "wiki_url": resolved_wiki_url, "wiki_tags": wiki_tags})
        online = True
    except Exception as exc:
        errors.append(str(exc))
    try:
        sample_data = _e621_samples(tag) if board == "e621" else _gelbooru_samples(tag, user_id, api_key, tag_database_path)
        details.update(sample_data)
        online = True
    except Exception as exc:
        errors.append(str(exc))
    details.update({
        "board": board,
        "tag": tag,
        "online": online,
        "errors": errors,
        "cached": bool(cached) and not online,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    if online:
        cache.save(board, tag, {key: value for key, value in details.items() if key not in {"errors", "cached", "online"}})
    return details
