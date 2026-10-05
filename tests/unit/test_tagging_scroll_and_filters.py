"""Native scrolling and persisted decisions through the visible review filters."""
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSlider

from booruflow.domain.image_analysis import DecisionState, ObservationSource, SourceTag
from booruflow.infrastructure.wd14 import WD14Result, WD14Tag
from booruflow.presentation.pyside6.pages import ScrollablePageHost
from booruflow.presentation.pyside6.tagging_suggestions import ROW_KIND_ROLE, STATE_COLUMN
from tests.unit.test_tagging_navigation_prefetch import navigation  # noqa: F401


def visible_tags(page):
    table = page.suggestions
    return [table.item(row, 0).text() for row in table.tag_rows()]


@pytest.mark.parametrize("preloaded", [False, True])
def test_bulk_wheel_and_scrollbar_move_content_without_moving_actions(navigation, preloaded):  # noqa: F811
    n = navigation
    page = n.page
    page.hide()
    if preloaded:
        for i in range(100):
            page.bulk_palette.add_tag(f"long_bulk_tag_{i:03}")
    host = ScrollablePageHost(page)
    host.resize(1200, 850)
    host.show()
    try:
        if not preloaded:
            for i in range(100):
                page.bulk_palette.add_tag(f"long_bulk_tag_{i:03}")
        QTest.qWait(50)
        scroll = page.bulk_palette.chips_scroll
        content = scroll.widget()
        bar = scroll.verticalScrollBar()
        print("bulk geometry", scroll.viewport().size().toTuple(), content.size().toTuple(),
              content.sizeHint().toTuple(), content.minimumSizeHint().toTuple(),
              content.minimumSize().toTuple(), type(content.layout()).__name__,
              bar.minimum(), bar.maximum(), bar.pageStep(), bar.value())
        assert content is page.bulk_palette.chips
        assert content.height() >= content.layout().heightForWidth(content.width())
        assert bar.maximum() > 0
        input_position = page.bulk_palette.input.mapToGlobal(QPoint())
        apply_position = page.bulk_apply.mapToGlobal(QPoint())
        first = page.bulk_palette._chips["long_bulk_tag_000"]
        first_y = first.mapTo(scroll.viewport(), QPoint()).y()
        bar.setValue(bar.maximum())
        n.app.processEvents()
        assert bar.value() > 0
        assert content.y() == -bar.value()
        assert first.mapTo(scroll.viewport(), QPoint()).y() < first_y
        last = page.bulk_palette._chips["long_bulk_tag_099"]
        assert scroll.viewport().rect().intersects(last.rect().translated(last.mapTo(scroll.viewport(), QPoint())))
        bar.setValue(0)
        pos = QPointF(scroll.viewport().rect().center())
        wheel = QWheelEvent(pos, QPointF(scroll.viewport().mapToGlobal(pos.toPoint())),
                            QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(scroll.viewport(), wheel)
        assert bar.value() > 0
        assert content.y() == -bar.value()
        bar.setValue(0)
        option = QStyleOptionSlider()
        bar.initStyleOption(option)
        handle = bar.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar, option,
                                           QStyle.SubControl.SC_ScrollBarSlider, bar)
        QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=handle.center())
        QTest.mouseMove(bar, QPoint(handle.center().x(), bar.height() - 25), delay=20)
        QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=QPoint(handle.center().x(), bar.height() - 25))
        n.app.processEvents()
        assert bar.value() > 0
        assert content.y() == -bar.value()
        assert page.bulk_palette.input.mapToGlobal(QPoint()) == input_position
        assert page.bulk_apply.mapToGlobal(QPoint()) == apply_position
        page.bulk_splitter.setSizes([220, 900])
        QTest.qWait(30)
        assert content.height() >= content.layout().heightForWidth(content.width())
        assert bar.maximum() > 0
        for tag in page.bulk_palette.tags():
            page.bulk_palette.remove_tag(tag)
        QTest.qWait(30)
        assert bar.maximum() == 0
    finally:
        host.takeWidget()
        host.close()
        host.deleteLater()


@pytest.mark.parametrize("category", ["general", "character", "metadata", "unknown", "0", 0, None])
def test_filters_track_persisted_accept_reject_without_reload(navigation, category):  # noqa: F811
    n = navigation
    n.show(1)
    n.open(1)
    item_id = n.resolve(1)
    repo = n.analysis.repository
    repo.replace_source_metadata(item_id, "gelbooru", (SourceTag("b", ObservationSource.GELBOORU, category),), ())
    with patch.object(n.backend, "analyze", return_value=WD14Result(tuple(
        WD14Tag(name, "general", score) for name, score in (("a", .99), ("b", .85), ("c", .71))
    ))):
        assert n.worker.process_one()
    observations = {row.name: token for token, row in repo.observations(item_id)}
    repo.decide_observation(observations["c"], DecisionState.REJECTED)
    n.controller.refresh_local_review()
    n.app.processEvents()
    n.page.activateWindow()

    def check(expected):
        for value, tags in expected.items():
            combo = n.page.decision_filter
            combo.setCurrentIndex(combo.findData(value))
            assert set(visible_tags(n.page)) == set(tags)
            table = n.page.suggestions
            groups = [row for row in range(table.rowCount())
                      if table.item(row, 0).data(ROW_KIND_ROLE) == "GROUP"]
            assert len(groups) == len(tags)  # Different bucket for each tag.
        assert n.page.current_post_id == 1

    check({"all": "abc", "unreviewed": "a", "accepted": "b", "rejected": "c"})
    n.page.decision_filter.setCurrentIndex(n.page.decision_filter.findData("unreviewed"))
    table = n.page.suggestions
    assert table.item(table.tag_rows()[0], STATE_COLUMN).text() == "?"
    table.selectRow(table.tag_rows()[0])
    table.setFocus()
    QTest.keyClick(table, Qt.Key.Key_A)
    assert dict(repo.observations(item_id))[observations["a"]].decision is DecisionState.ACCEPTED
    assert visible_tags(n.page) == []
    check({"all": "abc", "unreviewed": "", "accepted": "ab", "rejected": "c"})
    n.page.decision_filter.setCurrentIndex(n.page.decision_filter.findData("accepted"))
    table.selectRow(next(row for row in table.tag_rows() if table.item(row, 0).text() == "a"))
    table.setFocus()
    QTest.keyClick(table, Qt.Key.Key_R)
    assert dict(repo.observations(item_id))[observations["a"]].decision is DecisionState.REJECTED
    assert visible_tags(n.page) == ["b"]
    check({"all": "abc", "unreviewed": "", "accepted": "b", "rejected": "ac"})
