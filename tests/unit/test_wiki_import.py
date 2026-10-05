import sqlite3
from pathlib import Path

import pytest

from booruflow.application.wiki_import import (
    DanbooruToGelbooruConverter,
    DanbooruWikiParser,
    GelbooruTagResolver,
)
from booruflow.infrastructure.wiki_clients import DanbooruWikiClient, GelbooruWikiClient
from booruflow.infrastructure.wiki_tag_importer import WikiFetchResponse, WikiPageNotFoundError


def databases(tmp_path: Path) -> tuple[Path, Path]:
    tags = tmp_path / "tags.db"
    with sqlite3.connect(tags) as connection:
        connection.execute("CREATE TABLE tags(name TEXT PRIMARY KEY, post_count INTEGER, category INTEGER)")
        connection.executemany(
            "INSERT INTO tags VALUES(?,?,?)",
            [("canonical_tag", 100, 0), ("known_tag", 50, 0)],
        )
    aliases = tmp_path / "aliases.db"
    with sqlite3.connect(aliases) as connection:
        connection.execute(
            "CREATE TABLE gelbooru_aliases(source_name TEXT, target_name TEXT, status TEXT)"
        )
        connection.execute(
            "INSERT INTO gelbooru_aliases VALUES('old_tag','canonical_tag','active')"
        )
    return tags, aliases


def test_basic_dtext_conversion_alias_and_unknown_preservation(tmp_path: Path) -> None:
    tags, aliases = databases(tmp_path)
    source = """h1. Overview
* [[old_tag]]
* [[unknown_tag]]

[b]Bold[/b] and [i]italic[/i], [quote]quoted[/quote], [spoiler]hidden[/spoiler].
"Official":https://example.test/page
"""
    converter = DanbooruToGelbooruConverter(GelbooruTagResolver(tags, aliases))
    result = converter.convert(DanbooruWikiParser().parse(source), "Current Tag")

    assert result.text.startswith("{{current_tag}}\n[h2]Overview[/h2]\n* [[canonical_tag]]")
    assert "* [[unknown_tag]]" in result.text
    assert "[h1]" not in result.text
    assert "Bold" in result.text and "https://example.test/page" in result.text
    assert any(item.reason == "gelbooru_alias" for item in result.tag_conversions)
    assert any(item.source == "unknown_tag" and item.reason == "unresolved" for item in result.tag_conversions)
    assert any("original preserved" in warning for warning in result.warnings)


def test_unsupported_dtext_is_preserved_and_warned(tmp_path: Path) -> None:
    tags, aliases = databases(tmp_path)
    result = DanbooruToGelbooruConverter(GelbooruTagResolver(tags, aliases)).convert(
        DanbooruWikiParser().parse("[section=More]kept[/section]"), "known_tag"
    )
    assert "[section=More]kept[/section]" in result.text
    assert any("[section]" in warning for warning in result.warnings)


def test_danbooru_client_uses_exact_title_and_raw_body() -> None:
    client = DanbooruWikiClient(
        fetcher=lambda _url: [
            {"id": 1, "title": "tag_extra", "body": "wrong"},
            {"id": 2, "title": "Exact_Tag", "body": "h2. Raw"},
        ]
    )
    page = client.fetch("exact tag")
    assert page.text == "h2. Raw"
    assert page.source_url.endswith("/2")


def test_danbooru_client_reports_missing_wiki() -> None:
    with pytest.raises(WikiPageNotFoundError):
        DanbooruWikiClient(fetcher=lambda _url: []).fetch("missing")


def test_gelbooru_client_loads_exact_edit_textarea_source() -> None:
    direct_url = "https://gelbooru.com/index.php?page=wiki&s=view&id=7"
    direct = (
        "<html><body><h2>Now Viewing: exact_tag</h2>"
        "<div>Tag type: General</div><p>Rendered text</p>"
        "<div>Other Wiki Information</div></body></html>"
    )
    edit = '<html><textarea name="body">{{exact_tag}}\n[h2]Raw[/h2]</textarea></html>'
    responses = iter((
        WikiFetchResponse(direct_url, direct_url, 200, direct),
        WikiFetchResponse("https://edit", "https://edit", 200, edit),
    ))
    page = GelbooruWikiClient(fetcher=lambda _url: next(responses)).fetch("exact_tag")
    assert page.text == "{{exact_tag}}\n[h2]Raw[/h2]"
    assert page.wiki_id == 7
    assert page.warnings == ()
