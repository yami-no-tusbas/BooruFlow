from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QPushButton

from booruflow.domain.auto_organize import PostMetadata
from booruflow.infrastructure.browser_launcher import BrowserDetector, BrowserLauncher
from booruflow.infrastructure.entity_profile_repository import EntityProfileRepository
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.tag_browser import TagRow
from booruflow.presentation.pyside6.entity_profiles import EntityProfileDialog
from booruflow.presentation.pyside6.status_bar import PageStatus
from booruflow.presentation.pyside6.tag_browser_page import TagBrowserPage
from booruflow.presentation.pyside6.tagging_legacy_page import CollapsibleResultGroup, ResultCard
from tests.unit.test_tagging_navigation_prefetch import navigation, wait_for  # noqa: F401


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def catalog(language="en"):
    return LanguageCatalog(Path(__file__).resolve().parents[2] / "resources/i18n", language)


def selected_tag(table):
    item = table.item(table.currentRow(), 0)
    return item.text() if item else None


@pytest.mark.parametrize("source_category,expected", [
    ("character", {"hero", "franchise"}), ("copyright", {"hero", "franchise"}),
    ("artist", {"hero", "franchise", "illustrator"}),
])
def test_dialog_context_policy_keeps_statistics(app, tmp_path, source_category, expected):
    dialog = EntityProfileDialog(tmp_path, catalog(), "source", source_category, dict,
                                 None, Mock(), PageStatus("tagging"))
    dialog.profile_id = dialog.repository.create("source", "source")
    dialog.repository.add_posts(dialog.profile_id, [PostMetadata(
        "gelbooru", "1", ("source", "hero", "franchise", "illustrator", "anonymous"),
        {"hero": "character", "franchise": "copyright", "illustrator": "artist", "anonymous": "artist"})])
    before = dialog.repository.tags(dialog.profile_id)
    dialog.tabs.setCurrentIndex(1); dialog.render()
    table = dialog.tables["relation"]
    assert {table.item(row, 0).text() for row in range(table.rowCount())} == expected
    assert dialog.repository.tags(dialog.profile_id) == before
    dialog.repository.exclude("anonymous")
    dialog.status_filter.setCurrentIndex(dialog.status_filter.findData("excluded"))
    assert table.rowCount() == 0
    dialog.close(); app.processEvents()


def test_dialog_composition_hidden_by_default_available_for_exclusion_audit(app, tmp_path):
    dialog = EntityProfileDialog(tmp_path, catalog(), "source", "general", dict,
                                 None, Mock(), PageStatus("tagging"))
    dialog.profile_id = dialog.repository.create("source", "source")
    useful = {"hairclip", "tattoo", "jacket", "scythe"}
    noise = {"1girl", "2girls", "1boy", "solo", "multiple_girls", "highres"}
    dialog.repository.add_posts(dialog.profile_id, [PostMetadata(
        "gelbooru", "1", tuple({"source"} | useful | noise), {"highres": "meta"})])
    dialog.render()
    table = dialog.tables["attribute"]
    assert {table.item(row, 0).text() for row in range(table.rowCount())} == useful
    dialog.status_filter.setCurrentIndex(dialog.status_filter.findData("excluded"))
    assert {table.item(row, 0).text() for row in range(table.rowCount())} == noise
    dialog.close(); app.processEvents()


@pytest.mark.parametrize("kind,category", [("attribute", "general"), ("relation", "character")])
def test_frequency_initial_sort_is_numeric_and_user_choice_survives_reload(app, tmp_path, kind, category):
    dialog = EntityProfileDialog(tmp_path, catalog(), "source", "artist", dict,
                                 None, Mock(), PageStatus("tagging"))
    dialog.profile_id = dialog.repository.create("source", "source")
    dialog.repository.add_posts(dialog.profile_id, [PostMetadata(
        "gelbooru", str(i), tuple(["source", "z_high"] + (["a_medium"] if i <= 2 else [])
                                  + (["b_low"] if i == 1 else [])),
        {tag: category for tag in ("z_high", "a_medium", "b_low")}) for i in range(1, 13)])
    dialog.render(); table = dialog.tables[kind]
    assert [table.item(row, 0).text() for row in range(3)] == ["z_high", "a_medium", "b_low"]
    assert [table.item(row, 2).data(Qt.ItemDataRole.DisplayRole) for row in range(3)] == [100., 16.67, 8.33]
    table.sortItems(0, Qt.SortOrder.AscendingOrder)
    dialog.render(); dialog._reload_datasets()
    assert table.item(0, 0).text() == "a_medium"
    dialog.close(); app.processEvents()


