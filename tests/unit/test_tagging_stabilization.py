"""Real Qt gestures, lazy Batch reads, canonical decisions and concurrent cache pressure."""
# ruff: noqa: F811 - imported pytest fixtures
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest

from booruflow.domain.image_analysis import (
    AnalysisState,
    InputKind,
)
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import (
    ImageCacheCapacityError,
    ImageSourceService,
    NormalizedPost,
)
from booruflow.presentation.pyside6.main_window import MainWindow
from booruflow.runtime import build_identity
from booruflow.worker.image_analysis import ImageAnalysisWorker
from tests.unit.test_curated_profiles import catalogue  # noqa: F401
from tests.unit.test_curated_tagging_ui import curated, find  # noqa: F401
from tests.unit.test_image_analysis_regressions import SyntheticWD14, png, remote
from tests.unit.test_tagging_navigation_prefetch import navigation, wait_for  # noqa: F401


def test_single_click_selects_double_click_opens_without_decision(curated):
    n = curated
    table = n.page.suggestions
    table._expand_curated('group:panties', True)
    row = find(table, 'curated:panties:thong')
    rect = table.visualItemRect(table.item(row, 0))
    opened = []
    n.page.browser_launcher = SimpleNamespace(open=opened.append)
    before = n.analysis.repository.observations(n.item_id)
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert table.currentRow() == row
    assert n.controller._curated_state(n.item_id).pending['panties'] == set()
    QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert opened and 'tags=thong' in opened[-1]
    assert n.analysis.repository.observations(n.item_id) == before
    assert n.controller._curated_state(n.item_id).pending['panties'] == set()


def test_passive_refresh_preserves_focus_cells_preview_and_obsolete_view(curated, monkeypatch):
    n = curated
    n.page.manual_tag.setText('my_entry')
    n.page.manual_tag.setFocus()
    n.app.processEvents()
    first = n.page.suggestions.item(0, 0)
    decode = Mock(side_effect=AssertionError('passive preview decode'))
    monkeypatch.setattr(n.page.preview, 'set_image_bytes', decode)
    for _ in range(6):
        n.controller.refresh_local_review()
        n.app.processEvents()
        assert n.app.focusWidget() is n.page.manual_tag
        assert n.page.suggestions.item(0, 0) is first
    n.page.show_search()
    n.controller.refresh_local_review()
    n.analysis.sources_changed.emit()
    n.app.processEvents()
    assert n.page.mode_stack.currentWidget() is n.page.search_view
    decode.assert_not_called()


def test_curated_accept_then_late_alias_wd14_reject_undo_and_site_isolation(curated):
    n = curated
    repo = n.analysis.repository
    n.controller.select_curated_refinement('panties', 'thong', True)
    n.controller.decide(n.ids['panties'], 'accepted')
    run = repo.begin_model_run(n.item_id, 'wd14', 'later', '1', 'later')
    repo.save_tag_predictions(n.item_id, run, [('old_thong', 'general', .87)],
                             canonicalize=n.controller._curated_canonical)
    n.controller.refresh_local_review()
    rows = [row for row in n.page._all_suggestion_rows if row['tag'] == 'thong']
    assert len(rows) == 1 and rows[0]['decision'] == 'accepted'
    assert rows[0]['wd14_score'] == .87
    n.controller.decide(rows[0]['id'], 'rejected')
    assert all(obs.decision.value == 'rejected' for _, obs in repo.observations(n.item_id)
               if n.controller._curated_canonical(obs.reviewed_name or obs.name) == 'thong')
    n.controller.undo()
    assert repo.canonical_review_decisions(n.item_id, 'gelbooru')['thong'] == 'accepted'
    repo.set_canonical_review_decision(n.item_id, 'e621', 'thong', 'rejected')
    assert repo.canonical_review_decisions(n.item_id, 'gelbooru')['thong'] == 'accepted'
    assert repo.canonical_review_decisions(n.item_id, 'e621')['thong'] == 'rejected'
    other = repo.add_unresolved_remote(InputKind.GELBOORU_POST, '111')
    assert not repo.canonical_review_decisions(other, 'gelbooru')


