"""Exercise the loaded grid through its menu and independent analytical chips."""
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMenu

from booruflow.application.targeted_wd14 import TargetedWD14Progress, TargetedWD14Result
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.tagging_page import TaggingPage


@pytest.fixture
def grid():
    app = QApplication.instance() or QApplication([])
    catalog = LanguageCatalog(Path(__file__).resolve().parents[2] / "resources/i18n", "en")
    page = TaggingPage(catalog, {})
    page.resize(1200, 850)
    page.show()
    # ID, artist and tags all yield different orders. Artist ties ignore case.
    page.show_results([
        {"id": 1, "tags": "z", "artist_tags": ["Z"], "priority": "low", "tag_count": 1},
        {"id": 2, "tags": "y", "artist_tags": ["a"], "priority": "low", "tag_count": 1},
        {"id": 3, "tags": "x", "artist_tags": ["A"], "priority": "low", "tag_count": 1},
        {"id": 4, "tags": "w", "priority": "low", "tag_count": 1},
        {"id": 5, "tags": "w", "priority": "low", "tag_count": 1},
    ])
    app.processEvents()
    yield app, page
    page.close()
    page.deleteLater()
    app.processEvents()


def ids(page):
    return [card.post["id"] for group in page.result_groups for card in group.cards]


def choose_sort(page, mode):
    # Open the real card context menu; keyboard selection returns its QAction.
    def select():
        menu = QApplication.activePopupWidget()
        assert isinstance(menu, QMenu)
        parent_action = next(action for action in menu.actions() if action.menu() is not None)
        submenu = parent_action.menu()
        action = next(action for action in submenu.actions() if action.data() == mode)
        menu.setActiveAction(parent_action)
        QTest.keyClick(menu, Qt.Key.Key_Right)
        def activate(parent_action=parent_action):
            QTest.mouseClick(submenu, Qt.MouseButton.LeftButton,
                             pos=submenu.actionGeometry(action).center())
        QTimer.singleShot(30, activate)
    QTimer.singleShot(30, select)
    timeout = QTimer(page)
    timeout.setSingleShot(True)
    timeout.timeout.connect(lambda: [menu.close() for menu in page.findChildren(QMenu)])
    timeout.start(2000)
    try:
        page.result_buttons[1].customContextMenuRequested.emit(QPoint(5, 5))
    finally:
        timeout.stop()
        timeout.deleteLater()


def complete(page, tag, scores):
    page.show_targeted_wd14_result(TargetedWD14Result(
        tag, scores, frozenset(), TargetedWD14Progress(5, 5, 0, 5, 0), 0.1,
    ))


def assert_layout(page):
    assert page.results_layout.count() == len(page.result_groups)
    for group in page.result_groups:
        assert [group.grid.itemAt(i).widget() for i in range(group.grid.count())] == group.cards


def test_real_sort_menu_reorders_layout_without_io(grid):
    app, page = grid
    cards = dict(page.result_buttons)
    page.result_buttons[3].setChecked(True)
    requested = QSignalSpy(page.targeted_wd14_requested)
    search = QSignalSpy(page.start_requested)
    with patch.object(page.thumbnail_loader, "extend_wave") as extend, \
            patch.object(page.thumbnail_loader, "begin_wave") as begin:
        assert ids(page) == [1, 2, 3, 4, 5]
        for mode, expected in (("artist", [2, 3, 1, 4, 5]), ("id", [1, 2, 3, 4, 5]),
                               ("tags", [4, 5, 3, 2, 1])):
            choose_sort(page, mode)
            assert ids(page) == expected
            assert_layout(page)
            app.processEvents()
            assert all(card.isVisible() for card in cards.values())
            assert page.result_buttons == cards
            assert [post["id"] for post in page.selected_posts()] == [3]
        extend.assert_not_called()
        begin.assert_not_called()
    assert requested.count() == search.count() == 0


