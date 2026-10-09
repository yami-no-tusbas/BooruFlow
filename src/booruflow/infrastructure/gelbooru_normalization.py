"""Decode Gelbooru wire tags once, before any lookup or consumer sees them."""
from __future__ import annotations

from html import unescape

UNICODE_TAGS = "_booruflow_unicode_tags"
TAG_FIELDS = ("tags", "tag_string", "tag_string_artist", "tag_string_character",
              "tag_string_copyright", "tag_string_general", "tag_string_meta")


def decode_tag(value: str) -> str:
    """One HTML5 decoding pass; never iterate until a fixed point."""
    return unescape(value)


def preferred_category(values):
    """Known category wins over an unknown one; input order breaks ties."""
    return next((value for value in values if value not in (None, "", "unknown")),
                next(iter(values), None))


def normalize_post(post: dict) -> dict:
    if post.get(UNICODE_TAGS) == 1:
        return post
    result = dict(post)
    for field in TAG_FIELDS:
        value = post.get(field)
        if isinstance(value, str):
            result[field] = decode_tag(value)
        elif isinstance(value, list):
            result[field] = list(dict.fromkeys(decode_tag(str(tag)) for tag in value))
    categories = post.get("_tag_categories")
    if isinstance(categories, dict):
        groups = {}
        for tag, category in categories.items():
            groups.setdefault(decode_tag(tag), []).append(category)
        result["_tag_categories"] = {tag: preferred_category(values) for tag, values in groups.items()}
    if any(field in post for field in TAG_FIELDS) or isinstance(categories, dict):
        # Survives dict(post), GUI signals and serialization of search results.
        result[UNICODE_TAGS] = 1
    return result


def post_tags(post: dict) -> list[str]:
    post = normalize_post(post)
    value = post.get("tags", post.get("tag_string", ""))
    tags = value if isinstance(value, list) else str(value).split()
    return list(dict.fromkeys(str(tag) for tag in tags if str(tag).strip()))
