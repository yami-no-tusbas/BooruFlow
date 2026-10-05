from __future__ import annotations

import io
import json
import os
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

import booruflow.application.targeted_wd14 as targeted_wd14_module
from booruflow.application.targeted_wd14 import (
    PersistentWD14Session,
    TargetedWD14Analyzer,
    TargetedWD14Progress,
    TargetedWD14Result,
    confidence_bucket,
    resolve_wd14_target,
)
from booruflow.domain.image_analysis import (
    AnalysisItem,
    InputKind,
    ModelIdentity,
    ObservationSource,
    SourceReference,
    SourceTag,
)
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import ImageSourceService, NormalizedPost
from booruflow.infrastructure.wd14 import WD14Config, WD14Result, WD14Tag, wd14_config_identity


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (1.0, "90-100"), (0.95, "90-100"), (0.90, "90-100"),
        (0.899, "80-89"), (0.80, "80-89"), (0.799, "70-79"),
        (0.10, "10-19"), (0.099, "under-10"),
    ],
)
def test_confidence_bucket_boundaries(score: float, expected: str) -> None:
    assert confidence_bucket(score) == expected


def test_persistent_wd14_session_reuses_and_recreates_only_for_incompatible_config(
    tmp_path: Path,
) -> None:
    identity = ModelIdentity("wd14", "model", "version", "config", "cpu")
    created = []

    class FakeBackend:
        tags = (("1girl", "general"), ("1boy", "general"))
        def __init__(self, config) -> None:
            self.config = config
            self.identity = identity
            self.closed = False
            created.append(self)

        def prepare(self) -> None:
            return

        def close(self) -> None:
            self.closed = True

    session = PersistentWD14Session(FakeBackend)
    first = session.acquire(WD14Config(tmp_path / "a", "model"))
    assert session.acquire(WD14Config(tmp_path / "a", "model")) is first
    second = session.acquire(WD14Config(tmp_path / "b", "model"))
    assert second is not first
    assert first.closed
    assert len(created) == 2
    session.close()
    assert second.closed


def test_resolve_wd14_target_rejects_unknown_tag(tmp_path: Path) -> None:
    (tmp_path / "selected_tags.csv").write_text(
        "name,category\n1girl,0\nsolo,0\n", encoding="utf-8"
    )
    assert resolve_wd14_target("1girl", tmp_path) == ("1girl",)
    assert resolve_wd14_target("unknown_tag", tmp_path) == ()


def test_targeted_analyzer_reuses_compatible_vector_and_reanalyzes_old_version(
    tmp_path: Path,
) -> None:
    database = tmp_path / "analysis.sqlite"
    identity = ModelIdentity("wd14", "model", "new-version", "config", "cpu")

    class FakeBackend:
        tags = (("1girl", "general"), ("1boy", "general"))
        analyzed = 0

        def __init__(self, _config) -> None:
            self.identity = identity
            self.runtime = "test"; self.device = "cpu"

        def prepare(self) -> None:
            return

        def analyze(self, _path: Path) -> WD14Result:
            type(self).analyzed += 1
            return WD14Result((WD14Tag("1girl", "general", 0.81),))

        def close(self) -> None:
            return

    with ImageAnalysisRepository(database) as repository:
        item_ids = {}
        for post_id in ("1", "2"):
            image = tmp_path / f"{post_id}.jpg"; image.write_bytes(b"image")
            item_ids[post_id] = repository.add_item(
                AnalysisItem(
                    SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id=post_id),
                    cached_path=image, content_sha256=post_id * 64,
                ),
                request_analysis=False,
            )
        current = repository.begin_model_run(
            item_ids["1"], "wd14", "model", "new-version", "config"
        )
        repository.save_wd14_score_vector(current, {"1girl": 0.97}, labels=("1girl", "1boy"))
        repository.complete_model_run(current)
        old = repository.begin_model_run(
            item_ids["2"], "wd14", "model", "old-version", "config"
        )
        repository.save_wd14_score_vector(old, {"1girl": 0.99}, labels=("1girl", "1boy"))
        repository.complete_model_run(old)

    progress = []
    analyzer = TargetedWD14Analyzer(
        database, tmp_path / "cache", tmp_path, "model", None, {}, None,
        backend_factory=FakeBackend,
    )
    result = analyzer.analyze(
        "1girl", ("1girl",), [{"id": 1}, {"id": 2}], progress.append
    )

    assert result.scores == pytest.approx({1: 0.97, 2: 0.81})
    assert FakeBackend.analyzed == 1
    assert result.progress.reused == 1
    assert result.progress.analyzed == 1
    assert result.progress.failed == 0
    assert result.progress.reused + result.progress.analyzed + result.progress.failed == 2
    assert progress[-1].completed == 2


