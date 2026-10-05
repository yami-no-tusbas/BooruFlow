from __future__ import annotations

import hashlib

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from booruflow.domain.image_analysis import AnalysisItem, InputKind, SourceReference
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import ImageSourceService
from booruflow.presentation.pyside6.thumbnail_cache import (
    ThumbnailCacheKey,
    ThumbnailMemoryCache,
)
from booruflow.presentation.pyside6.thumbnail_loader import (
    PersistentThumbnailSources,
    ThumbnailLoader,
    decode_thumbnail_bytes,
)

_APP = QApplication.instance() or QApplication([])


class FakeReply(QObject):
    finished = Signal()

    def __init__(self, *, error=QNetworkReply.NetworkError.NoError, data=b"", status=200):
        super().__init__()
        self._error = error
        self._data = data
        self._status = status
        self.aborted = False

    def error(self):
        return self._error

    def readAll(self):
        return QByteArray(self._data)

    def attribute(self, attribute):
        if attribute == QNetworkRequest.Attribute.HttpStatusCodeAttribute:
            return self._status
        return None

    def rawHeader(self, name):
        if not isinstance(name, str):
            raise TypeError("PySide6 QNetworkReply.rawHeader requires str")
        return b"image/png" if name == "Content-Type" else b""

    def abort(self):
        self.aborted = True
        self._error = QNetworkReply.NetworkError.OperationCanceledError
        self.finished.emit()

    def deleteLater(self):
        pass


class FakeNetwork:
    def __init__(self, replies):
        self.replies = list(replies)
        self.request_count = 0

    def setCache(self, _cache):
        pass

    def get(self, _request):
        reply = self.replies[self.request_count]
        self.request_count += 1
        return reply


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


def png_bytes() -> bytes:
    image = QImage(4, 4, QImage.Format.Format_ARGB32)
    image.fill(0xFF112233)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def key(post_id: int) -> ThumbnailCacheKey:
    return ThumbnailCacheKey("gelbooru", post_id, f"https://example.invalid/{post_id}.jpg")


def wait_until(predicate, timeout_ms: int = 10_000) -> None:
    elapsed = 0
    while not predicate() and elapsed < timeout_ms:
        QTest.qWait(10)
        elapsed += 10
    assert predicate()


def persist_sources(
    tmp_path,
    post_ids,
    *,
    corrupt_post_id: int | None = None,
    source_kind: str = "sample",
) -> tuple:
    database = tmp_path / "state.sqlite"
    cache_directory = tmp_path / "image_analysis"
    data = png_bytes()
    digest = hashlib.sha256(data).hexdigest()
    with ImageAnalysisRepository(database) as repository, ImageSourceService(
        repository, cache_directory
    ) as sources:
        for post_id in post_ids:
            repository.add_item(
                AnalysisItem(
                    SourceReference(
                        InputKind.GELBOORU_POST,
                        site="gelbooru",
                        post_id=str(post_id),
                    ),
                    content_sha256=digest,
                    mime_type="image/png",
                    width=4,
                    height=4,
                ),
                request_analysis=False,
            )
        sources.cache.put(
            digest,
            data,
            site="gelbooru",
            post_id=str(next(iter(post_ids))),
            source_kind=source_kind,
            mime_type="image/png",
            width=4,
            height=4,
            sha256=digest,
        )
        if corrupt_post_id is not None:
            sources.cache.connection.execute(
                "UPDATE image_cache SET data=?,byte_size=? WHERE cache_key=?",
                (b"bad!", 4, digest),
            )
            sources.cache.connection.commit()
    return database, cache_directory


def test_timeout_aborts_request_releases_slot_and_starts_next() -> None:
    first, second = FakeReply(), FakeReply(data=png_bytes())
    network = FakeNetwork([first, second])
    loader = ThumbnailLoader(
        ThumbnailMemoryCache(), network=network, concurrency=1, timeout_ms=1000,
        max_retries=0, rate_limit=None,
    )
    loader.begin_wave([key(1), key(2)])
    assert network.request_count == 1

    loader._timeout(first)

    assert first.aborted
    assert loader._timeouts == 1
    assert network.request_count == 2
    assert loader.active_count == 1


def test_transient_error_retries_once_then_succeeds() -> None:
    failed = FakeReply(error=QNetworkReply.NetworkError.TemporaryNetworkFailureError)
    succeeded = FakeReply(data=png_bytes())
    network = FakeNetwork([failed, succeeded])
    loader = ThumbnailLoader(
        ThumbnailMemoryCache(), network=network, concurrency=1, max_retries=1,
    )
    target = key(1)
    loader.begin_wave([target])
    failed.finished.emit()
    QTest.qWait(300)
    assert network.request_count == 2
    succeeded.finished.emit()

    assert loader._network_success == 1
    assert loader._network_errors == 1
    assert loader._failed == 0
    assert loader.cache.get(target) is not None