@pytest.mark.parametrize("kind,category", [("attribute", "general"), ("relation", "character")])
@pytest.mark.parametrize("status_filter", ["all", "review"])
def test_table_shortcuts_advance_in_visible_sort_order_and_stay_scoped(app, tmp_path, kind, category, status_filter):
    dialog = EntityProfileDialog(tmp_path, catalog(), "source", "artist", dict,
                                 None, Mock(), PageStatus("tagging"))
    dialog.profile_id = dialog.repository.create("source", "source")
    tags = ("a_pick", "b_pick", "c_pick", "d_hidden")
    dialog.repository.add_posts(dialog.profile_id, [PostMetadata(
        "gelbooru", "1", ("source", *tags), dict.fromkeys(tags, category))])
    dialog.render(); dialog.tabs.setCurrentIndex(0 if kind == "attribute" else 1)
    table = dialog.tables[kind]
    dialog.filter.setText("pick")
    dialog.status_filter.setCurrentIndex(dialog.status_filter.findData(status_filter))
    table.sortItems(0, Qt.SortOrder.DescendingOrder)
    dialog.show(); app.processEvents(); table.setFocus(); table.selectRow(0)
    assert selected_tag(table) == "c_pick"
    QTest.keyClick(table, Qt.Key.Key_A); app.processEvents()
    assert selected_tag(table) == "b_pick"
    assert next(row for row in dialog.repository.tags(dialog.profile_id) if row["tag"] == "c_pick")["status"] == "keep"
    QTest.keyClick(table, Qt.Key.Key_R); app.processEvents()
    assert selected_tag(table) == "a_pick"
    assert next(row for row in dialog.repository.tags(dialog.profile_id) if row["tag"] == "b_pick")["status"] == "ignore"
    with patch.object(dialog, "inspect_posts") as inspect:
        QTest.keyClick(table, Qt.Key.Key_Space)
        inspect.assert_called_once_with("a_pick")
    QTest.keyClick(table, Qt.Key.Key_B); app.processEvents()
    assert "a_pick" in dialog.repository.exclusions()
    assert "d_hidden" not in dialog.repository.exclusions()
    assert len(dialog.repository.tags(dialog.profile_id)) == 4
    # Main actions omit Review, but it remains a filter and a context-menu action.
    buttons = [button.text() for button in dialog.findChildren(QPushButton)]
    assert catalog().text("entity.review") not in buttons
    assert any(button.startswith(catalog().text("entity.blacklist")) for button in buttons)
    dialog.filter.setFocus()
    with patch.object(dialog, "inspect_posts") as inspect:
        QTest.keyClick(dialog.filter, Qt.Key.Key_Space)
        QTest.keyClick(dialog.filter, Qt.Key.Key_A)
        QTest.keyClick(dialog.filter, Qt.Key.Key_R)
        QTest.keyClick(dialog.filter, Qt.Key.Key_B)
        inspect.assert_not_called()
    dialog.close(); app.processEvents()


@pytest.mark.parametrize("filtered", [False, True])
def test_review_shortcut_last_row_is_safe(app, tmp_path, filtered):
    dialog = EntityProfileDialog(tmp_path, catalog(), "source", "general", dict,
                                 None, Mock(), PageStatus("tagging"))
    dialog.profile_id = dialog.repository.create("source", "source")
    dialog.repository.add_posts(dialog.profile_id, [PostMetadata("gelbooru", "1", ("source", "hairclip"))])
    dialog.render()
    if filtered:
        dialog.status_filter.setCurrentIndex(dialog.status_filter.findData("review"))
    dialog.show(); app.processEvents()
    table = dialog.tables["attribute"]; table.setFocus(); table.selectRow(0)
    QTest.keyClick(table, Qt.Key.Key_A); app.processEvents()
    assert selected_tag(table) == (None if filtered else "hairclip")
    QTest.keyClick(table, Qt.Key.Key_R); app.processEvents()
    assert dialog.repository.tags(dialog.profile_id)[0]["status"] == ("keep" if filtered else "ignore")
    dialog.close(); app.processEvents()