def test_multi_tag_analysis_uses_one_inference_and_returns_combined_inputs(tmp_path: Path) -> None:
    database = tmp_path / "analysis.sqlite"
    identity = ModelIdentity("wd14", "model", "version", "config", "cpu")
    calls = []

    class FakeBackend:
        tags = (("1girl", "general"), ("1boy", "general"))
        provider = "CPUExecutionProvider"
        runtime = "test"
        device = "cpu"

        def __init__(self, _config) -> None:
            self.identity = identity

        def prepare(self) -> None:
            return

        def analyze_batch(self, paths, **_kwargs):
            calls.append(tuple(paths))
            return tuple(
                WD14Result((
                    WD14Tag("1girl", "general", 0.90),
                    WD14Tag("1boy", "general", 0.80),
                )) for _path in paths
            )

        def close(self) -> None:
            return

    image = tmp_path / "image.jpg"; image.write_bytes(b"image")
    with ImageAnalysisRepository(database) as repository:
        repository.add_item(
            AnalysisItem(
                SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="1"),
                cached_path=image, content_sha256="1" * 64,
            ), request_analysis=False,
        )
    analyzer = TargetedWD14Analyzer(
        database, tmp_path / "cache", tmp_path, "model", None, {}, None,
        backend_factory=FakeBackend, batch_size="1",
    )
    result = analyzer.analyze_many(
        (("1girl", ("1girl",)), ("1boy", ("1boy",))), [{"id": 1}]
    )
    assert len(calls) == 1
    assert result.scores_by_tag == {"1girl": {1: 0.9}, "1boy": {1: 0.8}}
    assert result.scores == {1: 0.9}
    assert result.progress.analyzed == 1
    calls.clear()
    cached = analyzer.analyze_many(
        (("1girl", ("1girl",)), ("1boy", ("1boy",))), [{"id": 1}]
    )
    assert calls == []
    assert cached.progress.reused == 1


def test_pending_preparation_streams_first_batch_before_all_items_are_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "analysis.sqlite"
    identity = ModelIdentity("wd14", "model", "version", "config", "cpu")
    posts = [{"id": index} for index in range(1, 9)]
    images = {}
    with ImageAnalysisRepository(database) as repository:
        for post in posts:
            image = tmp_path / f"{post['id']}.jpg"
            image.write_bytes(b"image")
            images[post["id"]] = image
            repository.add_item(
                AnalysisItem(
                    SourceReference(
                        InputKind.GELBOORU_POST, site="gelbooru", post_id=str(post["id"])
                    ),
                    cached_path=image, content_sha256=str(post["id"]) * 64,
                ), request_analysis=False,
            )

    class DelayedRepository(ImageAnalysisRepository):
        def item_by_remote_source(self, site, remote_id):
            if threading.current_thread().name == "wd14-pending":
                time.sleep(0.02)
            return super().item_by_remote_source(site, remote_id)

    monkeypatch.setattr(targeted_wd14_module, "ImageAnalysisRepository", DelayedRepository)
    events = []

    class FakeBackend:
        tags = (("1girl", "general"), ("1boy", "general"))
        provider = "CPUExecutionProvider"
        runtime = "test"
        device = "cpu"

        def __init__(self, _config) -> None:
            self.identity = identity

        def prepare(self) -> None:
            return

        def analyze_batch(self, paths, **_kwargs):
            events.append(("batch", len(paths)))
            return tuple(WD14Result((WD14Tag("1girl", "general", 0.8),)) for _path in paths)

        def close(self) -> None:
            return

    def diagnostic(message: str) -> None:
        events.append(("diagnostic", message))

    result = TargetedWD14Analyzer(
        database, tmp_path / "cache", tmp_path, "model", None, {}, None,
        backend_factory=FakeBackend, batch_size="4", diagnostic_callback=diagnostic,
    ).analyze("1girl", ("1girl",), posts)

    first_batch = next(index for index, event in enumerate(events) if event[0] == "batch")
    all_ready = next(
        index for index, event in enumerate(events)
        if event[0] == "diagnostic" and "preparation completed" in event[1]
    )
    assert first_batch < all_ready
    assert events[first_batch] == ("batch", 4)
    assert result.progress.analyzed == 8


