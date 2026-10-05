"""Menus, stable geometry, native selection paint and existing thumbnail signals."""
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMenu, QTableWidgetItem

from booruflow.application.tag_autocomplete import TagSuggestion
from booruflow.domain.auto_organize import PostMetadata
from booruflow.domain.image_finder import RemoteArtist, RemoteArtwork, RemoteImage
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.tag_browser import TagRow
from booruflow.infrastructure.wiki_audit import AuditResult, AuditTag, WikiStatus
from booruflow.presentation.pyside6.context_actions import (
    bind_post_menu,
    link_actions,
    tag_actions,
)
from booruflow.presentation.pyside6.entity_profiles import EntityProfileDialog
from booruflow.presentation.pyside6.image_finder_page import ImageFinderPage
from booruflow.presentation.pyside6.status_bar import PageStatus
from booruflow.presentation.pyside6.tag_browser_page import TagBrowserPage
from booruflow.presentation.pyside6.tagging_legacy_page import ResultCard
from booruflow.presentation.pyside6.tagging_page import TaggingPage
from booruflow.presentation.pyside6.thumbnail_feedback import ThumbnailFeedback
from booruflow.presentation.pyside6.ui_components import DataTable
from booruflow.presentation.pyside6.wiki_audit_page import WikiAuditPage
from tests.unit import test_pyside6_theme as live_theme


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    existing = set(application.topLevelWidgets())
    yield application
    for widget in set(application.topLevelWidgets()) - existing:
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()


@pytest.fixture(params=["en", "fr"])
def catalog(request):
    return LanguageCatalog(Path(__file__).resolve().parents[2] / "resources/i18n", request.param)


def choosing_menu(label, captured):
    class ChoosingMenu(QMenu):
        def exec(self, _position):
            captured.extend(action.text() for action in self.actions() if not action.isSeparator())
            return next((action for action in self.actions() if action.text() == label), None)
    return ChoosingMenu


@pytest.mark.parametrize("canonical", [None, "raw_tag", "resolved_tag"])
def test_common_tag_actions_only_offer_distinct_canonical(app, catalog, canonical):
    menu = QMenu()
    launcher, entity = Mock(), Mock()
    handlers = tag_actions(menu, catalog, "raw_tag", canonical=canonical, category=4,
                           launcher=launcher, entity=entity)
    label = catalog.text("tag_browser.open_canonical_site", site="Gelbooru")
    assert (label in [a.text() for a in menu.actions()]) == (canonical == "resolved_tag")
    for action, callback in handlers.items():
        if action.text() == catalog.text("tag_browser.copy_tag"):
            callback()
    assert QApplication.clipboard().text() == "raw_tag"
    handlers[next(a for a in menu.actions() if a.text() == catalog.text("entity.analyze_menu"))]()
    entity.assert_called_once_with(canonical or "raw_tag", "character")


@pytest.mark.parametrize("url,gelbooru", [
    ("https://gelbooru.com/index.php?page=post&s=view&id=42", True),
    ("https://www.gelbooru.com/", True),
    ("https://gelbooru.com.example.invalid/", False),
    ("https://e621.net/posts/42", False),
    ("file:///C:/temporary/image.png", False),
])
def test_link_routes_and_copy_do_not_redirect_other_destinations(app, catalog, url, gelbooru):
    launcher = Mock()
    menu = QMenu()
    handlers = link_actions(menu, catalog, url, launcher=launcher)
    with patch("booruflow.presentation.pyside6.context_actions.QDesktopServices.openUrl") as desktop:
        handlers[menu.actions()[0]]()
        assert launcher.open.call_count == int(gelbooru)
        assert desktop.call_count == int(not gelbooru)
    handlers[menu.actions()[1]]()
    assert QApplication.clipboard().text() == url
    internal = Mock()
    handlers = link_actions(QMenu(), catalog, "booruflow-tag:hairclip", opener=internal)
    next(iter(handlers.values()))()
    internal.assert_called_once_with("booruflow-tag:hairclip")