@pytest.mark.parametrize("mode", ["system", "dedicated", "custom"])
@pytest.mark.parametrize("canonical", [False, True])
def test_tag_menu_uses_configured_launcher_and_canonical_url(app, tmp_path, mode, canonical):
    commands, system_urls = [], []
    launcher = BrowserLauncher(
        tmp_path, {"gelbooru_browser_mode": mode, "gelbooru_browser_custom_command": 'test-browser "{url}"'},
        detector=BrowserDetector(platform="posix", which=lambda _: "/test/browser"),
        process_runner=lambda command: commands.append(command),
        default_opener=lambda url: system_urls.append(url) or True,
    )
    page = TagBrowserPage(catalog(), browser_launcher=launcher)
    page._show_rows([TagRow(1, "china_dress", 5, 0, 0, canonical_name="qipao")])
    page.show(); app.processEvents()
    label = page.catalog.text("tag_browser.open_canonical_site" if canonical else "tag_browser.open_site", site="Gelbooru")
    class ChoosingMenu(QMenu):
        def exec(self, _pos):
            return next(action for action in self.actions() if action.text() == label)
    with patch("booruflow.presentation.pyside6.tag_browser_page.QMenu", ChoosingMenu), patch("PySide6.QtGui.QDesktopServices.openUrl") as bypass:
        page._show_context_menu(QPoint(5, page.table.rowViewportPosition(0) + 5))
        bypass.assert_not_called()
    expected = "qipao" if canonical else "china_dress"
    if mode == "system":
        assert system_urls[0].endswith("tags=" + expected) and not commands
    else:
        assert commands[0][-1].endswith("tags=" + expected) and not system_urls
        if mode == "dedicated":
            assert any(value.startswith("--user-data-dir=") for value in commands[0])
        else:
            assert commands[0][0] == "test-browser"
    page.close()


def test_tag_browser_entity_action_prefills_canonical_entity(app):
    page = TagBrowserPage(catalog())
    page._show_rows([TagRow(1, "alias", 5, 4, 0, canonical_name="fate_testarossa")])
    page.show(); app.processEvents()
    spy = QSignalSpy(page.entity_profile_requested)
    class ChoosingMenu(QMenu):
        def exec(self, _pos):
            return next(action for action in self.actions() if action.text() == page.catalog.text("entity.analyze_menu"))
    with patch("booruflow.presentation.pyside6.tag_browser_page.QMenu", ChoosingMenu):
        page._show_context_menu(QPoint(5, page.table.rowViewportPosition(0) + 5))
    assert spy.count() == 1
    assert spy.at(0) == ["fate_testarossa", "character"]
    page.close()


def test_main_window_wires_external_configured_launcher_and_entity_action(app):
    from booruflow.presentation.pyside6.main_window import MainWindow
    opened = Mock()
    launcher = Mock()
    window = SimpleNamespace(catalog=catalog(), _settings={}, browser_launcher=launcher,
                             status_controller=Mock(), database_controller=None, _open_entity_profile=opened)
    page = MainWindow._build_tag_browser(window)
    assert page.browser_launcher is launcher
    page.entity_profile_requested.emit("bardiche", "general")
    opened.assert_called_once_with(page, "bardiche", "general")
    page.close()


def test_matching_thumbnails_are_requested_only_on_inspection(app, tmp_path):
    dialog = EntityProfileDialog(tmp_path, catalog(), "bardiche", "general", dict,
                                 None, Mock(), PageStatus("tagging"))
    profile = dialog.repository.create("bardiche", "bardiche")
    dialog.profile_id = profile
    dialog.repository.add_posts(profile, [PostMetadata(
        "gelbooru", "1", ("bardiche", "staff"), extra={"preview_url": "https://test.invalid/preview.jpg"})])
    loader = Mock()
    class InspectionDialog(QDialog):
        def exec(self):
            assert loader.begin_wave.call_count == 1
    with patch("booruflow.presentation.pyside6.entity_profiles.ThumbnailLoader", return_value=loader) as factory:
        dialog.render()
        factory.assert_not_called()
        with patch("booruflow.presentation.pyside6.entity_profiles.QDialog", InspectionDialog):
            dialog.inspect_posts("staff")
        assert loader.begin_wave.call_args[0][0][0].url == "https://test.invalid/preview.jpg"
        loader.shutdown.assert_called_once()
    dialog.close(); app.processEvents()


