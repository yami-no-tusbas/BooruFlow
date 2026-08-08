"""Wiki definitions and small post samples for taxonomy inspection."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from legacy.wiki_tag_importer import tag_definition


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


def _gelbooru_samples(tag: str, user_id: str, api_key: str) -> list[dict]:
    parameters = {
        "page": "dapi", "s": "post", "q": "index", "json": "1",
        "limit": "6", "tags": tag,
    }
    if user_id: parameters["user_id"] = user_id
    if api_key: parameters["api_key"] = api_key
    payload = _request_json(
        "https://gelbooru.com/index.php?" + urllib.parse.urlencode(parameters),
        "https://gelbooru.com/",
    )
    posts = payload if isinstance(payload, list) else payload.get("post", []) if isinstance(payload, dict) else []
    return [
        {
            "id": int(post.get("id", 0)),
            "preview_url": str(post.get("preview_url") or post.get("sample_url") or ""),
            "post_url": f"https://gelbooru.com/index.php?page=post&s=view&id={int(post.get('id', 0))}",
        }
        for post in posts if isinstance(post, dict)
    ]


def _e621_samples(tag: str) -> list[dict]:
    payload = _request_json(
        "https://e621.net/posts.json?" + urllib.parse.urlencode({"limit": 6, "tags": tag}),
        "https://e621.net/",
    )
    posts = payload.get("posts", []) if isinstance(payload, dict) else []
    samples = []
    for post in posts:
        preview = post.get("preview", {}) if isinstance(post, dict) else {}
        post_id = int(post.get("id", 0)) if isinstance(post, dict) else 0
        samples.append({
            "id": post_id,
            "preview_url": str(preview.get("url") or "") if isinstance(preview, dict) else "",
            "post_url": f"https://e621.net/posts/{post_id}",
        })
    return samples


def fetch_tag_details(
    board: str, tag: str, cache_path: Path, user_id: str = "", api_key: str = "",
) -> dict:
    cache = TagDetailsCache(cache_path)
    cached = cache.load(board, tag)
    details = dict(cached)
    errors: list[str] = []
    online = False
    try:
        definition, wiki_url = tag_definition(board, tag)
        details.update({"definition": definition, "wiki_url": wiki_url})
        online = True
    except Exception as exc:
        errors.append(str(exc))
    try:
        samples = _e621_samples(tag) if board == "e621" else _gelbooru_samples(tag, user_id, api_key)
        details["samples"] = samples
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