def test_pending_acquisition_uses_bounded_workers_and_serial_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "analysis.sqlite"
    identity = ModelIdentity("wd14", "model", "version", "config", "cpu")
    posts = [{"id": index} for index in range(1, 9)]
    active_acquisitions = 0
    maximum_acquisitions = 0
    active_inference = 0
    maximum_inference = 0
    backend_instances = 0
    counters_lock = threading.Lock()

    def fake_add_post(self, _provider, _raw_post, post_id, *, request_analysis=True, trace=None):
        nonlocal active_acquisitions, maximum_acquisitions
        with counters_lock:
            active_acquisitions += 1
            maximum_acquisitions = max(maximum_acquisitions, active_acquisitions)
        try:
            time.sleep(0.01 if int(post_id) % 2 == 0 else 0.04)
            if str(post_id) == "3":
                raise RuntimeError("synthetic acquisition failure")
            image = tmp_path / f"acquired-{post_id}.jpg"
            image.write_bytes(b"image")
            item_id = self.repository.add_item(
                AnalysisItem(
                    SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id=str(post_id)),
                    cached_path=image, content_sha256=str(post_id) * 64,
                ),
                request_analysis=request_analysis,
            )
            if trace:
                trace("metadata_fetch_ms", 1.0)
                trace("download_bytes_ms", 2.0)
            return item_id
        finally:
            with counters_lock:
                active_acquisitions -= 1

    monkeypatch.setattr(targeted_wd14_module.ImageSourceService, "add_post_data", fake_add_post)
    events = []

    class FakeBackend:
        tags = (("1girl", "general"), ("1boy", "general"))
        provider = "CUDAExecutionProvider"
        runtime = "test"
        device = "cuda"

        def __init__(self, _config) -> None:
            nonlocal backend_instances
            backend_instances += 1
            self.identity = identity

        def prepare(self) -> None:
            return

        def analyze_batch(self, paths, **kwargs):
            nonlocal active_inference, maximum_inference
            with counters_lock:
                active_inference += 1
                maximum_inference = max(maximum_inference, active_inference)
            try:
                time.sleep(0.01)
                events.append((
                    "batch", tuple(path.name for path in paths), kwargs["batch_size"]
                ))
                return tuple(WD14Result((WD14Tag("1girl", "general", 0.8),)) for _path in paths)
            finally:
                with counters_lock:
                    active_inference -= 1

        def close(self) -> None:
            return

    def diagnostic(message: str) -> None:
        events.append(("diagnostic", message))

    result = TargetedWD14Analyzer(
        database, tmp_path / "cache", tmp_path, "model", None, {}, None,
        backend_factory=FakeBackend, batch_size="auto", diagnostic_callback=diagnostic,
    ).analyze("1girl", ("1girl",), posts)

    worker_log = next(event[1] for event in events if event[0] == "diagnostic" and "worker count" in event[1])
    assert "worker count=2" in worker_log
    assert "queue_capacity=2" in worker_log
    assert maximum_acquisitions == 2
    assert backend_instances == 1
    assert maximum_inference == 1
    assert result.failed_post_ids == frozenset({3})
    assert result.progress.analyzed == 7
    batches = [event for event in events if event[0] == "batch"]
    assert len(batches) == 2
    assert len(batches[0][1]) == 4
    assert all(event[2] == 4 for event in batches)
    assert list(result.scores) == [1, 2, 4, 5, 6, 7, 8]
    first_batch_log = next(
        event[1] for event in events
        if event[0] == "diagnostic" and "first batch started" in event[1]
    )
    assert "size=4" in first_batch_log
    assert not [thread for thread in threading.enumerate() if thread.name.startswith("wd14-acquire")]