def test_matching_grid_reuses_tagging_cards_reflows_and_keeps_preview_ids(app, tmp_path):
    launcher = Mock()
    dialog = EntityProfileDialog(tmp_path, catalog(), "source", "general", dict,
                                 None, launcher, PageStatus("tagging"))
    dialog.profile_id = dialog.repository.create("source", "source")
    previews = {str(i): f"https://test.invalid/preview-{i}.jpg" for i in range(1, 5)}
    dialog.repository.add_posts(dialog.profile_id, [PostMetadata(
        "gelbooru", post_id, ("source", "hairclip"), extra={"preview_url": url,
        "file_url": f"https://test.invalid/original-{post_id}.png"}) for post_id, url in previews.items()])

    class FakeLoader(QObject):
        image_ready = Signal(object, object)
        progress = Signal(object)
        begin_wave = Mock()
        shutdown = Mock()
    loader = FakeLoader()

    class InspectionDialog(QDialog):
        def exec(self):
            self.show(); app.processEvents()
            group = self.findChild(CollapsibleResultGroup, "entity_matching_grid")
            assert group is not None and len(group.cards) == 4
            assert all(isinstance(card, ResultCard) for card in group.cards)
            assert group._columns >= 3
            assert group.cards[0].y() == group.cards[1].y()
            assert group.cards[0].x() != group.cards[1].x()
            cards = {card.post["id"]: card for card in group.cards}
            keys = loader.begin_wave.call_args[0][0]
            assert len(keys) == 4
            for index, key in enumerate(keys):
                assert key.url == previews[str(key.post_id)] and "original" not in key.url
                image = QImage(20, 20, QImage.Format.Format_RGB32)
                color = QColor(30 + index * 50, 10, 80); image.fill(color)
                loader.image_ready.emit(key, image)
                card = cards[key.post_id]
                assert card.text() == f"#{key.post_id}"
                assert card.icon().pixmap(20, 20).toImage().pixelColor(10, 10) == color
            QTest.mouseDClick(cards[2], Qt.MouseButton.LeftButton)
            launcher.open.assert_called_once_with("https://gelbooru.com/index.php?page=post&s=view&id=2")
            wide_columns = group._columns
            self.resize(430, 500); app.processEvents()
            assert group._columns < wide_columns
            assert group.cards[-1].y() > group.cards[0].y()
            self.hide()

    with patch("booruflow.presentation.pyside6.entity_profiles.ThumbnailLoader", return_value=loader) as factory:
        dialog.render(); dialog.filter.setText("hair")
        factory.assert_not_called()
        with patch("booruflow.presentation.pyside6.entity_profiles.QDialog", InspectionDialog):
            dialog.inspect_posts("hairclip")
    loader.begin_wave.assert_called_once(); loader.shutdown.assert_called_once()
    dialog.close(); app.processEvents()


def test_close_cancels_worker_before_destroying_dialog(app, tmp_path):
    import shiboken6
    entered, release = Event(), Event()
    def search(*_args, **_kwargs):
        entered.set()
        assert release.wait(3)
        return [{"id": 1, "tags": "bardiche staff"}]
    dialog = EntityProfileDialog(tmp_path, catalog(), "bardiche", "general", dict,
                                 None, Mock(), PageStatus("tagging"))
    dialog.show()
    with patch("booruflow.infrastructure.gelbooru_client.GelbooruDapiClient.search_posts", search):
        dialog.start()
        wait_for(app, entered.is_set)
        worker = dialog.worker
        dialog.close()
        assert worker.cancelled.is_set()
        assert shiboken6.isValid(dialog)
        release.set()
        wait_for(app, lambda: not shiboken6.isValid(dialog)
                 or (dialog.worker is None and not dialog.isVisible()))
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not shiboken6.isValid(dialog)
    with EntityProfileRepository(tmp_path / "var/state/entity_profiles.sqlite") as repository:
        assert repository.profiles()[0]["sample_count"] == 0
        assert repository.profiles()[0]["last_run_state"] == "cancelled"


