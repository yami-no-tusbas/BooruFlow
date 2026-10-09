"""Universal wiki sources, context selection and asynchronous session boundaries."""
from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import CancelledError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QProgressBar, QStatusBar

from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.tag_browser import TagRow
from booruflow.infrastructure.wiki_audit import (
    AuditResult,
    GelbooruWikiAuditService,
    WikiStatus,
    parse_audit_reference,
)
from booruflow.infrastructure.wiki_page_cache import WikiPageCache
from booruflow.presentation.pyside6.main_window import MainWindow
from booruflow.presentation.pyside6.status_bar import StatusBarController
from booruflow.presentation.pyside6.tag_browser_page import TagBrowserPage
from booruflow.presentation.pyside6.wiki_audit_page import WikiAuditPage

APP = QApplication.instance() or QApplication([])
ROOT = Path(__file__).parents[2]


def catalog():
    return LanguageCatalog(ROOT / "resources/i18n", "en")


def spin_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        QTest.qWait(5)
    APP.processEvents()
    assert predicate(), "Qt operation did not finish"


@pytest.mark.parametrize(("value", "source"), [
    (" 3453397 ", 3453397),
    ("https://gelbooru.com/index.php?page=post&s=view&id=42", 42),
    ("long_hair", ("long_hair",)),
    ("long_hair,blue_eyes,1girl", ("long_hair", "blue_eyes", "1girl")),
    (" long_hair, \r\n blue_eyes\nlong_hair,, 1girl ", ("long_hair", "blue_eyes", "1girl")),
    ("long_hair,LONG_HAIR,blue_eyes", ("long_hair", "blue_eyes")),
    ("123,456", ("123", "456")),
    ("literal&#039;s, literal&#039;s", ("literal&#039;s",)),
])
def test_reference_modes_keep_order_and_unicode(value, source):
    assert parse_audit_reference(value) == source


@pytest.mark.parametrize("value", ["", "  ", ",,\n,", "https://gelbooru.com/?no_id=1"])
def test_empty_or_invalid_reference_is_rejected(value):
    with pytest.raises(ValueError):
        parse_audit_reference(value)


def make_service(tmp_path, names=("old_tag", "new_tag"), workers=2):
    tags = tmp_path / "tags.db"
    with sqlite3.connect(tags) as db:
        db.execute("CREATE TABLE tags(id INTEGER, name TEXT, post_count INTEGER, category INTEGER)")
        db.executemany("INSERT INTO tags VALUES(?,?,?,?)",
                       [(i, name, len(names)-i, 6 if i == 0 else 0)
                        for i, name in enumerate(names)])
    aliases = tmp_path / "aliases.db"
    with sqlite3.connect(aliases) as db:
        db.execute("CREATE TABLE gelbooru_aliases(source_name TEXT, target_name TEXT, status TEXT)")
        db.execute("INSERT INTO gelbooru_aliases VALUES('old_tag','new_tag','active')")
    dapi = Mock()
    dapi.fetch_post.return_value = {"id": 42, "tags": [*names]}
    return GelbooruWikiAuditService(tags, aliases, WikiPageCache(tmp_path / "cache.db"),
                                   dapi_client=dapi, workers=workers)


