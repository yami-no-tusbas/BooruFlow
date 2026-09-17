"""Read-only e621 search normalized for the Tagging review workflow."""

from __future__ import annotations

from collections.abc import Callable

from booruflow.application.tagging import TaggingRequest, tagging_priority
from booruflow.domain.booru_sites import site_definition
from booruflow.infrastructure.e621_client import E621Client


def normalize_e621_post(post: dict) -> dict:
    groups = post.get("tags", {})
    names: list[str] = []
    categories: dict[str, int] = {}
    reverse = {name: category for category, name in site_definition("e621").categories.items()}
    if isinstance(groups, dict):
        for category_name, values in groups.items():
            if not isinstance(values, list):
                continue
            for value in values:
                name = str(value).strip()
                if name:
                    names.append(name)
                    categories[name] = reverse.get(str(category_name), -1)
    preview = post.get("preview", {})
    sample = post.get("sample", {})
    file_data = post.get("file", {})
    normalized = dict(post)
    file_url = str(file_data.get("url") or "") if isinstance(file_data, dict) else ""
    if not file_url and isinstance(sample, dict):
        file_url = str(sample.get("url") or "")
    normalized.update(
        tags=" ".join(names),
        tag_string=" ".join(names),
        tag_count=len(names),
        preview_url=str(preview.get("url") or "") if isinstance(preview, dict) else "",
        file_url=file_url,
        _tag_categories=categories,
    )
    return normalized


class E621TaggingScanner:
    def __init__(self, client: E621Client) -> None:
        self.client = client

    def scan(
        self,
        request: TaggingRequest,
        *,
        cancelled: Callable[[], bool] = lambda: False,
        progress: Callable[[int, int, int, int, int], None] = lambda *_args: None,
    ) -> tuple[list[dict], int, int, bool]:
        selected: list[dict] = []
        examined = 0
        page = request.start_page
        reached_end = False
        while not selected and not reached_end and not cancelled():
            for block_index in range(request.pages_per_block):
                if cancelled():
                    break
                posts = self.client.fetch_posts(tags=request.query, limit=100, page=page)
                for raw_post in posts:
                    post = normalize_e621_post(raw_post)
                    count = int(post["tag_count"])
                    examined += 1
                    if request.minimum_tags <= count <= request.maximum_tags:
                        post["priority"] = tagging_priority(
                            count, request.critical_maximum, request.high_maximum
                        )
                        selected.append(post)
                progress(page, block_index + 1, request.pages_per_block, examined, len(selected))
                page += 1
                if len(posts) < 100:
                    reached_end = True
                    break
        return selected, examined, page, reached_end