def test_concurrency_never_exceeds_configured_limit() -> None:
    replies = [FakeReply(data=png_bytes()) for _ in range(10)]
    network = FakeNetwork(replies)
    loader = ThumbnailLoader(
        ThumbnailMemoryCache(), network=network, concurrency=3, max_retries=0,
        rate_limit=None,
    )
    loader.begin_wave([key(index) for index in range(10)])
    assert network.request_count == 3
    assert loader.active_count == 3

    replies[0].finished.emit()
    assert network.request_count == 4
    assert loader.active_count == 3


def test_extend_wave_keeps_inflight_request_and_deduplicates_keys() -> None:
    replies = [FakeReply(data=png_bytes()) for _ in range(3)]
    network = FakeNetwork(replies)
    loader = ThumbnailLoader(
        ThumbnailMemoryCache(), network=network, concurrency=1,
        max_retries=0, rate_limit=None,
    )
    loader.begin_wave([key(1), key(2)])
    assert network.request_count == 1
    wave = loader.current_wave

    added = loader.extend_wave([key(1), key(2), key(3)])

    assert added == 1
    assert loader.current_wave == wave
    assert network.request_count == 1
    replies[0].finished.emit()
    assert network.request_count == 2
    replies[1].finished.emit()
    assert network.request_count == 3


def test_cache_hit_updates_stats_without_network_request() -> None:
    cache = ThumbnailMemoryCache()
    target = key(1)
    image = QImage(4, 4, QImage.Format.Format_ARGB32)
    image.fill(0xFF112233)
    cache.put(target, image)
    network = FakeNetwork([])
    loader = ThumbnailLoader(cache, network=network)

    loader.begin_wave([target])

    assert network.request_count == 0
    assert loader._cache_hits == 1
    assert loader._done == 1


def test_mixed_persistent_sources_only_request_missing_posts(tmp_path) -> None:
    database, cache_directory = persist_sources(tmp_path, range(1, 8))
    replies = [FakeReply(data=png_bytes()) for _ in range(3)]
    network = FakeNetwork(replies)
    loader = ThumbnailLoader(
        ThumbnailMemoryCache(),
        network=network,
        concurrency=3,
        max_retries=0,
        rate_limit=None,
        persistent_sources=PersistentThumbnailSources(database, cache_directory),
    )

    loader.begin_wave([key(post_id) for post_id in range(1, 11)])
    wait_until(lambda: network.request_count == 3 and loader._persistent_source_hits == 7)
    for reply in replies:
        reply.finished.emit()

    assert loader._done == 10
    assert loader._memory_cache_hits == 0
    assert loader._persistent_source_hits == 7
    assert loader._network_requests == 3
    assert loader._network_success == 3


@pytest.mark.parametrize("source_kind", ["sample", "original"])
def test_persistent_sample_or_original_becomes_thumbnail(tmp_path, source_kind) -> None:
    database, cache_directory = persist_sources(
        tmp_path, [1], source_kind=source_kind
    )
    loader = ThumbnailLoader(
        ThumbnailMemoryCache(),
        network=FakeNetwork([]),
        persistent_sources=PersistentThumbnailSources(database, cache_directory),
    )

    loader.begin_wave([key(1)])
    wait_until(lambda: loader._done == 1)

    assert loader._persistent_source_hits == 1
    assert loader.cache.get(key(1)) is not None


def test_persistent_decode_is_scaled_without_full_resolution_qimage() -> None:
    image = QImage(1600, 900, QImage.Format.Format_ARGB32)
    image.fill(0xFF112233)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    buffer.close()

    thumbnail = decode_thumbnail_bytes(bytes(data), 320)

    assert (thumbnail.width(), thumbnail.height()) == (320, 180)


def test_memory_cache_precedes_persistent_source(tmp_path) -> None:
    database, cache_directory = persist_sources(tmp_path, [1])
    cache = ThumbnailMemoryCache()
    image = QImage(4, 4, QImage.Format.Format_ARGB32)
    image.fill(0xFF445566)
    cache.put(key(1), image)
    network = FakeNetwork([])
    loader = ThumbnailLoader(
        cache,
        network=network,
        persistent_sources=PersistentThumbnailSources(database, cache_directory),
    )

    loader.begin_wave([key(1)])

    assert loader._memory_cache_hits == 1
    assert loader._persistent_source_hits == 0
    assert loader._network_requests == 0
    assert network.request_count == 0


