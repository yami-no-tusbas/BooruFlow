"""Synchronous Gelbooru reader used by the cancellable Qt worker."""

from __future__ import annotations

import html
import json
import urllib.parse
import urllib.request
from collections.abc import Callable

from booruflow.application.tagging import TaggingRequest, tagging_priority


def post_tags(post: dict) -> list[str]:
    value = post.get("tags", post.get("tag_string", ""))
    if isinstance(value, list):
        return [str(tag) for tag in value if str(tag).strip()]
    return [tag for tag in html.unescape(str(value)).split() if tag]


def payload_posts(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [post for post in payload if isinstance(post, dict)]
    if isinstance(payload, dict):
        posts = payload.get("post", [])
        if isinstance(posts, dict):
            return [posts]
        if isinstance(posts, list):
            return [post for post in posts if isinstance(post, dict)]
    return []


class GelbooruTaggingScanner:
    def scan(
        self,
        request: TaggingRequest,
        user_id: str,
        api_key: str,
        *,
        cancelled: Callable[[], bool] = lambda: False,
        progress: Callable[[int, int, int, int, int], None] = lambda *_args: None,
    ) -> tuple[list[dict], int, int, bool]:
        selected: list[dict] = []
        examined = 0
        block_start = request.start_page
        reached_end = False
        next_page = block_start
        while not selected and not reached_end and not cancelled():
            for block_index in range(request.pages_per_block):
                if cancelled():
                    break
                page = block_start + block_index
                parameters = {
                    "page": "dapi", "s": "post", "q": "index", "json": "1",
                    "limit": "100", "pid": str(page - 1), "tags": request.query,
                    "user_id": user_id, "api_key": api_key,
                }
                url = "https://gelbooru.com/index.php?" + urllib.parse.urlencode(parameters)
                http_request = urllib.request.Request(
                    url,
                    headers={"User-Agent": "BooruFlow/0.1", "Referer": "https://gelbooru.com/"},
                )
                with urllib.request.urlopen(http_request, timeout=30) as response:
                    posts = payload_posts(json.loads(response.read().decode("utf-8", errors="replace")))
                for post in posts:
                    count = len(post_tags(post))
                    examined += 1
                    if request.minimum_tags <= count <= request.maximum_tags:
                        item = dict(post)
                        item["tag_count"] = count
                        item["priority"] = tagging_priority(
                            count, request.critical_maximum, request.high_maximum
                        )
                        selected.append(item)
                next_page = page + 1
                progress(page, block_index + 1, request.pages_per_block, examined, len(selected))
                if len(posts) < 100:
                    reached_end = True
                    break
            block_start += request.pages_per_block
        return selected, examined, next_page, reached_end
