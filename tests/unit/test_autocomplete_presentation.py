from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from booruflow.application.tag_autocomplete import TagSuggestion
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.tag_browser import TagRow
from booruflow.presentation.pyside6.tag_browser_page import RAW_NAME_ROLE, TagBrowserPage
from booruflow.presentation.pyside6.wiki_audit_page import WikiAuditPage

APP = QApplication.instance() or QApplication([])
LANGUAGES = Path(__file__).resolve().parents[2] / "resources" / "i18n"


def palette(dark: bool) -> QPalette:
    value = QPalette()
    value.setColor(QPalette.ColorRole.Window, QColor("#202124" if dark else "#f4f5f7"))
    value.setColor(QPalette.ColorRole.Base, QColor("#303134" if dark else "#ffffff"))
    value.setColor(QPalette.ColorRole.Text, QColor("#f1f3f4" if dark else "#202124"))
    value.setColor(QPalette.ColorRole.Highlight, QColor("#8ab4f8" if dark else "#1a73e8"))
    value.setColor(QPalette.ColorRole.HighlightedText, QColor("#202124" if dark else "#ffffff"))
    return value


def test_tag_browser_colors_survive_hot_theme_change_and_keep_selection() -> None:
    page = TagBrowserPage(LanguageCatalog(LANGUAGES, "en"))
    page.setPalette(palette(True))
    page._show_rows(
        [
            TagRow(1, "solo", 100, 0, 0),
            TagRow(2, "old_name", 100, 3, 0, "old_name", "pokemon"),
            TagRow(3, "deprecated_name", 100, 6, 0),
        ]
    )
    by_name = {
        page.table.item(row, 1).data(RAW_NAME_ROLE): row
        for row in range(page.table.rowCount())
    }
    general = page.table.item(by_name["solo"], 1)
    alias = page.table.item(by_name["old_name"], 1)
    deprecated = page.table.item(by_name["deprecated_name"], 1)
    assert general.foreground().color() == palette(True).color(QPalette.ColorRole.Text)
    assert alias.foreground().color().name() == "#dd00dd"
    assert deprecated.foreground().color().name() == "#bfa7c5"

    page.table.selectRow(by_name["solo"])
    selected = general
    page.setPalette(palette(False))
    page.refresh_theme()
    assert selected.isSelected()
    assert selected.foreground().color() == palette(False).color(QPalette.ColorRole.Text)
    assert alias.foreground().color().name() == "#dd00dd"
    page.close()


def test_all_single_value_completers_commit_canonical_role_not_popup_text(tmp_path: Path) -> None:
    suggestion = TagSuggestion(
        "gelbooru", "pokemon", "pokemon", "copyright", 533_357, "pocket_monsters"
    )
    catalog = LanguageCatalog(LANGUAGES, "en")
    tag_browser = TagBrowserPage(catalog)
    wiki_audit = WikiAuditPage(catalog, None, None, tmp_path / "cache.db")
    try:
        for page, field in (
            (tag_browser, tag_browser.query),
            (wiki_audit, wiki_audit.search),
        ):
            page.suggestion_model.set_suggestions([suggestion], page.palette())
            assert page.completer.completionRole() == Qt.ItemDataRole.DisplayRole
            index = page.suggestion_model.index(0, 0)
            assert page.suggestion_model.stringList()[0] == "pocket_monsters → pokemon"
            page._autocomplete_activated(index)
            assert field.text() == "pokemon"
            color = page.suggestion_model.index(0, 0).data(
                Qt.ItemDataRole.ForegroundRole
            ).color()
            assert color.name() == "#dd00dd"
    finally:
        tag_browser.close()
        wiki_audit.close()
