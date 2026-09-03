"""Small explicit definitions for BooruFlow's supported booru sites."""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BooruSiteDefinition:
    site_id: str
    display_name: str
    base_url: str
    account_url: str
    database_setting_key: str
    categories: dict[int, str]

    def post_url(self, post_id: str | int) -> str:
        if self.site_id == "gelbooru":
            return f"{self.base_url}/index.php?page=post&s=view&id={post_id}"
        return f"{self.base_url}/posts/{post_id}"

    def search_url(self, tag: str) -> str:
        query = urllib.parse.urlencode({"tags": tag})
        if self.site_id == "gelbooru":
            return f"{self.base_url}/index.php?page=post&s=list&{query}"
        return f"{self.base_url}/posts?{query}"


SITES = {
    "gelbooru": BooruSiteDefinition(
        "gelbooru", "Gelbooru", "https://gelbooru.com",
        "https://gelbooru.com/index.php?page=account&s=home",
        "gelbooru_tag_database",
        {0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "meta", 6: "deprecated"},
    ),
    "e621": BooruSiteDefinition(
        "e621", "e621", "https://e621.net", "https://e621.net/users/home",
        "e621_database",
        {0: "general", 1: "artist", 2: "contributor", 3: "copyright", 4: "character", 5: "species", 7: "meta", 8: "lore"},
    ),
}


def site_definition(site: str) -> BooruSiteDefinition:
    try:
        return SITES[site.casefold()]
    except KeyError as exc:
        raise ValueError(f"unsupported booru site: {site}") from exc


def category_name(site: str, category: int | str | None) -> str:
    try:
        category_id = int(category) if category is not None else -1
    except (TypeError, ValueError):
        name = str(category or "unknown").casefold()
        return {"metadata": "meta"}.get(name, name)
    return site_definition(site).categories.get(category_id, "unknown")