def test_transient_acquisition_failure_retries_and_recovers_in_same_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "analysis.sqlite"
    identity = ModelIdentity("wd14", "model", "version", "config", "cpu")
    calls = {}
    diagnostics = []

    def fake_add_post_data(self, _provider, _raw_post, post_id, *, request_analysis=False, trace=None):
        calls[post_id] = calls.get(post_id, 0) + 1
        if post_id == "1" and calls[post_id] == 1:
            raise TimeoutError("simulated timeout")
        image = tmp_path / f"{post_id}.jpg"
        image.write_bytes(b"image")
        return self.repository.add_item(
            AnalysisItem(
                SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id=post_id),
                cached_path=image, content_sha256=post_id * 64,
            ), request_analysis=request_analysis,
        )

    monkeypatch.setattr(targeted_wd14_module.ImageSourceService, "add_post_data", fake_add_post_data)

    class FakeBackend:
        tags = (("1girl", "general"), ("1boy", "general"))
        provider = "CPUExecutionProvider"
        runtime = "test"
        device = "cpu"

        def __init__(self, _config) -> None:
            self.identity = identity

        def prepare(self) -> None:
            return

        def analyze_batch(self, paths, **_kwargs):
            return tuple(WD14Result((WD14Tag("1girl", "general", 0.8),)) for _path in paths)

        def close(self) -> None:
            return

    result = TargetedWD14Analyzer(
        database, tmp_path / "cache", tmp_path, "model", None, {}, None,
        backend_factory=FakeBackend, batch_size="4", diagnostic_callback=diagnostics.append,
    ).analyze("1girl", ("1girl",), [{"id": 1}, {"id": 2}])

    assert calls["1"] == 2
    assert result.progress.analyzed == 2
    assert result.progress.failed == 0
    assert result.attempted_retries == 1
    assert result.recovered_after_retry == 1
    assert result.failures == ()
    assert any("retry recovered post_id=1" in message for message in diagnostics)


def test_duplicate_source_tags_do_not_fail_targeted_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "analysis.sqlite"
    image = tmp_path / "duplicate-tags.jpg"
    image.write_bytes(b"image")
    identity = ModelIdentity("wd14", "model", "version", "config", "cpu")

    def add_post_with_duplicate_tags(
        self, _provider, _raw_post, post_id, *, request_analysis=False, trace=None,
    ):
        tags = (
            SourceTag("1girl", ObservationSource.GELBOORU, "general"),
            SourceTag("solo", ObservationSource.GELBOORU, "general"),
            SourceTag("1girl", ObservationSource.GELBOORU, "general"),
        )
        return self.repository.add_item(
            AnalysisItem(
                SourceReference(
                    InputKind.GELBOORU_POST, site="gelbooru", post_id=str(post_id)
                ),
                cached_path=image,
                content_sha256="d" * 64,
            ),
            tags,
            request_analysis=request_analysis,
        )

    monkeypatch.setattr(
        targeted_wd14_module.ImageSourceService,
        "add_post_data",
        add_post_with_duplicate_tags,
    )

    class FakeBackend:
        tags = (("1girl", "general"),)
        provider = "CPUExecutionProvider"
        runtime = "test"
        device = "cpu"

        def __init__(self, _config) -> None:
            self.identity = identity

        def prepare(self) -> None:
            return

        def analyze_batch(self, paths, **_kwargs):
            return tuple(
                WD14Result((WD14Tag("1girl", "general", 0.9),)) for _path in paths
            )

        def close(self) -> None:
            return

    result = TargetedWD14Analyzer(
        database, tmp_path / "cache", tmp_path, "model", None, {}, None,
        backend_factory=FakeBackend,
    ).analyze("1girl", ("1girl",), [{"id": 42}])

    assert result.scores == {42: pytest.approx(0.9)}
    assert result.failed_post_ids == frozenset()
    assert result.failure_summary == {}
    with ImageAnalysisRepository(database) as repository:
        assert [tag.name for tag in repository.source_tags(1)] == ["1girl", "solo"]


def test_terminal_acquisition_failure_is_not_retried_and_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "analysis.sqlite"
    identity = ModelIdentity("wd14", "model", "version", "config", "cpu")
    calls = 0

    def fake_add_post_data(self, _provider, _raw_post, _post_id, *, request_analysis=False, trace=None):
        nonlocal calls
        calls += 1
        raise targeted_wd14_module.ImageSourceError("unsupported media")

    monkeypatch.setattr(targeted_wd14_module.ImageSourceService, "add_post_data", fake_add_post_data)

    class FakeBackend:
        tags = (("1girl", "general"), ("1boy", "general"))
        provider = "CPUExecutionProvider"
        runtime = "test"
        device = "cpu"

        def __init__(self, _config) -> None:
            self.identity = identity

        def prepare(self) -> None:
            return

        def close(self) -> None:
            return

    result = TargetedWD14Analyzer(
        database, tmp_path / "cache", tmp_path, "model", None, {}, None,
        backend_factory=FakeBackend,
    ).analyze("1girl", ("1girl",), [{"id": 1}])

    assert calls == 1
    assert result.progress.analyzed == 0
    assert result.failed_post_ids == frozenset({1})
    assert result.failure_summary == {"ImageSourceError": 1}
    assert result.failures[0].stage == "source_selection"