def test_image_finder_gelbooru_source_respects_configured_launcher(app):
    from booruflow.presentation.pyside6.image_finder_page import ImageFinderPage
    launcher = Mock()
    page = SimpleNamespace(selected=lambda: SimpleNamespace(source_url="https://gelbooru.com/index.php?page=post&id=1"),
                           browser_launcher=launcher)
    with patch("booruflow.presentation.pyside6.image_finder_page.QDesktopServices") as desktop:
        ImageFinderPage._open_source(page)
        launcher.open.assert_called_once()
        desktop.openUrl.assert_not_called()


def test_standalone_entity_action_has_a_real_dialog_and_configured_launcher(app, tmp_path):
    from booruflow.infrastructure.settings import JsonSettingsRepository
    from booruflow.presentation.pyside6.tagging_standalone import StandaloneTaggingWindow
    window = StandaloneTaggingWindow(catalog(), JsonSettingsRepository(tmp_path / "config/settings.json"),
                                     JsonSettingsRepository(tmp_path / "config/credentials.json"))
    window.page.entity_profile_requested.emit("bardiche", "general")
    dialog = window._entity_profile_dialog
    assert dialog.root == tmp_path
    assert dialog.launcher is window.page.browser_launcher
    assert dialog.query.text() == "bardiche"
    assert (tmp_path / "var/state/entity_profiles.sqlite").is_file()
    window.close(); app.processEvents()


@pytest.mark.parametrize("language", ["en", "fr"])
@pytest.mark.parametrize("category", ["character", "general"])
def test_dialog_worker_persists_dataset_and_query_change_does_not_mix(app, tmp_path, language, category):
    status = PageStatus("tagging")
    dialog = EntityProfileDialog(tmp_path, catalog(language), "bardiche", category, dict,
                                 None, Mock(), status)
    assert dialog.query.text() == "bardiche" + (" solo" if category == "character" else "")
    dialog.show(); app.processEvents()
    dialog.query.setText("bardiche weapon")
    dialog.count.setValue(2)
    messages = QSignalSpy(status.priority_message_changed)
    with patch("booruflow.infrastructure.gelbooru_client.GelbooruDapiClient.search_posts",
               return_value=[{"id": 1, "tags": "bardiche staff smile"}, {"id": 2, "tags": "bardiche sword"}]):
        dialog.start()
        assert not dialog.query.isEnabled(), dialog.feedback.text()
        wait_for(app, lambda: dialog.worker is None)
    assert dialog.repository.profile(dialog.profile_id)["sample_count"] == 2
    assert dialog.repository.profile(dialog.profile_id)["query"] == "bardiche weapon"
    assert dialog.tables["attribute"].rowCount() == 2  # smile is globally hidden.
    dialog.status_filter.setCurrentIndex(dialog.status_filter.findData("excluded"))
    assert dialog.tables["attribute"].rowCount() == 1
    assert dialog.tables["attribute"].item(0, 0).text() == "smile"
    assert messages.count() >= 2
    dialog.query.setText("bardiche fate_testarossa")
    with patch("booruflow.infrastructure.gelbooru_client.GelbooruDapiClient.search_posts", return_value=[]):
        dialog.start()
        wait_for(app, lambda: dialog.worker is None)
    assert len(dialog.repository.profiles("bardiche")) == 2
    dialog.close(); app.processEvents()


def seed_profiles(path):
    with EntityProfileRepository(path) as profiles:
        fate = profiles.create("fate_testarossa", "fate_testarossa")
        bardiche = profiles.create("bardiche", "bardiche")
        profiles.add_posts(fate, [PostMetadata("gelbooru", "10", ("fate_testarossa", "bardiche"), {"bardiche": "character"})])
        profiles.add_posts(bardiche, [PostMetadata("gelbooru", "10", ("bardiche", "weapon"))])
        profiles.set_status(fate, "bardiche", "keep")
        profiles.set_status(bardiche, "weapon", "keep")


