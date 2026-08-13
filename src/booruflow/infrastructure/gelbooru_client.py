"""Small Gelbooru DAPI client shared by GUI and legacy scanners."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

API_URL = "https://gelbooru.com/index.php"
DEFAULT_USER_AGENT = "ArtistTagScanner/1.0 (personal Gelbooru library tool)"


def normalize_posts(data: Any) -> list[dict[str, Any]]:
    """Accept the JSON shapes returned by Gelbooru's DAPI."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    posts = data.get("post", [])
    if isinstance(posts, dict):
        return [posts]
    if isinstance(posts, list):
        return [item for item in posts if isinstance(item, dict)]
    return []


def fetch_page(
    query: str,
    page: int,
    limit: int,
    user_id: str,
    api_key: str,
) -> tuple[list[dict[str, Any]], int]:
    params = {
        "page": "dapi",
        "s": "post",
        "q": "index",
        "json": "1",
        "tags": query,
        "limit": str(limit),
        "pid": str(page),
    }
    if user_id and api_key:
        params["user_id"] = user_id
        params["api_key"] = api_key

    request = urllib.request.Request(
        f"{API_URL}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
    )
    response_text = ""
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
        response_text = raw.decode(charset, errors="replace")
        data = json.loads(response_text)
    except json.JSONDecodeError as exc:
        preview = response_text[:300].replace("\n", " ")
        raise RuntimeError(
            f"Réponse non JSON pour la requête {query!r}, page {page}: {preview}"
        ) from exc

    total = 0
    if isinstance(data, dict):
        try:
            total = int(data.get("@attributes", {}).get("count", 0))
        except (TypeError, ValueError):
            pass
    return normalize_posts(data), total


def fetch_result_count(
    query: str,
    user_id: str,
    api_key: str,
) -> tuple[int, list[dict[str, Any]]]:
    """Return a non-mutating count plus Gelbooru's single witness post."""
    posts, total = fetch_page(query, 0, 1, user_id, api_key)
    return total, posts
