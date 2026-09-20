from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from booruflow.infrastructure.gelbooru_client import GelbooruAuthenticationError
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.wiki_audit import AuditResult, AuditTag, WikiStatus
from booruflow.presentation.pyside6.wiki_audit_page import ROW_ROLE, WikiAuditPage

APP = QApplication.instance() or QApplication([])


def _catalog() -> LanguageCatalog:
    root = Path(__file__).parents[2] / "resources" / "i18n"
    return LanguageCatalog(root, "en")


def test_table_keeps_numeric_post_count_and_filters(tmp_path: Path) -> None:
    page = WikiAuditPage(_catalog(), None, None, tmp_path / "cache.db")
    page.result = AuditResult(
        42,
        "https://gelbooru.com/index.php?page=post&s=view&id=42",
        (
            AuditTag("large", 0, 100_000, None, None, WikiStatus("large", False)),
            AuditTag(
                "small", 5, 3, None, None,
                WikiStatus("small", True, updated_at=datetime(2025, 1, 1, tzinfo=UTC)),
            ),
        ),
    )
    page.apply_filters()
    assert page.table.rowCount() == 2
    numeric_values = {
        page.table.item(row, 2).data(Qt.ItemDataRole.DisplayRole)
        for row in range(page.table.rowCount())
    }
    assert numeric_values == {3, 100_000}
    assert all(
        isinstance(page.table.item(row, 0).data(ROW_ROLE), AuditTag)
        for row in range(page.table.rowCount())
    )
    page.ignore_metadata.setChecked(True)
    assert page.table.rowCount() == 1
    page.state_filter.setCurrentIndex(page.state_filter.findData("missing"))
    assert page.table.rowCount() == 1
    page.close()


def test_wiki_state_distinguishes_missing_from_network_error(tmp_path: Path) -> None:
    page = WikiAuditPage(_catalog(), None, None, tmp_path / "cache.db")
    page.result = AuditResult(
        42,
        "",
        (
            AuditTag("missing", 0, 10, None, None, WikiStatus("missing", False)),
            AuditTag("offline", 0, 9, None, None, WikiStatus("offline", False, error="offline")),
            AuditTag(
                "malformed", 0, 8, None, None,
                WikiStatus("malformed", False, error="invalid", error_kind="parse"),
            ),
        ),
    )
    page.apply_filters()
    values = {
        page.table.item(row, 0).text(): page.table.item(row, 4).text()
        for row in range(page.table.rowCount())
    }
    assert values == {
        "missing": "No wiki", "offline": "Network error", "malformed": "Parse error"
    }
    assert page.table.item(0, 5).text() == "—"
    assert "1 missing" in page.status.text()
    assert "2 errors" in page.status.text()
    page.close()


def test_filter_labels_and_search_placeholder_are_visible(tmp_path: Path) -> None:
    catalog = _catalog()
    page = WikiAuditPage(catalog, None, None, tmp_path / "cache.db")
    assert page.minimum_label.text() == "Minimum posts:"
    assert page.search.placeholderText() == "Type to filter…"
    catalog.set_language("fr")
    page.retranslate()
    assert page.minimum_label.text() == "Posts minimum :"
    assert page.search.placeholderText() == "Saisir pour filtrer…"
    page.close()


def test_open_selected_uses_wiki_view_or_wiki_search_not_post_search(tmp_path: Path) -> None:
    launcher = Mock()
    page = WikiAuditPage(_catalog(), None, None, tmp_path / "cache.db", browser_launcher=launcher)
    page.result = AuditResult(
        42,
        "",
        (
            AuditTag("shirt", 0, 10, None, None, WikiStatus("shirt", True, wiki_id=28419)),
            AuditTag("rryy", 1, 9, None, None, WikiStatus("rryy", False)),
            AuditTag("Love_Live!", 0, 8, None, None, WikiStatus("Love_Live!", False)),
        ),
    )
    page.apply_filters()
    page.table.setCurrentCell(0, 0)
    page.open_selected()
    page.table.setCurrentCell(1, 0)
    page.open_selected()
    page.table.setCurrentCell(2, 0)
    page.open_selected()
    urls = [call.args[0] for call in launcher.open.call_args_list]
    assert urls[0].endswith("page=wiki&s=view&id=28419")
    assert "page=wiki" in urls[1] and "s=list" in urls[1]
    assert "page=post" not in urls[1]
    assert urllib.parse.parse_qs(urllib.parse.urlparse(urls[2]).query)["search"] == ["Love_Live!"]
    page.close()


def test_word_column_sorts_numerically_and_missing_is_last(tmp_path: Path) -> None:
    page = WikiAuditPage(_catalog(), None, None, tmp_path / "cache.db")
    page.result = AuditResult(
        42,
        "",
        tuple(
            AuditTag(name, 0, 1, None, None, status)
            for name, status in (
                ("hundred", WikiStatus("hundred", True, word_count=100)),
                ("missing", WikiStatus("missing", False)),
                ("two", WikiStatus("two", True, word_count=2)),
                ("zero", WikiStatus("zero", True, word_count=0)),
                ("ten", WikiStatus("ten", True, word_count=10)),
            )
        ),
    )
    page.apply_filters()
    page.table.sortItems(5, Qt.SortOrder.AscendingOrder)
    assert [
        page.table.item(row, 0).text() for row in range(page.table.rowCount())
    ] == ["zero", "two", "ten", "hundred", "missing"]
    assert page.table.item(0, 5).data(Qt.ItemDataRole.DisplayRole) == 0
    assert page.table.item(4, 5).text() == "—"
    page.close()


