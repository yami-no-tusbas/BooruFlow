import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from booruflow.application.wiki_import import TagConversion, WikiImportResult
from booruflow.infrastructure.wiki_clients import RemoteWikiPage
from booruflow.infrastructure.wiki_tag_importer import WikiPageNotFoundError

PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None
LANGUAGES = Path(__file__).resolve().parents[2] / "resources" / "i18n"


@pytest.fixture(scope="module")
def app():
    if not PYSIDE6_AVAILABLE:
        pytest.skip("PySide6 is not installed")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(app, tmp_path: Path):
    from booruflow.infrastructure.localization import LanguageCatalog
    from booruflow.presentation.pyside6.wiki_page import WikiPage

    urls: list[str] = []

    class Browser:
        def open(self, url: str) -> None:
            urls.append(url)

    widget = WikiPage(LanguageCatalog(LANGUAGES, "en"), tmp_path, browser_launcher=Browser())
    widget.opened_urls = urls
    yield widget
    widget.close()


def test_wiki_tag_field_reuses_single_value_autocomplete(page) -> None:
    from PySide6.QtCore import Qt

    from booruflow.application.tag_autocomplete import TagSuggestion

    assert page.tag.completer() is page.completer
    assert not hasattr(page, "tag_chips")
    page.tag.setText("manual_tag_not_in_catalogue")
    assert page.tag.text() == "manual_tag_not_in_catalogue"
    page.suggestion_model.set_suggestions(
        [TagSuggestion("gelbooru", "pokemon", "pokemon", "copyright", 533_357)],
        page.palette(),
    )
    assert "copyright" in page.suggestion_model.stringList()[0]
    assert "533,357 posts" in page.suggestion_model.stringList()[0]
    assert page.completer.completionRole() == Qt.ItemDataRole.DisplayRole
    page._autocomplete_activated(page.suggestion_model.index(0, 0))
    assert page.tag.text() == "pokemon"


def test_check_found_loads_source_and_tag_change_invalidates_state(page) -> None:
    from booruflow.presentation.pyside6.wiki_page import WikiTagState

    page.tag.setText("known_tag")
    page.source.setPlainText("old draft")
    generation = page._tag_generation
    page._network_completed(
        "check", generation, "known_tag",
        RemoteWikiPage("known_tag", "{{known_tag}}\n[h2]Existing[/h2]", "https://wiki/7", 7),
    )
    assert page.wiki_state is WikiTagState.EXISTING_GELBOORU_WIKI
    assert "Existing" in page.source.toPlainText()
    assert "Gelbooru wiki found" in page.operation_status.text()
    page.tag.setText("other_tag")
    assert page.wiki_state is WikiTagState.UNKNOWN


def test_check_absent_and_network_error_keep_existing_draft(page) -> None:
    from booruflow.presentation.pyside6.wiki_page import WikiTagState

    page.tag.setText("missing_tag")
    page.source.setPlainText("keep me")
    generation = page._tag_generation
    page._network_failed(
        "check", generation, "missing_tag", WikiPageNotFoundError("missing")
    )
    assert page.wiki_state is WikiTagState.NEW_GELBOORU_WIKI
    assert page.source.toPlainText() == "keep me"
    page._network_failed("check", generation, "missing_tag", OSError("offline"))
    assert page.source.toPlainText() == "keep me"
    assert "kept" in page.operation_status.text()


def test_import_requires_confirmation_and_displays_attention_report(page) -> None:
    page.tag.setText("imported_tag")
    page.source.setPlainText("edited locally")
    result = WikiImportResult(
        "{{imported_tag}}\n[h2]Imported[/h2]\n* [[canonical]]\n* [[unknown]]\n",
        "https://danbooru/wiki/2",
        ("unknown — original preserved",),
        (
            TagConversion("old", "canonical", "gelbooru_alias"),
            TagConversion("same", "same", "same_name"),
            TagConversion("unknown", "unknown", "unresolved"),
        ),
    )
    generation = page._tag_generation
    from PySide6.QtWidgets import QMessageBox

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
        page._network_completed("import", generation, "imported_tag", result)
    assert page.source.toPlainText() == "edited locally"

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        page._network_completed("import", generation, "imported_tag", result)
    assert "Imported" in page.source.toPlainText()
    report = page.import_report.toPlainText()
    assert "old → canonical" in report and "unknown" in report
    assert "same → same" not in report


def test_import_missing_keeps_draft(page) -> None:
    page.tag.setText("missing")
    page.source.setPlainText("keep draft")
    page._network_failed(
        "import", page._tag_generation, "missing", WikiPageNotFoundError("missing")
    )
    assert page.source.toPlainText() == "keep draft"
    assert "No Danbooru wiki found" in page.operation_status.text()


def test_publish_routes_to_existing_or_new_after_recheck(page) -> None:
    page.tag.setText("tag")
    page.source.setPlainText("draft")
    generation = page._tag_generation
    page._network_completed(
        "publish", generation, "tag",
        RemoteWikiPage("tag", "remote", "https://gelbooru.test/wiki/9", 9),
    )
    assert page.opened_urls[-1] == "https://gelbooru.test/wiki/9"

    page._network_failed("publish", generation, "tag", WikiPageNotFoundError("missing"))
    assert "s=create" in page.opened_urls[-1] and "title=tag" in page.opened_urls[-1]

    calls = []
    page._start_network_operation = lambda operation, callback: calls.append((operation, callback))
    page._publish()
    page._publish()
    assert [operation for operation, _callback in calls] == ["publish", "publish"]