def test_corrupt_persistent_blob_falls_back_to_network(tmp_path) -> None:
    database, cache_directory = persist_sources(tmp_path, [1], corrupt_post_id=1)
    reply = FakeReply(data=png_bytes())
    network = FakeNetwork([reply])
    loader = ThumbnailLoader(
        ThumbnailMemoryCache(),
        network=network,
        max_retries=0,
        rate_limit=None,
        persistent_sources=PersistentThumbnailSources(database, cache_directory),
    )

    loader.begin_wave([key(1)])
    wait_until(lambda: network.request_count == 1)
    reply.finished.emit()

    assert loader._persistent_source_hits == 0
    assert loader._network_requests == 1
    assert loader._network_success == 1


def test_late_persistent_result_from_old_wave_is_ignored() -> None:
    cache = ThumbnailMemoryCache()
    loader = ThumbnailLoader(cache, network=FakeNetwork([]))
    loader.begin_wave([])
    old_wave = loader.current_wave
    loader.begin_wave([])
    image = QImage(4, 4, QImage.Format.Format_ARGB32)
    image.fill(0xFF112233)

    loader._persistent_ready(old_wave, key(1), image)

    assert cache.get(key(1)) is None
    assert loader._persistent_source_hits == 0
    assert loader._done == 0


def test_adaptive_cache_grows_from_measured_working_set() -> None:
    cache = ThumbnailMemoryCache()
    image = QImage(1024, 1024, QImage.Format.Format_RGBA8888)
    image.fill(0xFF112233)

    cache.prepare_for_items(40)
    cache.observe_image(image)

    assert cache.max_bytes > 128 * 1024 * 1024
    assert cache.max_bytes <= 1024 * 1024 * 1024
    assert cache.average_image_bytes == image.sizeInBytes()


def test_permanent_http_error_is_not_retried() -> None:
    forbidden = FakeReply(
        error=QNetworkReply.NetworkError.ContentAccessDenied, status=403
    )
    network = FakeNetwork([forbidden])
    loader = ThumbnailLoader(
        ThumbnailMemoryCache(), network=network, concurrency=1, max_retries=1,
    )
    loader.begin_wave([key(1)])
    forbidden.finished.emit()
    QTest.qWait(300)

    assert network.request_count == 1
    assert loader._network_errors == 1
    assert loader._done == 1


def test_real_qt_reply_header_signature_keeps_queue_progressing() -> None:
    replies = [FakeReply(data=png_bytes()) for _ in range(2)]
    loader = ThumbnailLoader(
        ThumbnailMemoryCache(), network=FakeNetwork(replies),
        concurrency=1, max_retries=0, rate_limit=None,
    )
    loader.begin_wave([key(1), key(2)])
    replies[0].finished.emit()
    replies[1].finished.emit()
    assert loader._done == 2
    assert loader._network_success == 2


def test_rate_limiter_spaces_request_starts_without_burst() -> None:
    clock = FakeClock()
    replies = [FakeReply(data=png_bytes()) for _ in range(5)]
    network = FakeNetwork(replies)
    loader = ThumbnailLoader(
        ThumbnailMemoryCache(), network=network, concurrency=1,
        max_retries=0, rate_limit=8.0, clock=clock,
        diagnostic_stage="network",
    )
    loader.begin_wave([key(index) for index in range(5)])
    assert network.request_count == 1
    for index in range(1, 5):
        replies[index - 1].finished.emit()
        clock.value += 0.124
        loader._rate_timer.stop()
        loader._pump()
        assert network.request_count == index
        clock.value += 0.001
        loader._pump()
        assert network.request_count == index + 1
    assert all(
        later - earlier >= 0.125
        for earlier, later in zip(loader._request_start_times, loader._request_start_times[1:])
    )


def test_rate_limiter_applies_to_retry_and_cancel_stops_pending_timer() -> None:
    clock = FakeClock()
    failed = FakeReply(error=QNetworkReply.NetworkError.TemporaryNetworkFailureError)
    retry = FakeReply(data=png_bytes())
    network = FakeNetwork([failed, retry])
    loader = ThumbnailLoader(
        ThumbnailMemoryCache(), network=network, concurrency=1,
        max_retries=1, rate_limit=8.0, clock=clock,
    )
    loader.begin_wave([key(1)])
    failed.finished.emit()
    assert network.request_count == 1
    loader.cancel()
    assert not loader._rate_timer.isActive()
    clock.value += 1.0
    loader._pump()
    assert network.request_count == 1