def test_length_filters_distinguish_missing_empty_short_and_non_empty(tmp_path: Path) -> None:
    page = WikiAuditPage(_catalog(), None, None, tmp_path / "cache.db")
    page.result = AuditResult(
        42,
        "",
        (
            AuditTag("missing", 0, 50, None, None, WikiStatus("missing", False)),
            AuditTag(
                "error", 0, 45, None, None,
                WikiStatus("error", False, error="offline"),
            ),
            AuditTag("empty", 0, 40, None, None, WikiStatus("empty", True, word_count=0)),
            AuditTag("short", 0, 30, None, None, WikiStatus("short", True, word_count=20)),
            AuditTag("normal", 0, 20, None, None, WikiStatus("normal", True, word_count=21)),
        ),
    )
    page.apply_filters()

    def visible() -> set[str]:
        return {
            page.table.item(row, 0).text() for row in range(page.table.rowCount())
        }

    assert visible() == {"missing", "error", "empty", "short", "normal"}
    page.length_filter.setCurrentIndex(page.length_filter.findData("empty"))
    assert visible() == {"empty"}
    page.length_filter.setCurrentIndex(page.length_filter.findData("short"))
    assert visible() == {"short"}
    page.length_filter.setCurrentIndex(page.length_filter.findData("non_empty"))
    assert visible() == {"short", "normal"}
    page.state_filter.setCurrentIndex(page.state_filter.findData("missing"))
    assert visible() == set()
    page.length_filter.setCurrentIndex(page.length_filter.findData("all"))
    assert visible() == {"missing"}
    page.close()


def test_read_uses_audited_content_without_starting_network_worker(tmp_path: Path) -> None:
    page = WikiAuditPage(_catalog(), None, None, tmp_path / "cache.db")
    row = AuditTag(
        "cached", 0, 1, None, None,
        WikiStatus(
            "cached", True, content="Cached editorial body", word_count=3,
            source_url="https://gelbooru.com/index.php?page=wiki&s=view&id=1",
        ),
    )
    page.result = AuditResult(42, "", (row,))
    page.apply_filters()
    page.table.setCurrentCell(0, 0)
    page.read_selected()
    assert page.reader.toPlainText() == "Cached editorial body"
    assert page._reader_url.endswith("id=1")
    page.close()


def test_read_renders_safe_rich_wiki_without_links_or_external_content(tmp_path: Path) -> None:
    page = WikiAuditPage(_catalog(), None, None, tmp_path / "cache.db")
    page.result = AuditResult(
        42,
        "",
        (AuditTag(
            "cached", 0, 1, None, None,
            WikiStatus(
                "cached", True,
                content=(
                    "<h2>Appearance</h2><p>A <b>character</b> description.</p>"
                    "<ul><li>Red hair</li><li>Blue eyes</li></ul>"
                    '<a href="https://example.invalid">short_hair</a>'
                    "<script>bad()</script><iframe>bad()</iframe>"
                    '<img src="https://example.invalid/image.png">'
                ),
                content_format="html",
            ),
        ),),
    )
    page.apply_filters()
    page.table.setCurrentCell(0, 0)
    page.read_selected()
    rendered = page.reader.toHtml().casefold()
    assert "appearance" in rendered and "character" in rendered
    assert "red hair" in rendered and "blue eyes" in rendered
    assert "short_hair" in rendered
    assert "<script" not in rendered
    assert "<iframe" not in rendered
    assert "<img" not in rendered
    assert "example.invalid" not in rendered
    page.close()


def test_stale_scan_result_does_not_replace_the_active_post(tmp_path: Path) -> None:
    page = WikiAuditPage(_catalog(), None, None, tmp_path / "cache.db")
    active = AuditResult(2, "", ())
    page.result = active
    page._scan_generation = 2
    page._completed(AuditResult(1, "", ()), generation=1)
    assert page.result is active
    assert page.reference.text() == ""
    page.close()


def test_missing_wiki_reader_is_explicit(tmp_path: Path) -> None:
    page = WikiAuditPage(_catalog(), None, None, tmp_path / "cache.db")
    row = AuditTag("missing", 0, 1, None, None, WikiStatus("missing", False))
    page.result = AuditResult(42, "", (row,))
    page.apply_filters(); page.table.setCurrentCell(0, 0); page.read_selected()
    assert "No wiki" in page.reader.toPlainText()
    page.close()


def test_authentication_error_is_localized_and_logs_no_secrets(tmp_path: Path) -> None:
    logs = []
    credentials = {
        "gelbooru": {"user_id": "private-user", "api_key": "private-api-key"}
    }
    page = WikiAuditPage(
        _catalog(), None, None, tmp_path / "cache.db",
        credentials_provider=lambda: credentials, log=logs.append,
    )
    page._dapi_operation = "random_post"
    page._failed(GelbooruAuthenticationError("authentication failed"))
    assert "Gelbooru API authentication is required or invalid" in page.status.text()
    assert "operation=random_post" in logs[0]
    assert "authenticated=true" in logs[0]
    assert "private-user" not in logs[0]
    assert "private-api-key" not in logs[0]
    page.close()
