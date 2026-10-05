from unittest.mock import patch

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QSignalSpy, QTest

from booruflow.presentation.pyside6.tag_presentation import present_tag
from booruflow.presentation.pyside6.tagging_suggestions import (
    BUCKET_ROLE,
    INITIAL_ROLE,
    ROW_KIND_ROLE,
    STATE_COLUMN,
)
from tests.unit.test_tagging_navigation_prefetch import navigation  # noqa: F401


@pytest.mark.parametrize("count", [1, 3])
@pytest.mark.parametrize("hide_queued", [False, True])
def test_space_at_end_returns_to_grid_without_orphan_review(navigation, count, hide_queued):  # noqa: F811
    n = navigation
    n.show(count)
    n.page.hide_queued_results.setChecked(hide_queued)
    n.page.set_reviewed_post_ids(set(range(1, count)))
    n.open(count)
    n.resolve(count)
    n.worker.process_one()
    n.analysis._handle_worker_line("[DEBUG] [Worker] [item:1] Item ready")
    QTest.keyClick(n.page.suggestions, Qt.Key.Key_Space)
    n.app.processEvents()
    assert n.page.mode_stack.currentWidget() is n.page.search_view
    assert " / " not in n.footer.page_label.text()
    assert n.controller._navigation_prefetch is None
    QTest.keyClick(n.page.results_scroll, Qt.Key.Key_Space)
    n.app.processEvents()
    assert n.page.mode_stack.currentWidget() is n.page.search_view


@pytest.fixture
def review(navigation):  # noqa: F811
    n = navigation
    rows = [
        {"id": "existing:A", "tag": "A", "confidence": "0.99", "decision": "remove",
         "match": "Existing + also WD14", "category": "5", "category_id": 5},
        *[{"id": i, "tag": name, "confidence": score, "decision": decision,
           "match": "exact", "category": "4", "category_id": 4}
          for i, name, score, decision in (
              (2, "B", "0.95", "unreviewed"), (3, "C", "0.94", "unreviewed"),
              (4, "D", "0.93", "accepted"), (5, "E", "0.89", "unreviewed"),
              (6, "F", "0.82", "unreviewed"), (7, "G", "0.71", "unreviewed"),
              (8, "H", "0.69", "unreviewed"),
          )],
    ]
    page = n.page
    page.decision_filter.setCurrentIndex(page.decision_filter.findData("all"))

    def refresh():
        page.show_local_review(page.catalog.text("tagging.analysis.ready"), None, ["A"], rows, [], ["A"])

    def decide(tokens, decision):
        tokens = tokens if isinstance(tokens, list) else [tokens]
        for row in rows:
            if str(row["id"]) in {str(token) for token in tokens}:
                row["decision"] = decision
        refresh()

    page.decision_requested.connect(decide)
    refresh()
    n.app.processEvents()
    page.activateWindow()
    page.suggestions.setFocus()
    n.app.processEvents()
    yield n, rows


def focus_bucket(table, bucket):
    row = next(row for row in range(table.rowCount())
               if table.item(row, 0).data(ROW_KIND_ROLE) == "GROUP"
               and table.item(row, 0).data(BUCKET_ROLE) == bucket)
    table.selectionModel().setCurrentIndex(table.model().index(row, 0),
                                           QItemSelectionModel.SelectionFlag.NoUpdate)
    return row


def tag_row(table, name):
    return next(row for row in table.tag_rows() if table.item(row, 0).text() == name)


def ctrl_space(table):
    QTest.keyClick(table, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier)


def test_bucket_toggle_multiselect_accept_and_filter(review):
    n, _rows = review
    table = n.page.suggestions
    assert table.item(tag_row(table, "B"), STATE_COLUMN).text() == "?"
    assert table.verticalHeader().isHidden()
    assert table.horizontalHeader().visualIndex(STATE_COLUMN) == 0
    assert [table.item(row, 0).text() for row in range(table.rowCount())
            if table.item(row, 0).data(ROW_KIND_ROLE) == "GROUP"] == [
                "90–100 %", "80–89 %", "70–79 %", "60–69 %",
            ]
    table.clearSelection()
    focus_bucket(table, 9)
    ctrl_space(table)
    assert set(table.selected_tokens()) == {"2", "3"}
    ctrl_space(table)
    assert table.selected_tokens() == []
    ctrl_space(table)
    focus_bucket(table, 8)
    ctrl_space(table)
    assert set(table.selected_tokens()) == {"2", "3", "5", "6"}
    sent = QSignalSpy(n.page.decision_requested)
    QTest.keyClick(table, Qt.Key.Key_A)
    assert sent.count() == 1
    assert set(sent.at(0)[0]) == {"2", "3", "5", "6"}
    for name in "BCDEF":
        assert table.item(tag_row(table, name), STATE_COLUMN).text() == "✓"
    assert table.item(tag_row(table, "A"), STATE_COLUMN).text() == "✕"
    n.page.decision_filter.setCurrentIndex(n.page.decision_filter.findData("unreviewed"))
    assert [table.item(row, 0).text() for row in table.tag_rows()] == ["G", "H"]
    assert table.rowCount() == 4  # Two non-empty groups only.
    focus_bucket(table, 7)
    table.clearSelection()
    ctrl_space(table)
    QTest.keyClick(table, Qt.Key.Key_R)
    assert [table.item(row, 0).text() for row in table.tag_rows()] == ["H"]