def test_analysis_toggle_uses_cached_scores_and_preserves_bulk_isolation(grid):
    app, page = grid
    page.bulk_add.add_tag("1girl")
    page.bulk_add._chips["1girl"].analysis.click()
    complete(page, "1girl", {1: .95, 2: .85, 3: .95, 4: .75, 5: .75})
    app.processEvents()
    assert ids(page) == [1, 3, 2, 4, 5]
    requested = QSignalSpy(page.targeted_wd14_requested)
    search = QSignalSpy(page.start_requested)
    cards = dict(page.result_buttons)
    page.result_buttons[2].setChecked(True)
    with patch.object(page.thumbnail_loader, "extend_wave") as extend:
        page.bulk_add._chips["1girl"].active.click()
        page.bulk_add.toggle_operation("1girl")
        assert ids(page) == [1, 3, 2, 4, 5]
        chip = page.analysis_palette._chips["1girl"]
        chip.active.click()
        assert ids(page) == [1, 2, 3, 4, 5]
        assert all("%" not in group.title for group in page.result_groups)
        chip.active.click()
        assert ids(page) == [1, 3, 2, 4, 5]
        titles = [group.title for group in page.result_groups]
        choose_sort(page, "artist")
        assert ids(page) == [3, 1, 2, 4, 5]
        choose_sort(page, "tags")
        assert ids(page) == [3, 1, 2, 4, 5]
        assert [group.title for group in page.result_groups] == titles
        assert_layout(page)
        assert page.result_buttons == cards
        assert [post["id"] for post in page.selected_posts()] == [2]
        extend.assert_not_called()
    assert requested.count() == search.count() == 0


def test_spinner_identity_and_two_cached_analyses(grid):
    _app, page = grid
    for tag in ("1girl", "2girls"):
        page.bulk_add.add_tag(tag)
    page.bulk_add._chips["1girl"].analysis.click()
    complete(page, "1girl", {1: .95, 2: .85, 3: .95, 4: .75, 5: .75})
    page.bulk_add._chips["2girls"].analysis.click()
    for editor in (page.bulk_add, page.analysis_palette):
        assert editor._chips["1girl"].analysis.text() == "✓"
        assert editor._chips["2girls"].analysis.text() == "⏳"
    complete(page, "2girls", {1: .15, 2: .95, 3: .95, 4: .75, 5: .75})
    assert ids(page) == [3, 2, 4, 5, 1]
    requested = QSignalSpy(page.targeted_wd14_requested)
    for editor in (page.bulk_add, page.analysis_palette):
        assert editor._chips["1girl"].analysis.text() == "✓"
        assert editor._chips["2girls"].analysis.text() == "✓"
    page.analysis_palette._chips["2girls"].active.click()
    assert ids(page) == [1, 3, 2, 4, 5]
    page.analysis_palette._chips["2girls"].active.click()
    assert ids(page) == [3, 2, 4, 5, 1]
    assert requested.count() == 0
    # Reusing an already available analysis through its analysis button is local too.
    page.analysis_palette._chips["2girls"].analysis.click()
    assert requested.count() == 0


def test_running_analysis_is_single_and_cancel_failure_clear_its_spinner(grid):
    _app, page = grid
    for tag in ("1girl", "2girls"):
        page.bulk_add.add_tag(tag)
    page.bulk_add._chips["2girls"].analysis.click()
    complete(page, "2girls", {i: .9 for i in range(1, 6)})
    requested = QSignalSpy(page.targeted_wd14_requested)
    page.bulk_add._chips["1girl"].analysis.click()
    page.bulk_add._chips["2girls"].analysis.click()
    assert requested.count() == 1
    assert page.analysis_palette._chips["1girl"].analysis.text() == "⏳"
    assert page.analysis_palette._chips["2girls"].analysis.text() == "✓"
    page.show_targeted_wd14_unavailable("1girl", "test failure")
    assert page.analysis_palette._chips["1girl"].analysis.text() == "!"
    assert page.analysis_palette._chips["2girls"].analysis.text() == "✓"
    page.bulk_add._chips["1girl"].analysis.click()
    page.show_targeted_wd14_cancelled(TargetedWD14Result(
        "1girl", {}, frozenset(), TargetedWD14Progress(0, 5, 0, 0, 0), .1,
    ))
    assert page.analysis_palette._chips["1girl"].analysis.text() == "—"
    assert page.analysis_palette._chips["2girls"].analysis.text() == "✓"
    assert page._running_confidence_tag == ""
