from __future__ import annotations

import ast
from pathlib import Path

import pytest

from booruflow.application.image_finder import ImageFinderService
from booruflow.domain.image_finder import RemoteMediaType, SearchMode, SearchQuery
from booruflow.infrastructure.pixiv import (
    PixivAuthenticationError,
    PixivSource,
    parse_artwork,
)


def pixiv_artwork(**changes):
    payload = {
        "id": 123,
        "title": "A title",
        "caption": "A caption",
        "type": "illust",
        "x_restrict": 0,
        "create_date": "2026-01-02T03:04:05+00:00",
        "width": 1200,
        "height": 800,
        "user": {"id": 42, "name": "Artist"},
        "tags": [{"name": "cat"}, {"name": "blue_hair"}],
        "image_urls": {"small": "small", "medium": "medium", "large": "large"},
        "meta_single_page": {"original_image_url": "original"},
        "meta_pages": [],
    }
    payload.update(changes)
    return payload


def test_parse_pixiv_simple_artwork_and_artist():
    artwork = parse_artwork(pixiv_artwork())
    assert artwork.source == "pixiv"
    assert artwork.source_post_id == "123"
    assert artwork.artist.artist_id == "42"
    assert artwork.artist.profile_url == "https://www.pixiv.net/users/42"
    assert artwork.tags == ("cat", "blue_hair")
    assert artwork.rating == "safe"
    assert artwork.images[0].original_url == "original"


def test_parse_pixiv_multipage_preserves_parent_and_page_indices():
    pages = [
        {"image_urls": {"small": f"s{index}", "medium": f"m{index}", "original": f"o{index}"}}
        for index in range(3)
    ]
    artwork = parse_artwork(pixiv_artwork(meta_pages=pages, page_count=3))
    assert artwork.page_count == 3
    assert [(image.artwork_id, image.page_index) for image in artwork.images] == [
        ("123", 0), ("123", 1), ("123", 2)
    ]
    assert artwork.images[2].original_url == "o2"


@pytest.mark.parametrize("x_restrict,expected", [(0, "safe"), (1, "explicit"), (2, "explicit")])
def test_parse_pixiv_rating(x_restrict, expected):
    assert parse_artwork(pixiv_artwork(x_restrict=x_restrict)).rating == expected


def test_parse_pixiv_missing_optional_fields_and_ugoira():
    artwork = parse_artwork(pixiv_artwork(
        title=None, caption=None, tags=None, user=None, create_date=None, type="ugoira"
    ))
    assert artwork.title == ""
    assert artwork.created_at is None
    assert artwork.images[0].media_type is RemoteMediaType.UGOIRA


def test_pixiv_search_modes_pagination_and_authorization_header():
    calls = []
    def request(method, url, headers, data):
        calls.append((method, url, headers, data))
        if "auth/token" in url:
            return {"access_token": "SECRET_ACCESS", "refresh_token": "NEW_REFRESH"}
        return {"illusts": [pixiv_artwork()], "next_url": "https://next"}
    source = PixivSource("SECRET_REFRESH", json_request=request)
    result = source.search(SearchQuery("cat girl", SearchMode.TITLE_CAPTION))
    assert result.next_cursor == "https://next"
    assert "search_target=title_and_caption" in calls[1][1]
    assert calls[1][2]["Authorization"] == "Bearer SECRET_ACCESS"
    assert source.refresh_token == "NEW_REFRESH"


def test_pixiv_artist_search_uses_user_endpoint():
    urls = []
    def request(_method, url, _headers, _data):
        urls.append(url)
        return {"illusts": []}
    source = PixivSource("token", json_request=request); source.access_token = "access"
    source.search(SearchQuery("", artist_id="42"))
    assert "/v1/user/illusts?" in urls[0]
    assert "user_id=42" in urls[0]


def test_refresh_failure_message_never_contains_token():
    def request(*_args):
        raise PixivAuthenticationError("rejected")
    with pytest.raises(PixivAuthenticationError) as error:
        PixivSource("VERY_SECRET", json_request=request).refresh_access_token()
    assert "VERY_SECRET" not in str(error.value)


def test_generic_service_has_no_pixiv_to_gelbooru_dependency():
    tree = ast.parse(Path("src/booruflow/application/image_finder.py").read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any("pixiv" in (name or "") or "gelbooru" in (name or "") for name in imported)
    assert ImageFinderService(()) .source_ids == ()