def test_group_never_dispatches_and_tag_ctrl_space_remains_standard(review):
    n, _rows = review
    table = n.page.suggestions
    group = focus_bucket(table, 9)
    table.clearSelection()
    table.selectRow(group)
    sent = QSignalSpy(n.page.decision_requested)
    QTest.keyClick(table, Qt.Key.Key_A)
    QTest.keyClick(table, Qt.Key.Key_R)
    assert sent.count() == 0
    assert not table.item(group, 0).flags() & Qt.ItemFlag.ItemIsEditable
    table.clearSelection()
    row = tag_row(table, "B")
    table.selectionModel().setCurrentIndex(table.model().index(row, 0),
                                           QItemSelectionModel.SelectionFlag.NoUpdate)
    ctrl_space(table)
    assert table.selected_tokens() == ["2"]
    ctrl_space(table)
    assert table.selected_tokens() == []
    table.selectRow(row)
    QTest.keyClick(table, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    assert set(table.selected_tokens()) == {"2", "3"}
    QTest.keyClick(table, Qt.Key.Key_R)
    assert set(sent.at(0)[0]) == {"2", "3"}


def test_initial_presence_is_independent_and_hot_theme_preserves_everything(review):
    n, _rows = review
    table = n.page.suggestions
    assert table.item(tag_row(table, "A"), 0).data(INITIAL_ROLE)
    assert not table.item(tag_row(table, "B"), 0).data(INITIAL_ROLE)
    table.selectRow(tag_row(table, "B"))
    QTest.keyClick(table, Qt.Key.Key_A)
    assert table.item(tag_row(table, "B"), STATE_COLUMN).text() == "✓"
    assert not table.item(tag_row(table, "B"), 0).data(INITIAL_ROLE)
    table.selectRow(tag_row(table, "A"))
    QTest.keyClick(table, Qt.Key.Key_R)
    assert table.item(tag_row(table, "A"), 0).data(INITIAL_ROLE)
    selected = table.selected_tokens()
    current = table.currentRow()
    count = table.rowCount()
    groups = [table.item(row, 0).text() for row in range(count)
              if table.item(row, 0).data(ROW_KIND_ROLE) == "GROUP"]
    with patch.object(n.page, "_render_suggestions", side_effect=AssertionError("theme rebuilt rows")), \
            patch.object(n.controller, "refresh_local_review", side_effect=AssertionError("theme ran business logic")):
        for dark in (False, True, False):
            palette = QPalette(table.palette())
            palette.setColor(QPalette.ColorRole.Window, QColor("#202020" if dark else "#ffffff"))
            palette.setColor(QPalette.ColorRole.Base, QColor("#252525" if dark else "#ffffff"))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#353535" if dark else "#eeeeee"))
            palette.setColor(QPalette.ColorRole.Text, QColor("white" if dark else "black"))
            table.setPalette(palette)
            n.app.processEvents()
            row = tag_row(table, "A")
            assert table.item(row, 0).foreground().color() == present_tag("gelbooru", "A", 5, palette).style.color(palette)
            assert table.item(row, 0).background().color() == palette.color(QPalette.ColorRole.AlternateBase)
            assert table.item(row, STATE_COLUMN).text() == "✕"
            assert table.selected_tokens() == selected
            assert table.currentRow() == current
            assert table.rowCount() == count
            assert [table.item(row, 0).text() for row in range(count)
                    if table.item(row, 0).data(ROW_KIND_ROLE) == "GROUP"] == groups


def test_sorting_stays_inside_buckets_and_keep_is_accepted(review):
    n, rows = review
    table = n.page.suggestions
    rows[0]["decision"] = "keep"
    n.page.show_local_review(n.page.catalog.text("tagging.analysis.ready"), None, ["A"], rows, [], ["A"])
    assert table.item(tag_row(table, "A"), STATE_COLUMN).text() == "✓"
    table.sortItems(0, Qt.SortOrder.DescendingOrder)
    assert [table.item(row, 0).text() for row in table.tag_rows()] == list("DCBAFEGH")
    table.sortItems(1, Qt.SortOrder.DescendingOrder)
    assert [table.item(row, 0).text() for row in table.tag_rows()] == list("ABCDEFGH")
