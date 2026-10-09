"""Reproduce evicted batch sources, empty source loops and late Qt callbacks."""

import io
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication

from booruflow.domain.image_analysis import AnalysisState, InputKind, ModelIdentity
from booruflow.infrastructure.image_analysis_cache import ImageCacheMissError
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import ImageSourceService, NormalizedPost
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.wd14 import WD14Result, WD14Tag
from booruflow.presentation.pyside6.image_analysis_controller import ImageAnalysisController
from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
from booruflow.worker import image_analysis as worker_module
from booruflow.worker.image_analysis import ImageAnalysisWorker, ItemHeartbeat


class SyntheticWD14:
    identity = ModelIdentity("wd14", "synthetic", "1", "config", "cpu")
    provider = "CPUExecutionProvider"

    def analyze(self, path):
        with Image.open(path) as image:
            value = image.getpixel((0, 0))[0]
        return WD14Result((WD14Tag(f"color_{value}", "general", .8),))

    def analyze_batch(self, paths, *, batch_size=None):
        return [self.analyze(path) for path in paths]


def png(value):
    stream = io.BytesIO()
    Image.new("RGB", (8, 6), (value, 20, 30)).save(stream, "PNG")
    return stream.getvalue()


def remote(sources, post_id, value):
    sources.bytes_fetcher = lambda *_: png(value)
    provider = Mock()
    provider.fetch_post.return_value = NormalizedPost(
        "gelbooru", str(post_id), "https://synthetic.invalid/image.png", (), ()
    )
    return sources.add_post(provider, str(post_id))


@pytest.fixture
def batch(tmp_path):
    with (
        ImageAnalysisRepository(tmp_path / "state.sqlite") as repository,
        ImageSourceService(repository, tmp_path / "images") as sources,
    ):
        worker = ImageAnalysisWorker(
            repository, [SyntheticWD14()], wd14_batch_size="4",
            analysis_prefetch=200, cache_directory=tmp_path / "images",
        )
        try:
            yield repository, sources, worker
        finally:
            worker.close()


@pytest.mark.parametrize("missing_index", [0, 1, 2])
@pytest.mark.parametrize("missing_kind", ["evicted", "corrupt"])
def test_missing_blob_recovers_once_then_fails_only_its_claim(batch, capsys, missing_index, missing_kind):
    repository, sources, worker = batch
    ids = [remote(sources, 100 + i, 100 + i) for i in range(3)]
    bad_id = ids[missing_index]
    key = repository.get_item(bad_id).content_sha256
    if missing_kind == "evicted":
        # Simulate external deletion: normal LRU now protects requested jobs.
        sources.cache.delete(key)
    else:
        with sources.cache.connection:
            sources.cache.connection.execute(
                "UPDATE image_cache SET data=zeroblob(byte_size) WHERE cache_key=?", (key,)
            )
    assert worker.process_batch()
    assert repository.get_item(bad_id).state is AnalysisState.PENDING
    assert repository.connection.execute("SELECT source_state FROM analysis_items WHERE id=?", (bad_id,)).fetchone()[0] == "unresolved"
    assert worker.completed_since_summary == 2
    assert worker.failed_since_summary == 0
    assert not worker.process_batch()  # Unresolved is handled by source preparation, never the worker loop.
    sources.bytes_fetcher = lambda *_: png(100 + missing_index)
    provider = Mock()
    provider.fetch_post.return_value = NormalizedPost(
        "gelbooru", str(100 + missing_index), "https://synthetic.invalid/image.png", (), ())
    sources.resolve_post(bad_id, provider, str(100 + missing_index))
    sources.cache.delete(key)
    assert worker.process_batch()  # A second loss is terminal, even across worker turns.
    for i, item_id in enumerate(ids):
        assert repository.get_item(item_id).state is (
            AnalysisState.FAILED if item_id == bad_id else AnalysisState.READY_FOR_REVIEW
        )
        if item_id != bad_id:
            assert repository.observations(item_id)[0][1].name == f"color_{100 + i}"
    assert worker.last_processed_count == 1
    assert worker.failed_since_summary == 1
    assert worker.completed_since_summary == 2
    for _ in range(5):
        assert repository.recover_interrupted() == 0
        assert not worker.process_batch()
    assert repository.connection.execute(
        "SELECT attempt_count FROM analysis_items WHERE id=?", (bad_id,)
    ).fetchone()[0] == 2
    output = capsys.readouterr().out
    for detail in ("ImageCacheMissError", "Traceback (most recent call last)",
                   f"item:{bad_id}", f"cache_key={key}", "post=gelbooru:",
                   "phase=source_materialization", "batch=", "automatic_retry=none"):
        assert detail in output
    assert "materialize" in output
    assert ImageCacheMissError.__bases__ == (KeyError,)