def test_tag_browser_real_menu_and_double_click_use_clicked_tag(app, catalog):
    launcher = Mock()
    page = TagBrowserPage(catalog, browser_launcher=launcher)
    page._show_rows([TagRow(5, "raw_tag", 100, 4, 0, canonical_name="resolved_tag")])
    page.resize(1000, 650); page.show(); QTest.qWait(30)
    captured, errors = [], []
    def choose():
        menu = QApplication.activePopupWidget()
        if not isinstance(menu, QMenu):
            errors.append("menu did not open")
            return
        captured.extend(a.text() for a in menu.actions())
        action = next(a for a in menu.actions() if a.text() == catalog.text("tag_browser.copy_tag"))
        QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
    timeout = QTimer(page); timeout.setSingleShot(True)
    timeout.timeout.connect(lambda: [menu.close() for menu in page.findChildren(QMenu)])
    timeout.start(2000)
    QTimer.singleShot(30, choose)
    page._show_context_menu(QPoint(5, page.table.rowViewportPosition(0) + 5))
    timeout.stop()
    assert not errors and captured
    assert QApplication.clipboard().text() == "raw_tag"
    assert catalog.text("entity.analyze_menu") in captured
    position = page.table.visualItemRect(page.table.item(0, 1)).center()
    QTest.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseDClick(page.table.viewport(), Qt.MouseButton.LeftButton, pos=position)
    launcher.open.assert_called_once_with("https://gelbooru.com/index.php?page=post&s=list&tags=raw_tag")
    page.close()


def test_autocomplete_menu_is_lazy_and_copies_raw_alias(app, catalog):
    page = TagBrowserPage(catalog, browser_launcher=Mock())
    receiver = page.completer._context_menu_receiver
    assert receiver.popup_ref() is None
    page.resize(1000, 650); page.show(); page.query.setFocus(); QTest.qWait(30)
    page._autocomplete_ready("tag_browser.query", None, [TagSuggestion(
        "gelbooru", "raw_alias", "canonical_tag", "character", 12, "raw_alias")])
    app.processEvents()
    popup = page.completer.popup()
    assert receiver.popup_ref() is popup
    labels = []
    with patch("booruflow.presentation.pyside6.context_actions.QMenu",
               choosing_menu(catalog.text("tag_browser.copy_tag"), labels)):
        popup.customContextMenuRequested.emit(popup.visualRect(popup.model().index(0, 0)).center())
    assert QApplication.clipboard().text() == "raw_alias"
    assert catalog.text("tag_browser.open_canonical_site", site="Gelbooru") in labels
    assert catalog.text("entity.analyze_menu") in labels
    popup.hide(); page.close(); page.deleteLater(); app.processEvents()


def test_post_menu_uses_current_card_after_reuse_and_keeps_double_click(app, catalog):
    launcher = Mock()
    card = ResultCard({"id": 1})
    bind_post_menu(card, catalog, launcher=launcher)
    card.post = {"id": 42, "source": "https://example.invalid/source"}
    card.show()
    captured = []
    for key, expected in (("context.copy_post_id", "42"),
                          ("context.copy_post_url", "https://gelbooru.com/index.php?page=post&s=view&id=42"),
                          ("context.copy_source_url", card.post["source"])):
        with patch("booruflow.presentation.pyside6.context_actions.QMenu",
                   choosing_menu(catalog.text(key), captured)):
            card.customContextMenuRequested.emit(QPoint(5, 5))
        assert QApplication.clipboard().text() == expected
    with patch("booruflow.presentation.pyside6.context_actions.QMenu",
               choosing_menu(catalog.text("context.open_post"), [])):
        card.customContextMenuRequested.emit(QPoint(5, 5))
    launcher.open.assert_called_once_with("https://gelbooru.com/index.php?page=post&s=view&id=42")
    spy = QSignalSpy(card.open_requested)
    QTest.mouseDClick(card, Qt.MouseButton.LeftButton)
    assert spy.at(0)[0]["id"] == 42
    card.close()


