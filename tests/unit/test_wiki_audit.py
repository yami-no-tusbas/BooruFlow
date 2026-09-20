from __future__ import annotations

import urllib.error
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

import pytest

from booruflow.infrastructure.gelbooru_client import (
    GelbooruAuthenticationError,
    GelbooruDapiClient,
    fetch_page,
)
from booruflow.infrastructure.wiki_audit import (
    GelbooruWikiAuditService,
    normalize_wiki_text,
    page_kind,
    parse_direct_wiki,
    parse_last_updated,
    parse_post_reference,
    parse_wiki_list,
    random_post_id,
    summarize,
    wiki_text_metrics,
)
from booruflow.infrastructure.wiki_page_cache import WikiPageCache, WikiPageRecord
from booruflow.infrastructure.wiki_tag_importer import WikiFetchResponse

FIXTURES = Path(__file__).parents[1] / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_wiki_absent_and_neighbor_is_not_an_exact_match() -> None:
    status = parse_wiki_list(fixture("gelbooru_audit_list.html"), "mod")
    assert status.exists is False


def test_exact_wiki_in_multi_result_list_includes_metadata() -> None:
    status = parse_wiki_list(fixture("gelbooru_audit_list.html"), "model_sheet")
    assert (status.exists, status.wiki_id, status.version, status.updated_by) == (
        True, 13, 2, "Bob"
    )
    assert status.updated_at == datetime(2023, 10, 21, 5, 2, tzinfo=UTC)


def test_parentheses_and_underscores_are_compared_exactly() -> None:
    source = fixture("gelbooru_audit_list.html")
    assert parse_wiki_list(source, "ad(21)").wiki_id == 14
    assert parse_wiki_list(source, "model_sheet").wiki_id == 13
    assert parse_wiki_list(source, "model sheet").exists is False


def test_direct_unique_wiki_and_last_updated_footer() -> None:
    source = fixture("gelbooru_audit_direct.html")
    assert page_kind(source) == "direct"
    status = parse_direct_wiki(
        source, "1girl", "https://gelbooru.com/index.php?page=wiki&s=view&id=5479"
    )
    assert status.exists is True
    assert status.wiki_id == 5479
    assert status.updated_by == "Absolutixn"
    assert status.updated_at == datetime(2016, 1, 31, 23, 37, tzinfo=UTC)


def test_last_updated_parser() -> None:
    stamp, author = parse_last_updated("Last updated: 01/31/16 11:37 PM by Absolutixn")
    assert stamp == datetime(2016, 1, 31, 23, 37, tzinfo=UTC)
    assert author == "Absolutixn"


def test_shared_page_cache_hit_and_forced_refresh(tmp_path: Path) -> None:
    cache = WikiPageCache(tmp_path / "wiki.sqlite")
    calls = 0

    def fetcher(_url: str) -> tuple[str, str]:
        nonlocal calls
        calls += 1
        return fixture("gelbooru_audit_direct.html"), "?page=wiki&s=view&id=5479"

    service = GelbooruWikiAuditService(tmp_path / "tags.db", None, cache, fetcher=fetcher)
    cache.put(WikiPageRecord(
        "gelbooru", "1girl", "Cached editorial body", "https://wiki/5479",
        author="Cached author", remote_updated_at="2024-01-01T00:00:00+00:00", version=3,
    ))
    cached = service.wiki_status("1girl")
    assert calls == 0
    assert cached.word_count == 3
    assert cached.updated_by == "Cached author"
    refreshed = service.wiki_status("1girl", force=True)
    assert calls == 1
    assert refreshed.word_count > 0
    refreshed_record = cache.get("gelbooru", "1girl")
    assert refreshed_record is not None
    assert refreshed_record.content_format == "html"
    assert "An image" in refreshed_record.content