def test_inference_fallback_is_bounded_and_result_ownership_survives_failure(batch, monkeypatch):
    repository, sources, worker = batch
    ids = [remote(sources, 200 + i, 200 + i) for i in range(3)]
    backend = worker.backends[0]
    original = backend.analyze
    calls = []

    def per_image(path):
        with Image.open(path) as image:
            value = image.getpixel((0, 0))[0]
        calls.append(value)
        if value == 201:
            raise ValueError("synthetic bad image")
        return original(path)

    monkeypatch.setattr(backend, "analyze_batch", Mock(side_effect=ValueError("synthetic batch failure")))
    monkeypatch.setattr(backend, "analyze", per_image)
    assert worker.process_batch()
    assert calls == [200, 201, 202]
    assert repository.get_item(ids[1]).state is AnalysisState.FAILED
    assert repository.observations(ids[0])[0][1].name == "color_200"
    assert repository.observations(ids[2])[0][1].name == "color_202"
    assert not worker.process_batch()
    assert backend.analyze_batch.call_count == 1
    assert not repository.connection.execute("SELECT 1 FROM model_runs WHERE state='running'").fetchone()


def test_short_batch_results_fall_back_without_shifting_items(batch, monkeypatch):
    repository, sources, worker = batch
    ids = [remote(sources, 230 + i, 230 + i) for i in range(3)]
    monkeypatch.setattr(worker.backends[0], "analyze_batch", lambda *a, **k: [])
    assert worker.process_batch()
    assert [repository.observations(item_id)[0][1].name for item_id in ids] == [
        "color_230", "color_231", "color_232"
    ]


def test_batch_claims_are_heartbeaten_until_finished(batch):
    repository, sources, _ = batch
    ids = [remote(sources, i, i) for i in range(2)]
    for _ in ids:
        repository.claim_next(None)
    with repository.connection:
        repository.connection.execute(
            "UPDATE analysis_items SET processing_heartbeat_at='2000-01-01T00:00:00+00:00'"
        )
    before = repository.connection.execute(
        "SELECT processing_heartbeat_at FROM analysis_items WHERE id=?", (ids[1],)
    ).fetchone()[0]
    with ItemHeartbeat(repository.path, ids, .1):
        repository.transition(ids[0], AnalysisState.FAILED, "synthetic")
        # A finished member must not stop the heartbeat of its still-active neighbour.
        threading.Event().wait(.25)
        after = repository.connection.execute(
            "SELECT processing_heartbeat_at FROM analysis_items WHERE id=?", (ids[1],)
        ).fetchone()[0]
        assert after > before
        assert repository.recover_interrupted(after) == 0
    assert repository.get_item(ids[1]).state is AnalysisState.PROCESSING


@pytest.fixture
def analysis(tmp_path):
    app = QApplication.instance() or QApplication([])
    catalog = LanguageCatalog(Path(__file__).resolve().parents[2] / "resources/i18n")
    page = ImageAnalysisPage(catalog)
    controller = ImageAnalysisController(
        tmp_path, sys.executable, page, {}, dict, lambda _: None, auto_start_worker=False
    )
    controller.timer.stop()
    try:
        yield app, controller, page
    finally:
        assert controller.shutdown() is not False
        page.close()
        app.processEvents()


def test_suppressed_unresolved_post_cannot_launch_empty_preparation_loop(analysis, monkeypatch):
    _, controller, _ = analysis
    item_id = controller.repository.add_unresolved_remote(InputKind.GELBOORU_POST, "99")
    controller.repository.suppress_analysis_request(item_id)
    assert controller.repository.queue_counts()["unresolved"] == 1
    factory = Mock()
    monkeypatch.setattr(sys.modules[controller.__class__.__module__], "SourcePreparationWorker", factory)
    controller._start_source_preparation()
    for _ in range(5):
        controller._sources_finished([], [], {}, [], [])
    factory.assert_not_called()
    assert not controller.repository.has_sources_to_resolve(10)
    controller.repository.request_analysis(item_id)
    assert controller.repository.has_sources_to_resolve(10)
    assert not controller.repository.has_sources_to_resolve(0)
    controller.repository.claim_sources_to_resolve(1)
    assert not controller.repository.has_sources_to_resolve(1)