def test_active_filters_fixed_height_empty_and_horizontal_overflow(navigation):
    n = navigation
    page = n.page
    page.show_search()
    n.app.processEvents()
    y, height = page.group.y(), page.active_filters.height()
    assert page.active_filters_empty.isVisible()
    for i in range(30):
        page.analysis_palette.add_tag(f'filter_{i}')
    n.app.processEvents()
    assert not page.active_filters_empty.isVisible()
    assert page.active_filters.height() == height and page.group.y() == y
    assert page.analysis_palette.chips_scroll.verticalScrollBar().maximum() == 0
    wait_for(n.app, lambda: page.analysis_palette.chips_scroll.horizontalScrollBar().maximum() > 0)
    for tag in page.analysis_palette.tags():
        page.analysis_palette.remove_tag(tag)
    n.app.processEvents()
    assert page.active_filters_empty.isVisible()
    assert page.group.y() == y


def test_entity_reject_reflects_curated_and_late_wd14_and_undo(curated):
    n = curated
    n.controller.decide('entity:thong', 'rejected')
    n.controller.refresh_local_review()
    root = next(row for row in n.page._all_suggestion_rows if row['tag'] == 'panties')
    child = next(row for row in root['curated']['top'] if row['tag'] == 'thong')
    assert child['decision'] == 'rejected'
    repo = n.analysis.repository
    run = repo.begin_model_run(n.item_id, 'wd14', 'late', '1', 'late')
    repo.save_tag_predictions(n.item_id, run, [('thong', 'general', .9)])
    assert dict(repo.observations(n.item_id))[max(dict(repo.observations(n.item_id)))].decision.value == 'rejected'
    n.controller.undo()
    assert repo.canonical_review_decisions(n.item_id, 'gelbooru')['thong'] == 'unreviewed'
    n.controller.select_curated_refinement('panties', 'thong', True)
    n.controller.decide(n.ids['panties'], 'accepted')
    n.controller.decide('entity:thong', 'rejected')
    n.controller.undo()
    assert repo.canonical_review_decisions(n.item_id, 'gelbooru')['thong'] == 'accepted'


def test_batch_large_model_and_slow_read_leave_gui_responsive(navigation, monkeypatch):
    n = navigation
    n.show(500)
    repo = n.analysis.repository
    item = repo.add_unresolved_remote(InputKind.GELBOORU_POST, '1')
    repo.save_review_batch_entry(item, original_tags=['solo'], additions=['new'], removals=[],
                                 reviewed_final_tags=['solo', 'new'])
    entries = repo.list_batch_entries()
    many = [dict(entries[0], item_id=i, post_id=i) for i in range(1, 10001)]
    read_started, release = threading.Event(), threading.Event()
    threads = []

    def slow_read(path, site):
        threads.append(threading.get_ident())
        read_started.set()
        assert release.wait(3)
        return many, set(), 150, 0

    monkeypatch.setattr(ImageAnalysisRepository, 'read_batch_snapshot', slow_read)
    n.page.show_batch_entries(many)
    cards = dict(n.page.result_buttons)
    ticks = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    n.page.show_batch()
    wait_for(n.app, read_started.is_set)
    QTest.qWait(70)
    assert len(ticks) >= 4
    assert n.page.mode_stack.currentWidget() is n.page.batch_view
    assert '10000' in n.page.batch_counts.text().replace(',', '')
    assert threads[0] != threading.get_ident()
    release.set()
    wait_for(n.app, lambda: not n.controller._batch_snapshot_workers)
    assert n.page.batch_table.rowCount() == 10000
    assert n.page.result_buttons == cards
    assert n.page.batch_table.model().data(n.page.batch_table.model().index(9999, 6)) == '10000'
    n.page.batch_table.verticalScrollBar().setValue(50)
    index = n.page.batch_table.model().index(3, 0)
    n.page.show_batch_entries(many)
    assert index.isValid()
    assert n.page.batch_table.verticalScrollBar().value() == 50
    timer.stop()


def test_obsolete_batch_snapshot_does_not_mutate_current_view(navigation):
    n = navigation
    n.controller._batch_snapshot_generation = 9
    n.page.show_search()
    before = n.page.batch_entries
    n.controller._batch_snapshot_ready(9, 'gelbooru', ([], set(), 0, 0))
    assert n.page.batch_entries is before
    assert n.page.mode_stack.currentWidget() is n.page.search_view