def test_cached_rich_wiki_preserves_html_and_metrics(tmp_path: Path) -> None:
    cache = WikiPageCache(tmp_path / "wiki.sqlite")
    rich = "<p>Rich <b>editorial</b> body.</p>"
    cache.put(WikiPageRecord(
        "gelbooru", "rich", rich, "https://wiki/1", content_format="html"
    ))
    status = GelbooruWikiAuditService(
        tmp_path / "tags.db", None, cache, fetcher=lambda _url: (_ for _ in ()).throw(
            AssertionError("network must not be used")
        )
    ).wiki_status("rich")
    assert status.content == rich
    assert status.content_format == "html"
    assert status.word_count == 3


def test_missing_wiki_is_not_reported_as_network_error(tmp_path: Path) -> None:
    def fetcher(_url: str) -> tuple[str, str]:
        return fixture("gelbooru_audit_list.html"), "https://gelbooru.com/?page=wiki&s=list"

    status = GelbooruWikiAuditService(
        tmp_path / "tags.db", None, WikiPageCache(tmp_path / "cache.db"), fetcher=fetcher
    ).wiki_status("definitely_missing", force=True)
    assert status.exists is False
    assert status.error is None
    assert status.word_count is None


def test_create_redirect_is_missing_without_incrementing_error_state(tmp_path: Path) -> None:
    def fetcher(_url: str) -> tuple[str, str]:
        return (
            fixture("gelbooru_audit_create.html"),
            "https://gelbooru.com/index.php?page=wiki&s=create&title=tesdf",
        )

    service = GelbooruWikiAuditService(
        tmp_path / "tags.db", None, WikiPageCache(tmp_path / "cache.db"), fetcher=fetcher
    )
    status = service.wiki_status("tesdf", force=True)
    assert (status.exists, status.error, status.word_count) == (False, None, None)


def test_refresh_error_uses_previous_shared_cache_without_overwriting_it(
    tmp_path: Path,
) -> None:
    cache = WikiPageCache(tmp_path / "wiki.sqlite")
    cache.put(WikiPageRecord(
        "gelbooru", "cached", "Old valid body", "https://wiki/1"
    ))

    def offline(_url: str) -> tuple[str, str]:
        raise OSError("offline")

    service = GelbooruWikiAuditService(
        tmp_path / "tags.db", None, cache, fetcher=offline
    )
    status = service.wiki_status("cached", force=True)
    assert status.exists is True
    assert status.content == "Old valid body"
    assert status.word_count == 3
    assert status.error == "offline"
    assert cache.get("gelbooru", "cached").content == "Old valid body"


def test_cached_empty_page_is_present_with_zero_words_and_no_network(tmp_path: Path) -> None:
    cache = WikiPageCache(tmp_path / "wiki.sqlite")
    cache.put(WikiPageRecord("gelbooru", "empty", "", "https://wiki/2"))

    def unexpected(_url: str) -> tuple[str, str]:
        raise AssertionError("network must not be used")

    status = GelbooruWikiAuditService(
        tmp_path / "tags.db", None, cache, fetcher=unexpected
    ).wiki_status("empty")
    assert status.exists is True
    assert status.word_count == 0


def test_network_error_is_kept_on_row(tmp_path: Path) -> None:
    tags = tmp_path / "tags.db"
    import sqlite3

    with sqlite3.connect(tags) as connection:
        connection.execute(
            "CREATE TABLE tags(id INTEGER, name TEXT, post_count INTEGER, category INTEGER, ambiguous INTEGER)"
        )
        connection.execute("INSERT INTO tags VALUES(1,'1girl',100,0,0)")

    def fetcher(url: str) -> tuple[str, str]:
        raise OSError("offline")

    def page_fetcher(_query, _page, _limit, _user_id, _api_key):
        return [{"id": 42, "tags": "1girl"}], 1

    service = GelbooruWikiAuditService(
        tags, None, WikiPageCache(tmp_path / "cache.db"),
        dapi_client=GelbooruDapiClient(page_fetcher=page_fetcher),
        fetcher=fetcher, workers=1,
    )
    result = service.audit_post(42)
    assert result.tags[0].wiki.error == "offline"