def test_successful_preview_remains_without_decode_or_cache_access_after_eviction(analysis, monkeypatch):
    _, controller, page = analysis
    item_id = remote(controller.sources, 45, 45)
    controller.open_item(item_id)
    assert not page.image._source.isNull()
    preview = page.image._source.cacheKey()
    controller.sources.cache.delete(controller.current.content_sha256)
    monkeypatch.setattr(controller.sources, "cached_bytes", Mock(side_effect=AssertionError("preview reread")))
    monkeypatch.setattr(page.image, "set_image_bytes", Mock(side_effect=AssertionError("preview decode")))
    monkeypatch.setattr(page, "clear_preview", Mock(side_effect=AssertionError("preview cleared")))
    controller.repository.claim_next(None)
    controller.repository.transition(item_id, AnalysisState.FAILED, "analysis source evicted")
    controller.refresh()
    controller._show_current()
    controller._sources_finished([], [], {}, [], [])
    assert page.image._source.cacheKey() == preview
    assert controller.current.state is AnalysisState.FAILED


def test_shutdown_defers_close_for_inflight_source_and_ignores_late_callbacks(analysis, monkeypatch):
    app, controller, _ = analysis
    first = controller.repository.add_unresolved_remote(InputKind.GELBOORU_POST, "81")
    second = controller.repository.add_unresolved_remote(InputKind.GELBOORU_POST, "82")
    entered, release = threading.Event(), threading.Event()

    def delayed(sources, item_id, _provider, _post_id):
        entered.set()
        assert release.wait(5)
        sources.repository.fail_source(item_id, "synthetic completion during shutdown")

    monkeypatch.setattr(ImageSourceService, "resolve_post", delayed)
    controller._start_source_preparation()
    worker = controller.source_worker
    assert entered.wait(2)
    monkeypatch.setattr(worker, "wait", lambda _timeout: False)
    close = Mock(wraps=controller.repository.close)
    monkeypatch.setattr(controller.repository, "close", close)
    try:
        assert controller.shutdown() is False
        close.assert_not_called()
        # Even while the repository is still open, closing callbacks must not query it.
        counts = Mock(side_effect=AssertionError("late queue counts"))
        refresh = Mock(side_effect=AssertionError("late refresh"))
        monkeypatch.setattr(controller.repository, "queue_counts", counts)
        monkeypatch.setattr(controller, "refresh", refresh)
        controller._sources_finished([], [], {}, [], [])
        release.set()
        assert QThread.wait(worker, 5000)
        assert controller.repository.get_item(first).state is AnalysisState.FAILED
        row = controller.repository.connection.execute(
            "SELECT source_resolution_started_at FROM analysis_items WHERE id=?", (second,)
        ).fetchone()
        assert row[0] is None
        assert controller.shutdown() is True
        QTimer.singleShot(0, lambda: controller._sources_finished([], [], {}, [], []))
        app.processEvents()
        controller._drop_scan_finished([], 0)
        controller._legacy_cache_migration_completed({})
        controller._legacy_cache_migration_failed("late")
        controller._run_exclusive_database_operation()
        controller._start_source_preparation()
        assert controller.add_remote_ids("gelbooru", ["999"]) == []
        assert controller.shutdown() is True
        close.assert_called_once()
        counts.assert_not_called()
        refresh.assert_not_called()
    finally:
        release.set()
        QThread.wait(worker, 5000)


def test_recycle_keeps_pending_sources_and_item_results_at_exact_100_boundary(tmp_path):
    database = tmp_path / "state.sqlite"
    cache = tmp_path / "images"
    with ImageAnalysisRepository(database) as repository:
        with ImageSourceService(repository, cache) as sources:
            ids = [remote(sources, i, i) for i in range(105)]
        worker = ImageAnalysisWorker(repository, [SyntheticWD14()],
                                     analysis_prefetch=200, wd14_batch_size="16", cache_directory=cache)
        processed = 0
        try:
            while processed < 100:
                assert worker.process_batch(max_items=100 - processed)
                processed += worker.last_processed_count
            assert processed == 100
            assert repository.get_item(ids[100]).state is AnalysisState.PENDING
            assert not repository.connection.execute("SELECT 1 FROM analysis_items WHERE state='processing'").fetchone()
        finally:
            worker.close()
    # New worker / new repository / new cache connection, as after exit code 75.
    with ImageAnalysisRepository(database) as repository:
        worker = ImageAnalysisWorker(repository, [SyntheticWD14()],
                                     analysis_prefetch=200, wd14_batch_size="16", cache_directory=cache)
        try:
            assert repository.recover_interrupted() == 0
            assert worker.process_batch()
            assert worker.last_processed_count == 5
            assert not worker.process_batch()
            for i, item_id in enumerate(ids):
                item = repository.get_item(item_id)
                assert item.source.post_id == str(i)
                assert item.state is AnalysisState.READY_FOR_REVIEW
                assert repository.observations(item_id)[0][1].name == f"color_{i}"
        finally:
            worker.close()


