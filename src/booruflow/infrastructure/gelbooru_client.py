"""Small Gelbooru DAPI client shared by GUI and legacy scanners."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

API_URL = "https://gelbooru.com/index.php"
DEFAULT_USER_AGENT = "ArtistTagScanner/1.0 (personal Gelbooru library tool)"


class GelbooruAuthenticationError(RuntimeError):
    """Gelbooru rejected or requires the configured DAPI identity."""


PageFetcher = Callable[[str, int, int, str, str], tuple[list[dict[str, Any]], int]]


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
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise GelbooruAuthenticationError(
                "Gelbooru API authentication is required or invalid."
            ) from exc
        raise
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


class GelbooruDapiClient:
    """Authenticated, read-only facade over BooruFlow's canonical DAPI transport."""

    def __init__(
        self,
        user_id: str = "",
        api_key: str = "",
        *,
        page_fetcher: PageFetcher = fetch_page,
    ) -> None:
        self.user_id = user_id.strip()
        self.api_key = api_key.strip()
        self.page_fetcher = page_fetcher

    @property
    def authenticated(self) -> bool:
        return bool(self.user_id and self.api_key)

    def search_posts(self, query: str, *, limit: int = 100, page: int = 0) -> list[dict[str, Any]]:
        posts, _total = self.page_fetcher(
            query, page, limit, self.user_id, self.api_key
        )
        return posts

    def fetch_post(self, post_id: int | str) -> dict[str, Any]:
        posts = self.search_posts(f"id:{post_id}", limit=1)
        if not posts:
            raise LookupError(f"Gelbooru post {post_id} was not found")
        return posts[0]

    def random_post(self) -> dict[str, Any]:
        posts = self.search_posts("sort:random", limit=1)
        if not posts:
            raise LookupError("Gelbooru returned no random post")
        return posts[0]


def fetch_result_count(
    query: str,
    user_id: str,
    api_key: str,
) -> tuple[int, list[dict[str, Any]]]:
    """Return a non-mutating count plus Gelbooru's single witness post."""
    posts, total = fetch_page(query, 0, 1, user_id, api_key)
    return total, posts