def test_production_audit_pipeline_keeps_redirect_history_and_exact_matches(
    tmp_path: Path,
) -> None:
    import sqlite3

    names = (
        "white_outline", "mahou_girls_precure!", "lilylily0601", "penguin",
        "rryy", "shirt", "Love_Live!",
    )
    tags = tmp_path / "tags.db"
    with sqlite3.connect(tags) as connection:
        connection.execute(
            "CREATE TABLE tags(id INTEGER, name TEXT, post_count INTEGER, "
            "category INTEGER, ambiguous INTEGER)"
        )
        connection.executemany(
            "INSERT INTO tags VALUES(?,?,?,?,?)",
            ((index, name, 100 - index, 0, 0) for index, name in enumerate(names, 1)),
        )

    requested_urls: list[str] = []
    missing = {"white_outline", "mahou_girls_precure!", "lilylily0601", "rryy"}

    def fetcher(url: str) -> WikiFetchResponse:
        requested_urls.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        operation = query.get("s", [""])[0]
        if operation == "list":
            tag = query.get("search", [""])[0]
            if tag in missing:
                create = "https://gelbooru.com/index.php?" + urllib.parse.urlencode(
                    {"page": "wiki", "s": "create", "title": tag}
                )
                return WikiFetchResponse(
                    url,
                    "https://gelbooru.com/index.php?page=account&s=home",
                    200,
                    "<html><h1>Account</h1></html>",
                    (create, "https://gelbooru.com/index.php?page=account&s=home"),
                )
            return WikiFetchResponse(
                url, url, 200, fixture("gelbooru_audit_control_list.html")
            )
        wiki_id = int(query["id"][0])
        title = {600: "shirt", 28419: "Love_Live!"}[wiki_id]
        direct = fixture("gelbooru_audit_direct.html").replace("1girl", title)
        return WikiFetchResponse(url, url, 200, direct)

    def page_fetcher(_query, _page, _limit, _user_id, _api_key):
        return [{"id": 42, "tags": " ".join(names)}], 1

    service = GelbooruWikiAuditService(
        tags,
        None,
        WikiPageCache(tmp_path / "cache.db"),
        dapi_client=GelbooruDapiClient(page_fetcher=page_fetcher),
        fetcher=fetcher,
        workers=1,
    )
    result = service.audit_post(42)
    by_name = {row.name: row.wiki for row in result.tags}

    for name in (*missing, "penguin"):
        assert by_name[name].exists is False
        assert by_name[name].error is None
        assert by_name[name].word_count is None
        assert service.cache.get("gelbooru", name) is None
    assert by_name["shirt"].wiki_id == 600
    assert by_name["Love_Live!"].wiki_id == 28419
    assert service.cache.get("gelbooru", "shirt") is not None
    assert service.cache.get("gelbooru", "Love_Live!") is not None
    assert "%21" in next(url for url in requested_urls if "mahou_girls_precure" in url)
    assert summarize(result.tags)[:4] == (7, 2, 5, 0)