def test_active_filters_keep_one_line_and_page_position_even_after_clear(app, catalog):
    page = TaggingPage(catalog, {}, Mock()); page.resize(1200, 850); page.show()
    QTest.qWait(30)
    def geometry():
        return page.active_filters.height(), page.bulk_splitter.mapTo(page, QPoint()).y()
    before = geometry()
    for i in range(35):
        page.analysis_palette.add_tag(f"long_filter_{i:03}", emit=False)
    QTest.qWait(30)
    assert geometry() == before
    editor = page.analysis_palette
    assert len({chip.y() for chip in editor._chips.values()}) == 1
    bar = editor.chips_scroll.horizontalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum()); app.processEvents()
    assert bar.value() > 0
    assert editor.chips_scroll.verticalScrollBar().maximum() == 0
    page.clear_targeted_wd14(); QTest.qWait(30)
    assert geometry() == before
    assert editor.isVisible()
    assert bar.maximum() == 0
    page.close()


@pytest.mark.parametrize("dark", [False, True])
@pytest.mark.parametrize("focus", [False, True])
def test_entity_selected_text_is_painted_with_highlighted_text(app, catalog, tmp_path, dark, focus):
    original = QPalette(app.palette())
    dialog = None
    try:
        app.setPalette(live_theme.LiveThemeTests.palette(dark))
        dialog = EntityProfileDialog(tmp_path, catalog, "subject", "general", dict,
                                     None, Mock(), PageStatus("tagging"))
        dialog.profile_id = dialog.repository.create("subject", "subject")
        dialog.repository.add_posts(dialog.profile_id, [PostMetadata(
            "gelbooru", "1", ("subject", "hairclip"))])
        dialog.render(); dialog.show()
        table = dialog.tables["attribute"]; table.selectRow(0)
        # Deliberately conflicting model brush reproduces native Windows 11's
        # historical black text on dark selected background.
        table.item(0, 0).setForeground(QColor("black"))
        (table if focus else dialog.filter).setFocus(); QTest.qWait(30)
        rect = table.visualItemRect(table.item(0, 0)).adjusted(2, 2, -2, -2)
        image = table.viewport().grab(rect).toImage()
        expected = table.palette().color(QPalette.ColorRole.HighlightedText)
        assert expected != table.palette().color(QPalette.ColorRole.Highlight)
        # Fusion/offscreen overlays a subtle focus gradient on painted glyphs;
        # retain a small tolerance while rejecting the conflicting black brush.
        def matches(pixel):
            return max(abs(left - right) for left, right in zip(
                pixel.getRgb()[:3], expected.getRgb()[:3], strict=True)) <= 25
        assert sum(matches(image.pixelColor(x, y)) for x in range(image.width())
                   for y in range(image.height())) > 5
    finally:
        if dialog is not None:
            dialog.close(); app.processEvents()
        app.setPalette(original); app.processEvents()


def test_entity_hot_theme_refresh_keeps_selection_sort_filter_and_rows(app, catalog, tmp_path):
    dialog = EntityProfileDialog(tmp_path, catalog, "subject", "artist", dict,
                                 None, Mock(), PageStatus("tagging"))
    dialog.profile_id = dialog.repository.create("subject", "subject")
    dialog.repository.add_posts(dialog.profile_id, [PostMetadata(
        "gelbooru", "1", ("subject", "hairclip", "jacket", "scythe"))])
    dialog.render(); dialog.show()
    table = dialog.tables["attribute"]
    table.sortItems(0, Qt.SortOrder.DescendingOrder); table.selectRow(1)
    before = [table.item(row, 0) for row in range(table.rowCount())]
    selected = table.item(table.currentRow(), 0)
    original = QPalette(app.palette())
    try:
        with patch.object(dialog, "render", wraps=dialog.render) as render:
            for dark in (True, False, True, False):
                app.setPalette(live_theme.LiveThemeTests.palette(dark)); QTest.qWait(30)
                assert table.item(table.currentRow(), 0) is selected
                assert [table.item(row, 0) for row in range(table.rowCount())] == before
                assert table.horizontalHeader().sortIndicatorSection() == 0
                assert table.horizontalHeader().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
            render.assert_not_called()
    finally:
        dialog.close(); app.processEvents(); app.setPalette(original)