def test_retryable_acquisition_failure_is_retained_after_three_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "analysis.sqlite"
    identity = ModelIdentity("wd14", "model", "version", "config", "cpu")
    calls = 0

    def always_timeout(self, _provider, _raw_post, _post_id, *, request_analysis=False, trace=None):
        nonlocal calls
        calls += 1
        raise TimeoutError("persistent timeout")

    monkeypatch.setattr(targeted_wd14_module.ImageSourceService, "add_post_data", always_timeout)

    class FakeBackend:
        tags = (("1girl", "general"), ("1boy", "general"))
        provider = "CPUExecutionProvider"
        runtime = "test"
        device = "cpu"

        def __init__(self, _config) -> None:
            self.identity = identity

        def prepare(self) -> None:
            return

        def close(self) -> None:
            return

    result = TargetedWD14Analyzer(
        database, tmp_path / "cache", tmp_path, "model", None, {}, None,
        backend_factory=FakeBackend,
    ).analyze("1girl", ("1girl",), [{"id": 1}])

    assert calls == 3
    assert result.failed_post_ids == frozenset({1})
    assert result.attempted_retries == 2
    assert result.failures[0].attempt == 3


def test_transient_inference_failure_retries_without_concurrent_inference(
    tmp_path: Path,
) -> None:
    database = tmp_path / "analysis.sqlite"
    identity = ModelIdentity("wd14", "model", "version", "config", "cpu")
    image = tmp_path / "image.jpg"
    image.write_bytes(b"image")
    with ImageAnalysisRepository(database) as repository:
        repository.add_item(
            AnalysisItem(
                SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="1"),
                cached_path=image, content_sha256="1" * 64,
            ), request_analysis=False,
        )

    class FakeBackend:
        tags = (("1girl", "general"), ("1boy", "general"))
        provider = "CPUExecutionProvider"
        runtime = "test"
        device = "cpu"
        attempts = 0

        def __init__(self, _config) -> None:
            self.identity = identity

        def prepare(self) -> None:
            return

        def analyze_batch(self, _paths, **_kwargs):
            raise RuntimeError("synthetic batch fallback")

        def analyze(self, _path):
            type(self).attempts += 1
            if type(self).attempts == 1:
                raise TimeoutError("synthetic inference timeout")
            return WD14Result((WD14Tag("1girl", "general", 0.9),))

        def close(self) -> None:
            return

    result = TargetedWD14Analyzer(
        database, tmp_path / "cache", tmp_path, "model", None, {}, None,
        backend_factory=FakeBackend, batch_size="1",
    ).analyze("1girl", ("1girl",), [{"id": 1}])

    assert FakeBackend.attempts == 2
    assert result.progress.analyzed == 1
    assert result.progress.failed == 0
    assert result.attempted_retries == 1
    assert result.recovered_after_retry == 1


def test_fully_cached_target_does_not_load_onnx_model(tmp_path: Path) -> None:
    model_id = "model"
    (tmp_path / "selected_tags.csv").write_text("name,category\n1girl,0\n", encoding="utf-8")
    (tmp_path / "metadata.json").write_text(json.dumps({
        "model_id": model_id, "model_sha256": "a" * 64,
    }), encoding="utf-8")
    identity = wd14_config_identity(
        WD14Config(tmp_path, model_id, 0.0, input_strategy="gelbooru_sample_v1")
    )
    database = tmp_path / "cached.sqlite"
    image = tmp_path / "cached.jpg"; image.write_bytes(b"image")
    with ImageAnalysisRepository(database) as repository:
        item_id = repository.add_item(AnalysisItem(
            SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="7"),
            cached_path=image, content_sha256="7" * 64,
        ), request_analysis=False)
        run_id = repository.begin_model_run(
            item_id, identity.backend, identity.name, identity.version,
            identity.configuration_hash,
        )
        repository.save_wd14_score_vector(run_id, {"1girl": 0.93}, labels=("1girl",))
        repository.complete_model_run(run_id)

    result = TargetedWD14Analyzer(
        database, tmp_path / "cache", tmp_path, model_id, None, {}, None,
    ).analyze("1girl", ("1girl",), [{"id": 7}])
    assert result.scores == pytest.approx({7: 0.93})
    assert result.progress.reused == 1
    assert result.progress.analyzed == 0