def test_bounded_recovery_success_survives_worker_restart(tmp_path, capsys):
    path = tmp_path / 'analysis.sqlite'
    with ImageAnalysisRepository(path) as repo, ImageSourceService(repo, tmp_path / 'images') as sources:
        item = remote(sources, 10, 10)
        sources.cache.delete(repo.get_item(item).content_sha256)
        worker = ImageAnalysisWorker(repo, [SyntheticWD14()], cache_directory=tmp_path / 'images')
        assert worker.process_one()
        worker.close()
        assert repo.get_item(item).state is AnalysisState.PENDING
    with ImageAnalysisRepository(path) as repo, ImageSourceService(repo, tmp_path / 'images') as sources:
        sources.bytes_fetcher = lambda *_: png(10)
        provider = Mock()
        provider.fetch_post.return_value = NormalizedPost('gelbooru', '10', 'https://test.invalid/a.png', (), ())
        sources.resolve_post(item, provider, '10')
        worker = ImageAnalysisWorker(repo, [SyntheticWD14()], cache_directory=tmp_path / 'images')
        try:
            assert worker.process_one()
            assert repo.get_item(item).state is AnalysisState.READY_FOR_REVIEW
            assert not worker.process_one()
            assert repo.connection.execute('SELECT source_recovery_count FROM analysis_items').fetchone()[0] == 1
        finally:
            worker.close()
    assert 'automatic_retry=source_reacquisition_once' in capsys.readouterr().out


def test_cache_pressure_protects_pending_and_processing_until_done(tmp_path):
    with ImageAnalysisRepository(tmp_path / 'analysis.sqlite') as repo, ImageSourceService(repo, tmp_path / 'images') as sources:
        sources.cache.max_bytes = len(png(1)) + len(png(2))
        ids = [remote(sources, i, i) for i in (1, 2)]
        claim = repo.claim_next(None)
        import pytest
        with pytest.raises(ImageCacheCapacityError):
            remote(sources, 3, 3)
        assert all(sources.cache.contains(repo.get_item(i).content_sha256) for i in ids)
        repo.transition(claim.id, AnalysisState.READY_FOR_REVIEW)
        third = remote(sources, 3, 3)
        assert sources.cache.contains(repo.get_item(third).content_sha256)
        assert sources.cache.stats()['logical_bytes'] <= sources.cache.max_bytes
        assert not sources.cache.contains(repo.get_item(claim.id).content_sha256)


def test_build_identity_legacy_explicit_and_renamed_folder(tmp_path):
    info = tmp_path / 'BUILD_INFO.txt'
    info.write_text('Built: 2026-10-07 12:41:18 +02:00\nCommit short: 62e950e\nWorking tree: DIRTY\nRuntime profile: CUDA\n', encoding='utf-8')
    assert build_identity(tmp_path) == '2026.10.07-g62e950e-dirty-1241-gpu'
    info.write_text(info.read_text() + 'Build identity: 2026.10.07-g62e950e-dirty-1241-gpu-2\n', encoding='utf-8')
    assert build_identity(tmp_path).endswith('-gpu-2')


def test_alias_warning_dedup_shared_with_bootstrap_and_new_warning_visible():
    messages = []
    shell = SimpleNamespace(_curated_validation_warnings=set(), log=messages.append)
    for message in ('chain a', 'chain a', 'chain b'):
        MainWindow._warn_curated_once(shell, message)
    assert messages == ['[WARNING] chain a', '[WARNING] chain b']


def test_real_batch_read_snapshot_uses_site_and_persistent_visibility(tmp_path):
    path = tmp_path / 'analysis.sqlite'
    with ImageAnalysisRepository(path) as repo:
        first = repo.add_unresolved_remote(InputKind.GELBOORU_POST, '12')
        second = repo.add_unresolved_remote(InputKind.E621_POST, '12')
        for item in (first, second):
            repo.save_review_batch_entry(item, original_tags=['solo'], additions=['new'],
                                         removals=[], reviewed_final_tags=['solo', 'new'])
        entries, reviewed, phases, _ = ImageAnalysisRepository.read_batch_snapshot(path, 'e621')
        assert {row['item_id'] for row in entries} == {first, second}
        assert reviewed == {12}
        assert set(phases) == {'query_sort_ms', 'decode_ms'}
        repo.remove_batch_entry(second)
        assert len(ImageAnalysisRepository.read_batch_snapshot(path, 'e621')[0]) == 1