def test_worker_main_recycles_after_100_items_and_controller_restarts_once(analysis, tmp_path, monkeypatch, capsys):
    database = tmp_path / "process-state.sqlite"
    cache = tmp_path / "images"
    with (
        ImageAnalysisRepository(database) as repository,
        ImageSourceService(repository, cache) as sources,
    ):
        ids = [remote(sources, i, i) for i in range(105)]

    class Backend(SyntheticWD14):
        device = "CPU"
        runtime = "synthetic"
        runtime_diagnostic = SimpleNamespace(
            runtime_version="synthetic", expected_cuda="", expected_cudnn="",
            cuda_runtime_installed=False, cudnn_installed=False,
            gpu_devices=(), effective_provider="CPUExecutionProvider",
        )

        def __init__(self, _config):
            pass

        def prepare(self, _trace):
            pass

        def close(self):
            pass

    monkeypatch.setattr(worker_module, "WD14Backend", Backend)
    monkeypatch.setattr(worker_module, "analysis_cache_directory_for_repository", lambda _: cache)
    monkeypatch.setattr(worker_module, "_watch_stdin", lambda _: None)
    result = worker_module.main([
        "--database", str(database), "--wd14-enabled", "--wd14-model-directory", str(tmp_path),
        "--analysis-prefetch", "200", "--wd14-batch-size", "16", "--worker-recycle-after", "100",
    ], parent_watchdog_started=True)
    assert result == 75
    assert "RECYCLE 100" in capsys.readouterr().out
    with ImageAnalysisRepository(database) as repository:
        assert repository.get_item(ids[99]).state is AnalysisState.READY_FOR_REVIEW
        assert repository.get_item(ids[100]).state is AnalysisState.PENDING
        assert not repository.connection.execute("SELECT 1 FROM worker_sessions WHERE state='running'").fetchone()
    app, controller, _ = analysis
    source_factory = Mock()
    monkeypatch.setattr(sys.modules[controller.__class__.__module__], "SourcePreparationWorker", source_factory)
    start = Mock()
    monkeypatch.setattr(controller, "start_worker", start)
    controller._worker_finished(75, None)
    app.processEvents()
    start.assert_called_once()
    source_factory.assert_not_called()


def test_queued_sources_result_after_close_never_queries_repository(analysis, monkeypatch):
    app, controller, _ = analysis
    controller.shutdown()
    queries = Mock(wraps=controller.repository.queue_counts)
    monkeypatch.setattr(controller.repository, "queue_counts", queries)
    refreshed = Mock()
    monkeypatch.setattr(controller, "refresh", refreshed)
    notifications = []
    controller.sources_changed.connect(lambda: notifications.append(True))
    controller._sources_finished([], [], {}, [], [])
    QTimer.singleShot(0, lambda: controller._sources_finished([], [], {}, [], []))
    app.processEvents()
    queries.assert_not_called()
    refreshed.assert_not_called()
    assert not notifications


def test_cached_model_result_does_not_shift_following_batch_results(batch):
    repository, sources, worker = batch
    ids = [remote(sources, 250 + i, 250 + i) for i in range(3)]
    identity = worker.backends[0].identity
    run = repository.begin_model_run(ids[0], identity.backend, identity.name,
                                     identity.version, identity.configuration_hash)
    repository.save_tag_predictions(ids[0], run, [("cached_tag", "general", .9)])
    repository.complete_model_run(run)
    assert worker.process_batch()
    assert repository.observations(ids[0])[0][1].name == "cached_tag"
    assert repository.observations(ids[1])[0][1].name == "color_251"
    assert repository.observations(ids[2])[0][1].name == "color_252"