@pytest.mark.parametrize("cache_state", ["sparse", "expired", "labels_changed"])
def test_targeted_cache_miss_reanalyzes_full_vector_in_reused_session(tmp_path, cache_state):
    database = tmp_path / "cache.sqlite"
    identity = ModelIdentity("wd14", "model", "version", "config", "cpu")
    labels = ("1girl", "1boy")
    with ImageAnalysisRepository(database) as repository:
        for post_id in ("1", "2"):
            image = tmp_path / f"{post_id}.jpg"
            image.write_bytes(b"image")
            item = repository.add_item(AnalysisItem(
                SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id=post_id),
                cached_path=image, content_sha256=post_id * 64,
            ), request_analysis=False)
            run = repository.begin_model_run(item, "wd14", "model", "version", "config")
            repository.save_wd14_score_vector(
                run, {"1girl": 0.8, "1boy": 0.099 if cache_state == "sparse" else 0.9},
                labels=labels[::-1] if cache_state == "labels_changed" else labels,
            )
            repository.complete_model_run(run)
        if cache_state == "expired":
            repository.connection.execute(
                "UPDATE wd14_score_vectors SET created_at='2000-01-01T00:00:00+00:00'"
            )
            repository.connection.commit()

    class FakeBackend:
        tags = (("1girl", "general"), ("1boy", "general"))
        provider = "CPUExecutionProvider"
        runtime = "test"
        device = "cpu"
        preparations = 0
        calls = 0

        def __init__(self, config):
            assert config.store_threshold == 0.0
            self.identity = identity

        def prepare(self):
            type(self).preparations += 1

        def analyze_batch(self, paths, **_kwargs):
            type(self).calls += len(paths)
            return tuple(WD14Result((
                WD14Tag("1girl", "general", 0.81),
                WD14Tag("1boy", "general", 0.0123456789 if path.stem == "1" else 0.099),
            )) for path in paths)

        def close(self):
            pass

    session = PersistentWD14Session(FakeBackend)
    try:
        analyzer = TargetedWD14Analyzer(
            database, tmp_path / "cache", tmp_path, "model", None, {}, None,
            backend_factory=FakeBackend, session=session,
        )
        requests = (("1boy", ("1boy",)), ("1girl", ("1girl",)))
        posts = [{"id": 2}, {"id": 1}]
        for _restart in range(2):
            result = analyzer.analyze_many(requests, posts)
            assert result.progress.reused == 0
            assert result.progress.analyzed == 2
            assert result.scores == {2: 0.099, 1: 0.0123456789}
            assert list(result.scores) == [2, 1]
            assert all(confidence_bucket(score) == "under-10" for score in result.scores.values())
            assert result.scores_by_tag["1girl"] == {2: 0.81, 1: 0.81}
        assert FakeBackend.preparations == 1
        assert FakeBackend.calls == 4
        # Retained targets still avoid inference on subsequent requests.
        high = analyzer.analyze("1girl", ("1girl",), posts)
        assert high.progress.reused == 2
        assert FakeBackend.calls == 4
        with ImageAnalysisRepository(database) as repository:
            vectors = repository.wd14_vectors_for_remote_posts(
                "gelbooru", ["1", "2"], "model", "version", "config", labels=labels,
            )
            assert all("1boy" not in vector for vector in vectors.values())
    finally:
        session.close()


