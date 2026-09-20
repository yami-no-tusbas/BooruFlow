from pathlib import Path
from unittest.mock import patch

import pytest

from booruflow.infrastructure.wiki_tag_importer import (
    WikiFetchResponse,
    WikiImportCancelled,
    WikiPageNotFoundError,
    WikiPageParseError,
    fetch_wiki_page_details,
    import_catalogues,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_gelbooru_direct_page_extracts_content_metadata_and_type() -> None:
    direct_url = "https://gelbooru.com/index.php?page=wiki&s=view&id=5479"
    with patch(
        "booruflow.infrastructure.wiki_tag_importer._get_with_url",
        return_value=(fixture("gelbooru_audit_direct.html"), direct_url),
    ):
        page = fetch_wiki_page_details("gelbooru", "1girl")
    assert page["wiki_id"] == 5479
    assert page["tag_type"] == "General"
    assert page["author"] == "Absolutixn"
    assert page["remote_updated_at"] == "2016-01-31T23:37:00+00:00"
    assert page["content_format"] == "html"
    assert "one female character" in page["content"]
    assert "<a" not in page["content"]
    assert page["referenced_tags"] == ["solo"]


def test_gelbooru_list_result_fetches_the_exact_direct_page() -> None:
    direct_url = "https://gelbooru.com/index.php?page=wiki&s=view&id=13"
    direct = fixture("gelbooru_audit_direct.html").replace("1girl", "model_sheet")
    with patch(
        "booruflow.infrastructure.wiki_tag_importer._get_with_url",
        side_effect=[
            (fixture("gelbooru_audit_list.html"), "https://gelbooru.com/?page=wiki&s=list"),
            (direct, direct_url),
        ],
    ) as fetched:
        page = fetch_wiki_page_details("gelbooru", "model_sheet")
    assert fetched.call_count == 2
    assert page["wiki_id"] == 13
    assert page["version"] == 2
    assert page["source_url"] == direct_url


def test_missing_and_unrecognized_responses_are_distinct() -> None:
    with patch(
        "booruflow.infrastructure.wiki_tag_importer._get_with_url",
        return_value=(fixture("gelbooru_audit_list.html"), "https://gelbooru.com/wiki"),
    ), pytest.raises(WikiPageNotFoundError):
        fetch_wiki_page_details("gelbooru", "definitely_missing")

    with patch(
        "booruflow.infrastructure.wiki_tag_importer._get_with_url",
        return_value=("<html><h1>Maintenance</h1></html>", "https://gelbooru.com/wiki"),
    ), pytest.raises(WikiPageParseError):
        fetch_wiki_page_details("gelbooru", "1girl")


def test_create_redirect_is_a_missing_wiki_not_a_parse_or_network_error() -> None:
    with patch(
        "booruflow.infrastructure.wiki_tag_importer._get_with_url",
        return_value=(fixture("gelbooru_audit_create.html"),
                      "https://gelbooru.com/index.php?page=wiki&s=create&title=tesdf"),
    ), pytest.raises(WikiPageNotFoundError):
        fetch_wiki_page_details("gelbooru", "tesdf")


def test_create_meta_refresh_is_missing_even_when_final_url_stays_on_listing() -> None:
    with patch(
        "booruflow.infrastructure.wiki_tag_importer._get_with_url",
        return_value=(fixture("gelbooru_audit_create_meta.html"),
                      "https://gelbooru.com/index.php?page=wiki&s=list&search=white_outline"),
    ), pytest.raises(WikiPageNotFoundError):
        fetch_wiki_page_details("gelbooru", "white_outline")


def test_create_redirect_history_survives_account_login_destination() -> None:
    requested = "https://gelbooru.com/index.php?page=wiki&s=list&search=white_outline"
    response = WikiFetchResponse(
        requested_url=requested,
        final_url="https://gelbooru.com/index.php?page=account&s=home",
        status=200,
        body="<html><h1>Account</h1></html>",
        redirect_urls=(
            "https://gelbooru.com/index.php?page=wiki&s=create&title=white_outline",
            "https://gelbooru.com/index.php?page=account&s=home",
        ),
    )
    with pytest.raises(WikiPageNotFoundError):
        fetch_wiki_page_details("gelbooru", "white_outline", fetcher=lambda _url: response)


def test_listing_without_exact_match_is_missing_wiki() -> None:
    with patch(
        "booruflow.infrastructure.wiki_tag_importer._get_with_url",
        return_value=(fixture("gelbooru_audit_list.html"), "https://gelbooru.com/index.php?page=wiki&s=list"),
    ), pytest.raises(WikiPageNotFoundError):
        fetch_wiki_page_details("gelbooru", "Love_Live!")


def test_existing_empty_wiki_is_valid() -> None:
    source = (
        "<html><body><table><tr><td><h2>Now Viewing: empty_tag</h2>"
        "<div>Tag type: General</div><div>Other Wiki Information</div>"
        "</td></tr></table></body></html>"
    )
    with patch(
        "booruflow.infrastructure.wiki_tag_importer._get_with_url",
        return_value=(source, "https://gelbooru.com/?page=wiki&s=view&id=99"),
    ):
        page = fetch_wiki_page_details("gelbooru", "empty_tag")
    assert page["wiki_id"] == 99
    assert page["content"] == ""


def test_global_import_honors_cancellation_before_first_request() -> None:
    with patch("booruflow.infrastructure.wiki_tag_importer._get") as fetched, pytest.raises(
        WikiImportCancelled
    ):
        import_catalogues(cancelled=lambda: True)
    fetched.assert_not_called()