def test_main_window_refreshes_entity_brushes_after_child_palettes(app, tmp_path):
    original = QPalette(app.palette())
    window = live_theme.LiveThemeTests().make_window(tmp_path)
    dialog = EntityProfileDialog(tmp_path, window.catalog, "subject", "general", dict,
                                 None, Mock(), PageStatus("tagging"), window)
    dialog.profile_id = dialog.repository.create("subject", "subject")
    dialog.repository.add_posts(dialog.profile_id, [PostMetadata(
        "gelbooru", "1", ("subject", "hairclip", "jacket"))])
    dialog.render(); window.show(); dialog.show()
    table = dialog.tables["attribute"]; table.selectRow(0)
    identities = [table.item(row, 0) for row in range(2)]
    try:
        for dark in (True, False, True, False):
            app.setPalette(live_theme.LiveThemeTests.palette(dark)); QTest.qWait(40)
            for _ in range(4):
                app.processEvents()
            assert [table.item(row, 0) for row in range(2)] == identities
            assert table.selectionModel().isRowSelected(0)
            assert table.item(1, 0).foreground().color() == table.palette().color(QPalette.ColorRole.Text)
            image = table.viewport().grab().toImage()
            rect = table.visualItemRect(table.item(1, 0))
            # Native styles can composite AlternateBase over their own backing
            # colour. Compare hot paint to a fresh view under the same palette.
            cold = DataTable(2, 1, dialog)
            cold.resize(300, 180)
            cold.setItem(1, 0, QTableWidgetItem("jacket")); cold.show(); app.processEvents()
            cold_rect = cold.visualItemRect(cold.item(1, 0))
            expected = cold.viewport().grab().toImage().pixelColor(
                cold_rect.right() - 4, cold_rect.bottom() - 4)
            cold.hide(); cold.deleteLater()
            assert image.pixelColor(rect.right() - 4, rect.bottom() - 4) == expected
    finally:
        dialog.close(); window.close(); app.processEvents(); app.setPalette(original)


def test_wiki_audit_menu_keeps_read_wiki_and_uses_canonical(app, catalog, tmp_path):
    launcher = Mock()
    page = WikiAuditPage(catalog, None, None, tmp_path / "wiki.sqlite", launcher)
    page.result = AuditResult(42, "", (AuditTag(
        "raw_tag", 4, 10, None, "resolved_tag", WikiStatus("raw_tag", True, content="wiki text")),))
    page.ignore_deprecated.setChecked(False); page.apply_filters()
    page.resize(1000, 700); page.show(); app.processEvents()
    labels = []
    with patch("booruflow.presentation.pyside6.wiki_audit_page.QMenu",
               choosing_menu(catalog.text("tag_browser.open_canonical_site", site="Gelbooru"), labels)):
        page._tag_menu(QPoint(5, page.table.rowViewportPosition(0) + 5))
    launcher.open.assert_called_once_with("https://gelbooru.com/index.php?page=post&s=list&tags=resolved_tag")
    assert catalog.text("wiki_audit.read") in labels
    page._set_busy(True)
    assert page.progress_bar.maximum() == 0
    page._progress(1, 4, "raw_tag")
    assert page.progress_bar.value() == 1 and page.progress_bar.maximum() == 4
    page._set_busy(False); page.close()