def test_direct_and_post_sources_use_the_same_engine_aliases_and_errors(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    calls = []

    def lookup(name, **kwargs):
        calls.append(name)
        return WikiStatus(name, False)

    monkeypatch.setattr(service, "wiki_status", lookup)
    direct = service.audit_tags([" old_tag ", "OLD_TAG", "not_in_database", "new_tag"])
    assert direct.post_id is None and direct.post_url == ""
    service.dapi_client.fetch_post.assert_not_called()
    assert set(calls) == {"old_tag", "not_in_database", "new_tag"}
    by_name = {row.name: row for row in direct.tags}
    assert by_name["old_tag"].canonical_name == "new_tag"
    assert by_name["not_in_database"].category is None
    assert by_name["not_in_database"].wiki.error is None
    post = service.audit_post(42)
    service.dapi_client.fetch_post.assert_called_once_with(42)
    assert post.post_id == 42 and post.post_url.endswith("id=42")
    assert post.tags == tuple(row for row in direct.tags if row.name != "not_in_database")


def test_large_selection_is_batched_and_has_no_post_or_list_persistence(tmp_path, monkeypatch):
    names = [f"tag_{i}" for i in range(2500)]
    service = make_service(tmp_path, names)
    progress = []
    monkeypatch.setattr(service, "wiki_status", lambda name, **kwargs: WikiStatus(name, False))
    result = service.audit_tags([*names, *names], progress=lambda *values: progress.append(values))
    assert len(result.tags) == 2500
    assert progress[-1][:2] == (2500, 2500)
    service.dapi_client.fetch_post.assert_not_called()
    with sqlite3.connect(service.cache.database) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not any("post" in table or "session" in table for table in tables)


def test_cancelled_large_selection_never_queues_the_remaining_requests(tmp_path, monkeypatch):
    service = make_service(tmp_path, [f"tag_{i}" for i in range(1000)], workers=1)
    cancelled = threading.Event()
    calls = []

    def lookup(name, **kwargs):
        calls.append(name)
        cancelled.set()
        return WikiStatus(name, False)

    monkeypatch.setattr(service, "wiki_status", lookup)
    with pytest.raises(CancelledError):
        service.audit_tags([f"tag_{i}" for i in range(1000)], cancelled=cancelled.is_set)
    assert calls == ["tag_0"]


def test_pre_cancelled_post_never_calls_dapi(tmp_path):
    service = make_service(tmp_path)
    with pytest.raises(CancelledError):
        service.audit_post(42, cancelled=lambda: True)
    service.dapi_client.fetch_post.assert_not_called()


class RecordingService:
    def __init__(self):
        self.calls = []
        self.dapi_client = Mock(authenticated=False)

    def audit_tags(self, names, *, force, progress, cancelled):
        self.calls.append((names, force))
        progress(len(names), len(names), names[-1])
        return AuditResult(None, "", ())

    def audit_post(self, post_id, *, force, progress, cancelled):
        self.calls.append((post_id, force))
        progress(1, 1, "post_tag")
        return AuditResult(post_id, "post_url", ())


def page_with_service(tmp_path, monkeypatch, service=None):
    page = WikiAuditPage(catalog(), None, None, tmp_path / "cache.db")
    service = service or RecordingService()
    monkeypatch.setattr(page, "_service", lambda: service)
    return page, service


@pytest.mark.parametrize("text", ["long_hair", "long_hair,blue_eyes", "long_hair\nblue_eyes"])
def test_manual_tags_load_and_refresh_without_a_post(tmp_path, monkeypatch, text):
    page, service = page_with_service(tmp_path, monkeypatch)
    page.reference.setText(text)
    page.load_reference()
    spin_until(lambda: page.worker is None)
    assert service.calls == [(parse_audit_reference(text), False)]
    assert page.result.post_id is None
    page.refresh_button.click()
    spin_until(lambda: page.worker is None)
    assert service.calls[-1] == (parse_audit_reference(text), True)
    page.close()


def test_browser_hint_is_never_parsed_and_manual_input_restores_the_field(tmp_path, monkeypatch):
    page, service = page_with_service(tmp_path, monkeypatch)
    page.load_tags(["long_hair", "blue_eyes", "long_hair"])
    spin_until(lambda: page.worker is None)
    assert page.reference.text() == ""
    assert page.reference.placeholderText() == "From Tag Browser — 2 tags"
    page.load_reference()
    spin_until(lambda: page.worker is None)
    assert service.calls[-1] == (("long_hair", "blue_eyes"), False)
    page.refresh_button.click()
    spin_until(lambda: page.worker is None)
    assert service.calls[-1] == (("long_hair", "blue_eyes"), True)
    page.reference.setText("3453397")
    page.load_reference()
    spin_until(lambda: page.worker is None)
    assert service.calls[-1] == (3453397, False)
    assert "From Tag Browser" not in page.reference.placeholderText()
    page.close()


def test_multiline_clipboard_and_large_input_are_not_truncated(tmp_path, monkeypatch):
    page, _ = page_with_service(tmp_path, monkeypatch)
    text = "\n".join(f"long_tag_name_{i}" for i in range(4000))
    assert len(text) > 32767
    APP.clipboard().setText(text)
    page.reference.paste()
    assert page.reference.text() == text
    assert len(parse_audit_reference(page.reference.text())) == 4000
    APP.clipboard().clear()
    page.close()


def test_random_after_browser_input_retains_the_real_post_path(tmp_path, monkeypatch):
    page, service = page_with_service(tmp_path, monkeypatch)
    page.load_tags(["tag"])
    spin_until(lambda: page.worker is None)
    client = Mock()
    client.random_post.return_value = {"id": 73}
    monkeypatch.setattr(page, "_dapi_client", lambda: client)
    page.load_random()
    spin_until(lambda: page.worker is None and page.result is not None)
    assert page.result.post_id == 73
    assert page.reference.text() == "73"
    assert service.calls == [(("tag",), False), (73, False)]
    client.random_post.assert_called_once()
    page.close()


def test_status_progress_is_global_temporary_and_does_not_affect_other_pages(tmp_path, monkeypatch):
    page, _ = page_with_service(tmp_path, monkeypatch)
    bar = QStatusBar()
    controller = StatusBarController(bar, catalog(), lambda message: None)
    controller._memory_timer.stop()
    controller.bind(page.page_status)
    controller.activate_page("wiki_audit")
    controller.set_progress("tag_browser", 2, 8)
    assert page.findChildren(QProgressBar) == []
    assert page.status.isHidden()
    page._set_busy(True)
    page._progress(22, 29, "mecha_musume")
    assert controller.message_label.text() == "Checking 22/29: mecha_musume"
    assert controller.progress.value() == 22
    assert page.status.text() == ""
    page._set_busy(False)
    assert controller.progress.isHidden()
    assert controller.message_label.text() == ""
    assert "tag_browser" in controller.page_progress
    page.close()
    bar.close()


def test_global_error_remains_visible_until_the_next_session(tmp_path, monkeypatch):
    page, _ = page_with_service(tmp_path, monkeypatch)
    bar = QStatusBar()
    controller = StatusBarController(bar, catalog(), lambda message: None)
    controller._memory_timer.stop()
    controller.bind(page.page_status)
    controller.activate_page("wiki_audit")
    page._failed(RuntimeError("offline"))
    page._set_busy(False)
    assert "offline" in page.status.text() and "offline" in controller.message_label.text()
    page.load_tags(["tag"])
    spin_until(lambda: page.worker is None)
    assert controller.message_label.text() == ""
    page.close()
    bar.close()


def test_new_session_rejects_old_result_progress_error_and_finished(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    class SlowService(RecordingService):
        def audit_tags(self, names, *, force, progress, cancelled):
            if names == ("old",):
                started.set()
                release.wait(3)
                progress(1, 1, "old")
                return AuditResult(999, "old", ())
            return super().audit_tags(names, force=force, progress=progress, cancelled=cancelled)

    page, _ = page_with_service(tmp_path, monkeypatch, SlowService())
    page.load_tags(["old"])
    spin_until(started.is_set)
    old_worker = page.worker
    old_generation = page._scan_generation
    page.load_tags(["new"])
    spin_until(lambda: page.worker is None)
    active = page.result
    assert active.post_id is None
    page._progress(1, 1, "old", old_generation)
    page._failed(RuntimeError("old failure"), old_generation)
    page._completed(AuditResult(999, "", ()), old_generation)
    page._random_ready(999, old_generation)
    assert page.result is active
    release.set()
    spin_until(lambda: old_worker not in page._workers)
    assert page.result is active
    assert page.reference.placeholderText() == "From Tag Browser — 1 tag"
    assert "old" not in page.status.text()
    page.close()


def test_close_during_loading_is_deferred_without_blocking_gui(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    class SlowService(RecordingService):
        def audit_tags(self, names, **kwargs):
            started.set()
            release.wait(3)
            return AuditResult(None, "", ())

    page, _ = page_with_service(tmp_path, monkeypatch, SlowService())
    page.show()
    page.load_tags(["tag"])
    spin_until(started.is_set)
    assert page.close() is False
    assert page._closing and page.worker.isInterruptionRequested()
    assert page.isVisible()
    release.set()
    spin_until(lambda: not page.isVisible())
    assert page.result is None


@pytest.mark.parametrize(("selected", "clicked", "expected"), [
    ([0], 0, ["z_old"]),
    ([0, 2], 2, ["z_old", "a_tag"]),
    ([0, 2], 1, ["middle"]),
])
def test_context_menu_preserves_selection_and_raw_visual_order(
    monkeypatch, selected, clicked, expected,
):
    page = TagBrowserPage(catalog())
    page._show_rows([TagRow(1, "z_old", 30, 6, 0, "new", "canonical"),
                     TagRow(2, "middle", 20, 0, 0), TagRow(3, "a_tag", 10, 0, 0)])
    page.table.sortItems(2, Qt.SortOrder.DescendingOrder)
    page.resize(1000, 600)
    page.show()
    APP.processEvents()
    for row in selected:
        page.table.selectionModel().select(
            page.table.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
    emitted = []
    page.wiki_audit_requested.connect(emitted.append)
    actions = []

    def choose(menu, position, handlers):
        actions.extend(action.text() for action in menu.actions())
        action = next(action for action in menu.actions() if action.text().startswith("Audit wiki"))
        handlers[action]()

    monkeypatch.setattr("booruflow.presentation.pyside6.tag_browser_page.exec_menu", choose)
    point = page.table.visualItemRect(page.table.item(clicked, 1)).center()
    QTest.mouseClick(page.table.viewport(), Qt.MouseButton.RightButton, pos=point)
    if not emitted:
        page._show_context_menu(point)
    assert emitted == [expected]
    assert "Copy tag" in actions
    assert any("entity" in action.lower() for action in actions)
    selected_after = sorted(index.row() for index in page.table.selectionModel().selectedRows())
    assert selected_after == (selected if clicked in selected else [clicked])
    label = "Audit wiki" if len(expected) == 1 else f"Audit wikis ({len(expected)} tags)"
    assert label in actions
    page.close()


def test_shell_transfer_coalesces_requests_until_the_lazy_page_is_ready():
    deferred = []
    page = Mock()
    shell = SimpleNamespace(wiki_audit_page=page, navigate_to_key=Mock(),
                            _run_when_ready=lambda key, callback: deferred.append(callback))
    names = ["first"]
    MainWindow._open_wiki_audit(shell, names)
    names.append("later_mutation")
    MainWindow._open_wiki_audit(shell, ["latest"])
    for callback in deferred:
        callback()
    page.load_tags.assert_called_once_with(("latest",))
    assert shell.navigate_to_key.call_args.args == ("wiki_audit",)


def test_live_shell_signal_navigates_to_existing_lazy_audit_page(tmp_path, monkeypatch):
    from booruflow.application.capabilities import ApplicationCapabilities
    from booruflow.domain import ToolAvailability

    window = MainWindow(ApplicationCapabilities(ToolAvailability(False, "not configured")),
                        catalog(), project_root=tmp_path, resource_root=ROOT,
                        start_image_worker=False)
    service = RecordingService()
    monkeypatch.setattr(WikiAuditPage, "_service", lambda self: service)
    window.navigate_to_key("tag_browser")
    spin_until(lambda: window.tag_browser_page is not None)
    count = window.pages.count()
    window.tag_browser_page.wiki_audit_requested.emit(["long_hair", "blue_eyes"])
    spin_until(lambda: window.wiki_audit_page is not None and window.wiki_audit_page.result is not None)
    assert window.pages.count() == count
    assert window.status_controller.active_page == "wiki_audit"
    assert service.calls == [(("long_hair", "blue_eyes"), False)]
    spin_until(lambda: window.wiki_audit_page.worker is None)
    assert window.global_progress.isHidden()
    assert window.status_message_label.text() == ""
    window.close()


def test_direct_cached_page_force_refresh_and_per_tag_errors(tmp_path, monkeypatch):
    from booruflow.infrastructure.wiki_tag_importer import WikiPageParseError

    service = make_service(tmp_path, ["1girl", "offline", "malformed", "missing"])
    direct = (ROOT / "tests/fixtures/gelbooru_audit_direct.html").read_text(encoding="utf-8")
    calls = []

    def fetch(url):
        calls.append(url)
        return direct, "https://gelbooru.com/index.php?page=wiki&s=view&id=5479"

    service.fetcher = fetch
    first = service.audit_tags(["1girl"])
    second = service.audit_tags(["1girl"])
    assert len(calls) == 1
    assert first.tags[0].wiki.content == second.tags[0].wiki.content
    service.audit_tags(["1girl"], force=True)
    assert len(calls) == 2
    original_lookup = service.wiki_status

    def lookup(name, **kwargs):
        if name == "offline":
            raise OSError("offline")
        if name == "malformed":
            raise WikiPageParseError("malformed")
        return original_lookup(name, **kwargs)

    monkeypatch.setattr(service, "wiki_status", lookup)
    statuses = {row.name: row.wiki for row in service.audit_tags(["offline", "malformed"]).tags}
    assert statuses["offline"].error_kind == "network"
    assert statuses["malformed"].error_kind == "parse"
    assert service.cache.get("gelbooru", "offline") is None
    service.dapi_client.fetch_post.assert_not_called()


def test_large_browser_selection_keeps_all_tags_and_existing_actions(monkeypatch):
    page = TagBrowserPage(catalog())
    names = [f"tag_{index:04}" for index in range(1500)]
    page._show_rows([TagRow(index, name, index, 0, 0) for index, name in enumerate(names)])
    page.table.sortItems(1, Qt.SortOrder.DescendingOrder)
    page.table.selectAll()
    page.show()
    APP.processEvents()
    emitted = []
    page.wiki_audit_requested.connect(emitted.append)

    def choose(menu, position, handlers):
        action = next(action for action in menu.actions() if action.text() == "Audit wikis (1500 tags)")
        handlers[action]()

    monkeypatch.setattr("booruflow.presentation.pyside6.tag_browser_page.exec_menu", choose)
    page._show_context_menu(page.table.visualItemRect(page.table.item(0, 1)).center())
    assert emitted == [names[::-1]]
    assert len(page.table.selectionModel().selectedRows()) == 1500
    page.close()


def test_browser_provenance_retranslates_and_empty_list_keeps_the_session(tmp_path, monkeypatch):
    page, _ = page_with_service(tmp_path, monkeypatch)
    page.load_tags(["one", "two"])
    spin_until(lambda: page.worker is None)
    active = page.result
    page.catalog.set_language("fr")
    page.retranslate()
    assert page.reference.placeholderText() == "Depuis Tag Browser — 2 tags"
    page.load_tags([])
    assert page.result is active
    assert page._source == ("one", "two")
    page.close()