def test_audit_cache_miss_is_written_and_second_audit_is_batch_cache_hit(
    tmp_path: Path,
) -> None:
    import sqlite3

    tags = tmp_path / "tags.db"
    with sqlite3.connect(tags) as connection:
        connection.execute(
            "CREATE TABLE tags(id INTEGER, name TEXT, post_count INTEGER, "
            "category INTEGER, ambiguous INTEGER)"
        )
        connection.execute("INSERT INTO tags VALUES(1,'1girl',100,0,0)")
    fetch_calls = 0

    def fetcher(_url: str) -> tuple[str, str]:
        nonlocal fetch_calls
        fetch_calls += 1
        return (
            fixture("gelbooru_audit_direct.html"),
            "https://gelbooru.com/index.php?page=wiki&s=view&id=5479",
        )

    def page_fetcher(_query, _page, _limit, _user_id, _api_key):
        return [{"id": 42, "tags": "1girl"}], 1

    cache = WikiPageCache(tmp_path / "cache.db")
    service = GelbooruWikiAuditService(
        tags,
        None,
        cache,
        dapi_client=GelbooruDapiClient(page_fetcher=page_fetcher),
        fetcher=fetcher,
        workers=2,
    )
    progress: list[tuple[int, int, str]] = []
    first = service.audit_post(42, progress=lambda *values: progress.append(values))
    second = service.audit_post(42, progress=lambda *values: progress.append(values))

    assert first.tags[0].wiki.word_count > 0
    assert second.tags[0].wiki.word_count == first.tags[0].wiki.word_count
    assert fetch_calls == 1
    assert cache.get("gelbooru", "1girl") is not None
    assert progress[-1][:2] == (1, 1)


@pytest.mark.parametrize(
    ("source", "expected_text", "expected_words"),
    [
        ("", "", 0),
        ("<br><br><div></div>", "", 0),
        ("<p>A character.</p>", "A character.", 2),
        ("<p>Hello <b>beautiful</b> world.</p>", "Hello beautiful world.", 3),
        ("<p>Red hair</p><p>Blue eyes</p>", "Red hair Blue eyes", 4),
        ("<div>  Hello\n\n world  </div>", "Hello world", 2),
        ("<p>Tom &amp; Jerry</p>", "Tom & Jerry", 3),
    ],
)
def test_visible_editorial_text_and_word_count(
    source: str, expected_text: str, expected_words: int
) -> None:
    text, words, characters = wiki_text_metrics(source)
    assert text == expected_text
    assert words == expected_words
    assert characters == len(expected_text)
    assert normalize_wiki_text(source) == expected_text


def test_empty_direct_page_excludes_all_gelbooru_chrome(tmp_path: Path) -> None:
    source = fixture("gelbooru_audit_empty.html")

    def fetcher(_url: str) -> tuple[str, str]:
        return source, "https://gelbooru.com/index.php?page=wiki&s=view&id=99"

    cache = WikiPageCache(tmp_path / "cache.sqlite")
    service = GelbooruWikiAuditService(Path("tags.db"), None, cache, fetcher=fetcher)
    status = service.wiki_status("zessica_wong", force=True)
    assert status.exists is True
    assert status.content == ""
    assert status.word_count == 0


def test_invalid_direct_title_is_not_accepted() -> None:
    status = parse_direct_wiki(fixture("gelbooru_audit_direct.html"), "1girls")
    assert status.exists is False


def test_random_and_load_post_share_authenticated_dapi_client(tmp_path: Path) -> None:
    calls = []

    def page_fetcher(query, page, limit, user_id, api_key):
        calls.append((query, page, limit, user_id, api_key))
        post_id = 73 if query == "sort:random" else int(query.removeprefix("id:"))
        return [{"id": post_id, "tags": ""}], 1

    client = GelbooruDapiClient("configured-user", "configured-secret", page_fetcher=page_fetcher)
    assert random_post_id(client) == 73
    assert client.fetch_post(42)["id"] == 42
    url_id = parse_post_reference("https://gelbooru.com/index.php?page=post&s=view&id=51")
    assert client.fetch_post(url_id)["id"] == 51
    assert [call[0] for call in calls] == ["sort:random", "id:42", "id:51"]
    assert all(call[3:] == ("configured-user", "configured-secret") for call in calls)


def test_dapi_401_becomes_explicit_authentication_error(monkeypatch) -> None:
    def denied(_request, timeout):
        raise urllib.error.HTTPError("https://gelbooru.com", 401, "Unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", denied)
    with pytest.raises(GelbooruAuthenticationError) as caught:
        fetch_page("id:42", 0, 1, "configured-user", "configured-secret")
    assert "authentication" in str(caught.value).casefold()
    assert "configured-secret" not in str(caught.value)