def test_name_sort_is_distinct_from_id_sort_without_changing_mapping(app, catalog):
    page = TagBrowserPage(catalog)
    page._show_rows([TagRow(2, "alpha", 10, 0, 0), TagRow(1, "zeta", 10, 0, 0)])
    page.table.sortItems(1, Qt.SortOrder.AscendingOrder)
    assert [page.table.item(row, 1).text() for row in range(2)] == ["alpha", "zeta"]
    page.table.sortItems(0, Qt.SortOrder.AscendingOrder)
    assert [page.table.item(row, 1).text() for row in range(2)] == ["zeta", "alpha"]
    page.close()


def test_finder_context_selects_clicked_artwork_and_reports_thumbnail_state(app, catalog):
    launcher = Mock()
    page = ImageFinderPage(catalog, browser_launcher=launcher)
    artworks = tuple(RemoteArtwork(
        "pixiv", str(i), f"https://www.pixiv.net/artworks/{i}", RemoteArtist("pixiv", "1", "artist"),
        str(i), "", (), "general", None,
        (RemoteImage("pixiv", str(i), 0, "", None, None, None),)) for i in range(1, 3))
    page.set_results(artworks, False, False); page.resize(1000, 700); page.show(); app.processEvents()
    with patch("booruflow.presentation.pyside6.image_finder_page.QMenu",
               choosing_menu(catalog.text("context.copy_url"), [])):
        page._results_menu(page.results.visualItemRect(page.results.item(1)).center())
    assert page.selected().source_post_id == "2"
    assert QApplication.clipboard().text() == artworks[1].source_url
    state = QSignalSpy(page.page_status.thumbnail_state_changed)
    page.thumbnail_loader.progress.emit({"wave": 7, "done": 0, "total": 10, "failed": 0})
    page.thumbnail_loader.progress.emit({"wave": 7, "done": 10, "total": 10, "failed": 1})
    assert state.at(0)[1:4] == [0, 10, 0]
    assert state.at(state.count() - 1)[1:4] == [9, 10, 1]
    page.thumbnail_loader.shutdown(); page.close()


def test_entity_context_copies_clicked_tag_and_exposes_shortcuts(app, catalog, tmp_path):
    dialog = EntityProfileDialog(tmp_path, catalog, "subject", "general", dict,
                                 None, Mock(), PageStatus("tagging"))
    dialog.profile_id = dialog.repository.create("subject", "subject")
    dialog.repository.add_posts(dialog.profile_id, [PostMetadata(
        "gelbooru", "1", ("subject", "hairclip", "jacket"))])
    dialog.render(); dialog.show(); app.processEvents()
    table = dialog.tables["attribute"]; labels = []
    with patch("booruflow.presentation.pyside6.entity_profiles.QMenu",
               choosing_menu(catalog.text("tag_browser.copy_tag"), labels)):
        dialog.menu(table, table.visualItemRect(table.item(1, 0)).center())
    assert QApplication.clipboard().text() == table.item(1, 0).text()
    assert catalog.text("entity.keep") + " (A)" in labels
    assert catalog.text("entity.ignore") + " (R)" in labels
    assert catalog.text("entity.analyze_menu") in labels
    dialog.close(); app.processEvents()


def test_thumbnail_feedback_starts_throttles_extends_and_completes(app):
    class Loader(QObject):
        progress = Signal(object)
    loader = Loader(); values = []
    feedback = ThumbnailFeedback(loader, lambda *value: values.append(value))
    def emit(done, total=100, failed=0, wave=1):
        loader.progress.emit({"wave": wave, "done": done, "total": total, "failed": failed})
    emit(0)
    assert values == [(0, 100, 0)]
    for done in range(1, 38):
        emit(done)
    assert len(values) == 1
    QTest.qWait(120)
    assert values[-1] == (37, 100, 0)
    emit(100, failed=2)
    assert values[-1] == (98, 100, 2)
    emit(100, total=110, failed=2); QTest.qWait(120)
    assert values[-1] == (98, 110, 2)
    emit(110, total=110, failed=2)
    assert values[-1] == (108, 110, 2)
    emit(0, total=10, wave=2)
    assert values[-1] == (0, 10, 0)
    feedback.stop()