def test_targeted_groups_keep_selection_and_analyze_only_visible_results(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QSignalSpy
    from PySide6.QtWidgets import QApplication

    from booruflow.infrastructure.localization import LanguageCatalog
    from booruflow.presentation.pyside6.tagging_page import TaggingPage

    app = QApplication.instance() or QApplication([])
    catalog = LanguageCatalog(Path(__file__).resolve().parents[2] / "resources" / "i18n", "en")
    page = TaggingPage(catalog, {})
    page.show_results([
        {"id": 1, "tags": "a", "priority": "low", "tag_count": 1},
        {"id": 2, "tags": "b", "priority": "low", "tag_count": 1},
        {"id": 3, "tags": "c", "priority": "low", "tag_count": 1},
    ])
    page.set_batch_queue_entries([{
        "site": "gelbooru", "post_id": "3", "additions": ["x"], "removals": [],
        "publish_state": "pending_publish",
    }])
    requested = QSignalSpy(page.targeted_wd14_requested)
    status_messages = QSignalSpy(page.page_status.message_changed)
    status_progress = QSignalSpy(page.page_status.progress_changed)
    status_progress_cleared = QSignalSpy(page.page_status.progress_cleared)
    assert not hasattr(page, "wd14_status")
    page.bulk_add.add_tag("1girl")
    assert page.bulk_add.active_tags() == ["1girl"]
    page.bulk_add.set_tag_active("1girl", False)
    assert page.bulk_add.active_tags() == []
    page.bulk_add.set_tag_active("1girl", True)
    page.bulk_add._chips["1girl"].analysis.click()
    assert [post["id"] for post in requested.at(0)[1]] == [1, 2]
    assert page.bulk_add._chips["1girl"].analysis.text() == "⏳"

    result = TargetedWD14Result(
        "1girl", {1: 0.95, 2: 0.82}, frozenset(),
        TargetedWD14Progress(2, 2, 1, 1, 0), 0.5,
    )
    page.show_targeted_wd14_result(result)
    assert status_messages.at(status_messages.count() - 1)[1] == (
        "2 images · 1 cached · 1 new · 0 errors"
    )
    assert status_progress.count() >= 1
    assert status_progress_cleared.count() == 1
    assert [group.title for group in page.result_groups] == [
        "90–100 % — 1", "80–89 % — 1",
    ]
    page.result_groups[0].select_all.click()
    assert page.result_groups[0].select_all.checkState() == Qt.CheckState.Checked
    assert [post["id"] for post in page.selected_posts()] == [1]
    assert "WD14 95 %" in page.result_buttons[1].text()
    page.bulk_add.add_tag("child")
    page.bulk_add._chips["child"].analysis.click()
    assert requested.count() == 2
    page.show_targeted_wd14_result(TargetedWD14Result(
        "child", {1: 0.20, 2: 0.95}, frozenset(),
        TargetedWD14Progress(2, 2, 2, 0, 0), 0.1,
    ))
    assert page.result_groups[0].cards[0].post["id"] == 2
    group_ids = [[card.post["id"] for card in group.cards] for group in page.result_groups]
    page.bulk_add.remove_tag("child"); app.processEvents()
    assert page._confidence_tag == "child"
    assert [[card.post["id"] for card in group.cards] for group in page.result_groups] == group_ids
    assert len(page.result_groups) == 2
    page.close()


def test_targeted_wd14_reads_existing_blob_without_network_or_loose_file(tmp_path: Path) -> None:
    database = tmp_path / "analysis.sqlite"
    cache_directory = tmp_path / "image_analysis"
    payload = io.BytesIO()
    Image.new("RGB", (12, 8), "red").save(payload, format="PNG")
    data = payload.getvalue()
    with ImageAnalysisRepository(database) as repository, ImageSourceService(
        repository, cache_directory,
        bytes_fetcher=lambda _url, _headers: data,
    ) as sources:
        item_id = sources.add_post(
            type("Provider", (), {"fetch_post": lambda _self, post_id: NormalizedPost(
                "gelbooru", post_id, "https://example/image.png", (), ()
            )})(),
            "42",
            request_analysis=False,
        )
        assert repository.get_item(item_id).cached_path is None

    identity = ModelIdentity("wd14", "model", "version", "config", "cpu")
    received = []

    class FakeBackend:
        tags = (("1girl", "general"),)
        provider = "CPUExecutionProvider"
        runtime = "test"
        device = "cpu"

        def __init__(self, _config):
            self.identity = identity

        def prepare(self):
            return

        def analyze_batch(self, sources, **_kwargs):
            received.extend(sources)
            return tuple(
                WD14Result((WD14Tag("1girl", "general", 0.9),)) for _source in sources
            )

        def close(self):
            return

    analyzer = TargetedWD14Analyzer(
        database, cache_directory, tmp_path, "model", None, {}, None,
        backend_factory=FakeBackend, batch_size="1",
    )
    result = analyzer.analyze("1girl", ("1girl",), [{"id": 42}], lambda _value: None)
    assert result.scores == {42: pytest.approx(0.9)}
    assert received == [data]
    assert not cache_directory.exists()