def test_main_window_title_and_log_keep_generated_identity_on_language_change(navigation, tmp_path):
    from booruflow.application.capabilities import ApplicationCapabilities
    from booruflow.domain import ToolAvailability
    from booruflow.infrastructure.settings import JsonSettingsRepository
    identity = '2026.10.08-g62e950e-dirty-1136-gpu'
    (tmp_path / 'BUILD_INFO.txt').write_text(f'Build identity: {identity}\n', encoding='utf-8')
    settings = JsonSettingsRepository(tmp_path / 'config/settings.json')
    settings.save({'image_analysis_wd14_enabled': False, 'first_run_wizard_completed': True})
    window = MainWindow(ApplicationCapabilities(ToolAvailability(False, 'test')),
                        navigation.page.catalog, project_root=tmp_path, resource_root=tmp_path,
                        settings_repository=settings, start_image_worker=False)
    try:
        assert window.windowTitle() == f'BooruFlow — {identity}'
        window.change_language('fr')
        assert identity in window.windowTitle()
        assert identity in window.disk_log_path.read_text(encoding='utf-8')
    finally:
        window.close()
        navigation.app.processEvents()


def test_rapid_navigation_with_concurrent_source_prep_and_tiny_cache(navigation, capsys):
    n = navigation
    n.show(500)
    stop = threading.Event()
    errors = []
    ready = threading.Event()
    path = n.analysis.repository.path
    cache = n.analysis.sources.cache_directory

    def prepare_and_analyze():
        try:
            with ImageAnalysisRepository(path) as repo, ImageSourceService(repo, cache) as sources:
                sources.cache.max_bytes = 160
                worker = ImageAnalysisWorker(repo, [SyntheticWD14()], cache_directory=cache)
                provider = Mock()
                ready.set()
                try:
                    while not stop.is_set():
                        for item_id in repo.claim_sources_to_resolve(2):
                            item = repo.get_item(item_id)
                            number = int(item.source.post_id)
                            sources.bytes_fetcher = lambda *_, value=number: png(value % 255)
                            provider.fetch_post.return_value = NormalizedPost('gelbooru', str(number),
                                'https://synthetic.invalid/image.png', (), ())
                            try:
                                sources.resolve_post(item_id, provider, str(number))
                            except ImageCacheCapacityError:
                                repo.release_source_claim(item_id)
                        worker.process_batch()
                        stop.wait(.002)
                finally:
                    worker.close()
        except Exception as exc:  # noqa: BLE001 - propagate failures from the stress thread
            errors.append(exc)

    thread = threading.Thread(target=prepare_and_analyze)
    thread.start()
    assert ready.wait(3)
    try:
        for index in range(40):
            n.open(1 + (index * 13) % 100)
            n.page.next_button.click()
            n.page.previous_button.click()
            n.controller._poll_current()
            n.page.show_search()
            n.analysis.sources_changed.emit()
            n.app.processEvents()
            assert n.page.mode_stack.currentWidget() is n.page.search_view
            n.open(1 + (index * 13) % 100)
            QTest.qWait(5)
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors
    completed = n.analysis.repository.connection.execute(
        "SELECT COUNT(*) FROM model_runs WHERE state='completed'").fetchone()[0]
    assert completed >= 5  # Navigation really overlaps preparation/inference.
    cache_bytes = n.analysis.sources.cache.stats()['logical_bytes']
    assert cache_bytes <= 160
    assert n.analysis.repository.connection.execute(
        'SELECT COUNT(*) FROM analysis_items WHERE source_recovery_count>0').fetchone()[0] == 0
    assert 'ImageCacheMissError' not in capsys.readouterr().out
    capture = os.environ.get('BOORUFLOW_STABILIZATION_CAPTURE_DIR')
    if capture:
        assert n.app.platformName() == 'windows'
        directory = Path(capture)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'stress-results.json').write_text(json.dumps({
            'result_grid': 500, 'navigation_cycles': 40, 'completed_model_runs': completed,
            'cache_capacity_bytes': 160, 'cache_logical_bytes': cache_bytes,
            'source_recovery_count': 0, 'unexpected_cache_misses': 0,
        }, indent=2), encoding='utf-8')
        n.page.show_search()
        n.app.processEvents()
        assert n.page.grab().save(str(directory / 'native-stress-final.png'))
