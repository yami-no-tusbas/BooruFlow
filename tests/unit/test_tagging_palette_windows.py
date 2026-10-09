"""Observe native window events, including windows reparented before the event loop."""

import json
import os
import sys
import traceback
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.tagging_page import TaggingPage


def widget_info(widget):
    if widget is None:
        return None
    return {
        'python_class': type(widget).__name__,
        'qt_class': widget.metaObject().className(),
        'object_name': widget.objectName(),
    }


class WindowTrace(QObject):
    """Test-only observer; event records retain metadata rather than widget wrappers."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.context = 'empty construction'
        self.intended = []
        self.events = []
        app.installEventFilter(self)

    def eventFilter(self, widget, event):
        watched = {
            QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.FocusIn,
            QEvent.Type.FocusOut, QEvent.Type.WindowActivate,
        }
        if (
            isinstance(widget, QWidget) and event.type() in watched
            and (widget.isWindow() or event.type() in {QEvent.Type.FocusIn, QEvent.Type.FocusOut})
        ):
            tag = None
            if event.type() == QEvent.Type.Show and widget.isWindow():
                frame = sys._getframe()
                while frame is not None:
                    if frame.f_code.co_name == 'add_tag':
                        tag = frame.f_locals.get('normalized')
                        break
                    frame = frame.f_back
                del frame
            self.events.append({
                **widget_info(widget),
                'event': event.type().name,
                'context': self.context,
                'tag': tag,
                'parent': widget_info(widget.parent()),
                'flags': widget.windowFlags().value,
                'is_window': widget.isWindow(),
                'visible': widget.isVisible(),
                'focus_policy': widget.focusPolicy().name,
                'focus_widget': widget_info(self.app.focusWidget()),
                'unexpected': widget.isWindow() and widget not in self.intended,
                'caller': traceback.format_stack(limit=12)
                if event.type() == QEvent.Type.Show and widget.isWindow() else [],
            })
        return False

    def unexpected_windows(self):
        return [event for event in self.events if event['unexpected']
                and event['event'] in {'Show', 'Hide'}]


@pytest.fixture
def window_trace(request):
    app = QApplication.instance() or QApplication([])
    trace = WindowTrace(app)
    yield app, trace
    app.removeEventFilter(trace)
    if destination := os.environ.get('BOORUFLOW_PALETTE_WINDOW_TRACE'):
        path = Path(destination)
        path = path.with_name(f'{path.stem}-{request.node.name}{path.suffix}')
        path.write_text(json.dumps(trace.events, indent=2), encoding='utf-8')


def settle(app):
    app.processEvents()
    QTest.qWait(25)
    app.processEvents()


def show_page(app, trace, settings):
    catalog = LanguageCatalog(Path(__file__).resolve().parents[2] / 'resources/i18n', 'en')
    page = TaggingPage(catalog, settings)
    trace.intended.append(page)
    page.resize(1200, 850)
    page.show()
    page.activateWindow()
    settle(app)
    return page


@pytest.mark.parametrize(('focus_target', 'insertion'), [
    ('palette', 'direct'), ('search', 'direct'), ('palette', 'enter'),
])
def test_empty_one_many_and_persisted_palette_have_no_transient_windows(
    window_trace, tmp_path, focus_target, insertion,
):
    app, trace = window_trace
    pages = []
    try:
        page = show_page(app, trace, {})
        pages.append(page)
        palette = page.bulk_palette
        assert palette.tags() == []
        focus = palette.input if focus_target == 'palette' else page.query.input
        focus.setFocus(Qt.FocusReason.OtherFocusReason)
        settle(app)
        assert app.focusWidget() is focus
        checkpoints = [len(trace.unexpected_windows())]
        added, changes, analyses = [], [], []
        palette.tag_added.connect(added.append)
        palette.tags_changed.connect(lambda: changes.append(True))
        palette.analysis_requested.connect(analyses.append)
        for number in range(24):
            trace.context = f'add left palette tag palette_{number}'
            if insertion == 'enter':
                palette.input.setText(f'palette_{number}')
                QTest.keyClick(palette.input, Qt.Key.Key_Return)
            else:
                palette.add_tag(f'palette_{number}', active=number % 3 != 0,
                                operation='remove' if number % 2 else 'add')
            settle(app)
            checkpoints.append(len(trace.unexpected_windows()))
            # Test each insertion, so transient focus loss cannot hide behind later restoration.
            assert app.focusWidget() is focus
            assert not any(event['event'] == 'FocusOut' and event['context'] == trace.context
                           for event in trace.events)
        assert added == [f'palette_{number}' for number in range(24)]
        assert len(changes) == 24
        assert analyses == []
        persisted = tmp_path / 'palette.json'
        entries = palette.entries()
        persisted.write_text(json.dumps(page.settings), encoding='utf-8')
        trace.context = 'close first page'
        page.close()
        settle(app)
        trace.context = 'persisted reload of 24 left palette tags'
        restored = show_page(app, trace, json.loads(persisted.read_text(encoding='utf-8')))
        pages.append(restored)
        assert restored.bulk_palette.entries() == entries
        focus = restored.query.input
        focus.setFocus()
        settle(app)
        trace.context = 'passive repopulation with another control focused'
        for entry in entries:
            restored.bulk_palette.remove_tag(entry['tag'])
        # Removal legitimately persisted an empty palette. Restore the saved
        # state explicitly, as startup does, while another input retains focus.
        restored.settings['tagging_bulk_palette'] = entries
        restored_changes, restored_additions = [], []
        restored.bulk_palette.tags_changed.connect(lambda: restored_changes.append(True))
        restored.bulk_palette.tag_added.connect(restored_additions.append)
        restored._restore_bulk_palette()
        settle(app)
        assert restored.bulk_palette.entries() == entries
        assert app.focusWidget() is focus
        assert restored_changes == [] and restored_additions == []
        checkpoints.append(len(trace.unexpected_windows()))
        assert checkpoints == [0] * 26, trace.unexpected_windows()
        assert [widget for widget in app.topLevelWidgets() if widget.isVisible()] == [restored]
    finally:
        trace.context = 'cleanup'
        for page in pages:
            page.close()
            page.deleteLater()
        settle(app)


def test_cached_suggestions_do_not_open_popup_when_adding_palette_tag(window_trace):
    app, trace = window_trace
    page = show_page(app, trace, {})
    try:
        palette = page.bulk_palette
        palette.set_suggestions(['solo', '1boy', '1girl'])
        palette.input.setFocus()
        settle(app)
        trace.events.clear()
        trace.context = 'add solo with cached suggestions and focused empty input'
        palette.add_tag('solo')
        settle(app)
        # A delayed lookup response for the now-empty input must also stay passive.
        palette.set_suggestions(['1boy', '1girl'])
        palette.remove_tag('solo')
        settle(app)
        assert not trace.unexpected_windows(), trace.unexpected_windows()
        assert app.focusWidget() is palette.input
    finally:
        page.bulk_palette.completer.popup().hide()
        page.close()
        page.deleteLater()
        settle(app)


def test_explicit_palette_completion_buttons_and_context_menu_still_work(window_trace):
    app, trace = window_trace
    page = show_page(app, trace, {})
    try:
        palette = page.bulk_palette
        palette.input.setFocus()
        settle(app)
        trace.events.clear()
        trace.context = 'explicit user typing and completion'
        QTest.keyClicks(palette.input, 'sol')
        popup = palette.completer.popup()
        trace.intended.append(popup)
        palette.set_suggestions(['solo'])
        settle(app)
        assert popup.isVisible()
        QTest.mouseClick(popup.viewport(), Qt.MouseButton.LeftButton,
                         pos=popup.visualRect(popup.model().index(0, 0)).center())
        settle(app)
        assert palette.tags() == ['solo']
        assert not popup.isVisible()
        chip = palette._chips['solo']
        changed, analysis = [], []
        palette.tags_changed.connect(lambda: changed.append(palette.entries()))
        palette.analysis_requested.connect(analysis.append)
        trace.context = 'explicit chip button clicks'
        QTest.mouseClick(chip.analysis, Qt.MouseButton.LeftButton)
        assert analysis == ['solo']
        QTest.mouseClick(chip.operation, Qt.MouseButton.LeftButton)
        assert palette.operation('solo') == 'remove'
        assert chip.analysis.isHidden()
        QTest.mouseClick(chip.active, Qt.MouseButton.LeftButton)
        assert not chip.active.isChecked()
        assert len(changed) == 2
        trace.context = 'explicit context menu request'
        menus = []

        def close_menu():
            menu = app.activePopupWidget()
            if isinstance(menu, QMenu):
                menus.append([action.text() for action in menu.actions()])
                trace.intended.append(menu)
                menu.close()

        QTimer.singleShot(25, close_menu)
        position = chip.label.rect().center()
        QApplication.sendEvent(chip.label, QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, position, chip.label.mapToGlobal(position)))
        assert menus and menus[0]
        # Only the explicitly requested menu is allowed in this context.
        unexpected = [event for event in trace.unexpected_windows()
                      if not (event['context'] == 'explicit context menu request'
                              and event['qt_class'] == 'QMenu')]
        assert not unexpected, unexpected
        QTest.mouseClick(chip.remove, Qt.MouseButton.LeftButton)
        assert palette.tags() == []
        assert len(changed) == 3
    finally:
        page.bulk_palette.completer.popup().hide()
        page.close()
        page.deleteLater()
        settle(app)