@pytest.mark.parametrize("category,artist_visible", [("character", False), ("copyright", False), ("artist", True)])
def test_tagging_injection_applies_directional_and_composition_policy(navigation, tmp_path, category, artist_visible):  # noqa: F811
    n = navigation
    n.controller._project_root = tmp_path
    with EntityProfileRepository(tmp_path / "var/state/entity_profiles.sqlite") as profiles:
        profile = profiles.create("profile_subject", "profile_subject")
        tags = ("profile_subject", "illustrator", "hero", "franchise", "anonymous", "1girl", "solo", "hairclip")
        profiles.add_posts(profile, [PostMetadata("gelbooru", "10", tags, {
            "illustrator": "artist", "hero": "character", "franchise": "copyright", "anonymous": "artist"})])
        for row in profiles.tags(profile):
            profiles.set_status(profile, row["tag"], "keep")
    n.page.show_results([{"id": 1, "tags": "profile_subject", "tag_count": 1, "priority": "low",
                         "_tag_categories": {"profile_subject": category}}])
    n.open(1); n.controller._navigation_timer.stop()
    rows = {row["tag"] for row in n.page._all_suggestion_rows}
    assert {"hero", "franchise", "hairclip"} <= rows
    assert ("illustrator" in rows) == artist_visible
    assert not ({"anonymous", "1girl", "solo"} & rows)
    assert n.backend.calls == 0


def test_tagging_derived_does_not_activate_until_confirmed_and_undo_is_local(navigation, tmp_path):  # noqa: F811
    n = navigation
    n.controller._project_root = tmp_path
    path = tmp_path / "var/state/entity_profiles.sqlite"
    seed_profiles(path)
    n.page.show_results([{"id": 1, "tags": "fate_testarossa", "tag_count": 1, "priority": "low"}])
    n.open(1)
    n.controller._navigation_timer.stop()
    rows = {row["tag"]: row for row in n.page._all_suggestion_rows}
    assert "bardiche" in rows and "weapon" not in rows
    assert rows["bardiche"]["id"] == "entity:bardiche"
    repo = n.analysis.repository
    item = repo.item_by_remote_source("gelbooru", "1")
    assert not repo.connection.execute("SELECT analysis_requested FROM analysis_items WHERE id=?", (item.id,)).fetchone()[0]
    assert repo.observations(item.id) == []
    assert repo.batch_entry(item.id) is None
    assert n.page.accept_button.isEnabled()
    n.controller.decide("entity:bardiche", "accepted")
    rows = {row["tag"]: row for row in n.page._all_suggestion_rows}
    assert "weapon" in rows
    assert repo.tag_review_summary(item.id, ["fate_testarossa"])["additions"] == ["bardiche"]
    assert len(repo.observations(item.id)) == 1
    n.controller.undo()
    rows = {row["tag"]: row for row in n.page._all_suggestion_rows}
    assert "weapon" not in rows
    assert rows["bardiche"]["decision"] == "unreviewed"
    assert repo.tag_review_summary(item.id, ["fate_testarossa"])["additions"] == []
    n.controller.redo()
    assert "weapon" in {row["tag"] for row in n.page._all_suggestion_rows}
    assert len(repo.observations(item.id)) == 1
    n.controller.decide("entity:weapon", "rejected")
    n.controller.refresh_local_review()
    rows = {row["tag"]: row for row in n.page._all_suggestion_rows}
    assert rows["weapon"]["decision"] == "rejected"
    assert len(repo.observations(item.id)) == 1  # Rejecting evidence never adds a manual tag.
    assert n.backend.calls == 0


def test_tagging_context_menu_uses_tag_category(navigation):  # noqa: F811
    n = navigation
    n.page.decision_filter.setCurrentIndex(n.page.decision_filter.findData("all"))
    n.page.show_local_review(n.page.catalog.text("tagging.analysis.ready"), None, ["fate_testarossa"], [
        {"id": "existing:fate_testarossa", "tag": "fate_testarossa", "category_id": 4,
         "category": "character", "confidence": "", "decision": "keep", "match": "Existing"}], [], ["fate_testarossa"])
    spy = QSignalSpy(n.page.entity_profile_requested)
    menu_called = []
    class ChoosingMenu(QMenu):
        def exec(self, _pos):
            menu_called.append(True)
            return next(action for action in self.actions()
                        if action.text() == n.page.catalog.text("entity.analyze_menu"))
    with patch("booruflow.presentation.pyside6.tagging_page.QMenu", ChoosingMenu):
        n.page._entity_suggestion_menu(QPoint(5, n.page.suggestions.rowViewportPosition(1) + 5))
        assert len(menu_called) == 1
    assert spy.count() == 1
    assert spy.at(0) == ["fate_testarossa", "character"]
