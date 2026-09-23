from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from booruflow.presentation.pyside6.thumbnail_cache import (
    ThumbnailCacheKey,
    ThumbnailMemoryCache,
)
from booruflow.presentation.pyside6.thumbnail_loader import ThumbnailLoader

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


def test_timeout_aborts_request_releases_slot_and_starts_next() -> None:
    first, second = FakeReply(), FakeReply(data=png_bytes())
    network = FakeNetwork([first, second])
    loader = ThumbnailLoader(
        ThumbnailMemoryCache(), network=network, concurrency=1, timeout_ms=1000,
        max_retries=0,
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
    )
    loader.begin_wave([key(index) for index in range(10)])
    assert network.request_count == 3
    assert loader.active_count == 3

    replies[0].finished.emit()
    assert network.request_count == 4
    assert loader.active_count == 3


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
        concurrency=1, max_retries=0,
    )
    loader.begin_wave([key(1), key(2)])
    replies[0].finished.emit()
    replies[1].finished.emit()
    assert loader._done == 2
    assert loader._network_success == 2
