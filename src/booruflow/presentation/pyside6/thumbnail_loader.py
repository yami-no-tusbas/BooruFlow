"""Bounded, observable, memory-only thumbnail download queue."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import ceil
from statistics import mean
from time import perf_counter

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from booruflow.domain.booru_sites import site_definition
from booruflow.presentation.pyside6.thumbnail_cache import (
    ThumbnailCacheKey,
    ThumbnailMemoryCache,
)

THUMBNAIL_CONCURRENCY = 6
THUMBNAIL_TIMEOUT_MS = 12_000
THUMBNAIL_MAX_RETRIES = 1
THUMBNAIL_RETRY_BACKOFF_MS = 250
THUMBNAIL_STALL_MS = 10_000


@dataclass(slots=True)
class _ThumbnailJob:
    key: ThumbnailCacheKey
    wave: int
    attempts: int = 0
    timed_out: bool = False
    started_at: float = 0.0


class ThumbnailLoader(QObject):
    """Download thumbnails with bounded concurrency and aggregate diagnostics."""

    image_ready = Signal(object, object)
    failed = Signal(object, str)
    diagnostic = Signal(str, str)

    def __init__(
        self,
        cache: ThumbnailMemoryCache,
        parent: QObject | None = None,
        *,
        network: QNetworkAccessManager | None = None,
        concurrency: int = THUMBNAIL_CONCURRENCY,
        timeout_ms: int = THUMBNAIL_TIMEOUT_MS,
        max_retries: int = THUMBNAIL_MAX_RETRIES,
    ) -> None:
        super().__init__(parent)
        self.cache = cache
        self.network = network or QNetworkAccessManager(self)
        self.network.setCache(None)
        self.concurrency = max(1, int(concurrency))
        self.timeout_ms = max(1, int(timeout_ms))
        self.max_retries = max(0, int(max_retries))
        self._queue: deque[_ThumbnailJob] = deque()
        self._active: dict[QNetworkReply, tuple[_ThumbnailJob, QTimer]] = {}
        self._wave = 0
        self._wave_started = 0.0
        self._last_progress = 0.0
        self._total = 0
        self._done = 0
        self._cache_hits = 0
        self._network_success = 0
        self._timeouts = 0
        self._network_errors = 0
        self._decode_errors = 0
        self._failed = 0
        self._cancelled = 0
        self._network_durations: list[float] = []
        self._decode_durations: list[float] = []
        self._milestones: set[int] = set()
        self._first_thumbnail_ms: float | None = None
        self.stall_timer = QTimer(self)
        self.stall_timer.setInterval(5_000)
        self.stall_timer.timeout.connect(self._check_stall)

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def queued_count(self) -> int:
        return len(self._queue)

    def begin_wave(self, keys: list[ThumbnailCacheKey]) -> None:
        self.cancel()
        self._wave += 1
        self._wave_started = self._last_progress = perf_counter()
        unique_keys = list(dict.fromkeys(keys))
        self._total = len(unique_keys)
        self._done = self._cache_hits = self._network_success = 0
        self._timeouts = self._network_errors = self._decode_errors = self._failed = 0
        self._cancelled = 0
        self._network_durations = []
        self._decode_durations = []
        self._milestones = set()
        self._first_thumbnail_ms = None
        cached_items = []
        for key in unique_keys:
            cached = self.cache.get(key)
            if cached is not None:
                self._cache_hits += 1
                cached_items.append((key, cached))
            else:
                self._queue.append(_ThumbnailJob(key, self._wave))
        self.diagnostic.emit(
            "INFO",
            f"Thumbnail loading started total={self._total} cache_hits={self._cache_hits} "
            f"network_requests={len(self._queue)} concurrency={self.concurrency} "
            f"timeout_ms={self.timeout_ms} retries={self.max_retries}",
        )
        for key, cached in cached_items:
            self._record_completion()
            self.image_ready.emit(key, cached)
        if self._queue or self._active:
            self.stall_timer.start()
            self._pump()
        else:
            self._complete_wave()

    def prioritize(self, keys: list[ThumbnailCacheKey]) -> None:
        priorities = {key: index for index, key in enumerate(keys)}
        self._queue = deque(
            sorted(self._queue, key=lambda job: priorities.get(job.key, len(priorities)))
        )

    def cancel(self) -> None:
        remaining = len(self._queue) + len(self._active)
        if remaining and self._total:
            self._cancelled += remaining
            self.diagnostic.emit(
                "DEBUG",
                f"Thumbnail load cancelled loaded={self._done}/{self._total} "
                f"cancelled={remaining}",
            )
        self._queue.clear()
        active = list(self._active.items())
        self._active.clear()
        for reply, (_job, timer) in active:
            timer.stop()
            reply.abort()
            reply.deleteLater()
        self.stall_timer.stop()

    def _pump(self) -> None:
        while self._queue and len(self._active) < self.concurrency:
            self._start(self._queue.popleft())
        if self._total and self._done >= self._total:
            self._complete_wave()

    def _start(self, job: _ThumbnailJob) -> None:
        job.attempts += 1
        job.timed_out = False
        job.started_at = perf_counter()
        request = QNetworkRequest(QUrl(job.key.url))
        request.setRawHeader(b"User-Agent", b"BooruFlow/0.1")
        referer = (
            "https://www.pixiv.net/"
            if job.key.site == "pixiv"
            else site_definition(job.key.site).base_url
        )
        request.setRawHeader(b"Referer", referer.encode())
        request.setAttribute(
            QNetworkRequest.Attribute.CacheLoadControlAttribute,
            QNetworkRequest.CacheLoadControl.AlwaysNetwork,
        )
        request.setAttribute(QNetworkRequest.Attribute.CacheSaveControlAttribute, False)
        reply = self.network.get(request)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda current=reply: self._timeout(current))
        reply.finished.connect(lambda current=reply: self._reply_finished(current))
        self._active[reply] = (job, timer)
        timer.start(self.timeout_ms)

    def _timeout(self, reply: QNetworkReply) -> None:
        active = self._active.get(reply)
        if active is None:
            return
        active[0].timed_out = True
        reply.abort()

    def _reply_finished(self, reply: QNetworkReply) -> None:
        active = self._active.pop(reply, None)
        if active is None:
            return
        job, timer = active
        timer.stop()
        timer.deleteLater()
        elapsed_ms = (perf_counter() - job.started_at) * 1000
        status_code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        error = reply.error()
        data = bytes(reply.readAll())
        reply.deleteLater()
        if job.wave != self._wave:
            return
        if job.timed_out:
            self._timeouts += 1
            self._retry_or_finish(job, "timeout")
        elif error != QNetworkReply.NetworkError.NoError:
            self._network_errors += 1
            permanent = isinstance(status_code, int) and 400 <= status_code < 500 and status_code not in {408, 429}
            self._retry_or_finish(job, "network_error", permanent=permanent)
        else:
            decode_started = perf_counter()
            image = QImage()
            if not data or not image.loadFromData(data):
                self._decode_durations.append((perf_counter() - decode_started) * 1000)
                self._finish_failure(job, "decode_error")
            else:
                self._decode_durations.append((perf_counter() - decode_started) * 1000)
                self.cache.put(job.key, image)
                self._network_success += 1
                self._network_durations.append(elapsed_ms)
                self._record_completion()
                self.image_ready.emit(job.key, image)
        self._pump()

    def _retry_or_finish(self, job: _ThumbnailJob, reason: str, *, permanent: bool = False) -> None:
        if not permanent and job.attempts <= self.max_retries:
            QTimer.singleShot(THUMBNAIL_RETRY_BACKOFF_MS, lambda current=job: self._retry(current))
            return
        self._finish_failure(job, reason)

    def _retry(self, job: _ThumbnailJob) -> None:
        if job.wave != self._wave:
            return
        self._queue.appendleft(job)
        self._pump()

    def _finish_failure(self, job: _ThumbnailJob, reason: str) -> None:
        self._failed += 1
        if reason == "decode_error":
            self._decode_errors += 1
        elif reason == "cancelled":
            self._cancelled += 1
        self._record_completion()
        self.failed.emit(job.key, reason)

    def _record_completion(self) -> None:
        self._done += 1
        now = perf_counter()
        self._last_progress = now
        if self._first_thumbnail_ms is None:
            self._first_thumbnail_ms = (now - self._wave_started) * 1000
            self.diagnostic.emit(
                "INFO", f"First thumbnail displayed after {self._first_thumbnail_ms:.1f} ms"
            )
        for percent in (25, 50, 75, 100):
            threshold = ceil(self._total * percent / 100) if self._total else 0
            if self._done >= threshold and percent not in self._milestones:
                self._milestones.add(percent)
                self.diagnostic.emit(
                    "INFO",
                    f"Thumbnail load progress percent={percent} loaded={self._done}/{self._total} "
                    f"cache_hits={self._cache_hits} failed={self._failed} "
                    f"elapsed_ms={(now - self._wave_started) * 1000:.1f}",
                )

    def _check_stall(self) -> None:
        if self._done >= self._total:
            return
        stalled_ms = (perf_counter() - self._last_progress) * 1000
        if stalled_ms >= THUMBNAIL_STALL_MS:
            self.diagnostic.emit(
                "WARNING",
                f"Thumbnail loader stalled loaded={self._done}/{self._total} "
                f"active={len(self._active)} queued={len(self._queue)} "
                f"no_progress_ms={stalled_ms:.0f}",
            )

    def _complete_wave(self) -> None:
        self.stall_timer.stop()
        elapsed_ms = (perf_counter() - self._wave_started) * 1000 if self._wave_started else 0.0
        durations = sorted(self._network_durations)
        decode_durations = self._decode_durations
        p95 = durations[min(len(durations) - 1, ceil(len(durations) * 0.95) - 1)] if durations else 0.0
        self.diagnostic.emit(
            "INFO",
            f"Thumbnail load complete total={self._total} cache_hits={self._cache_hits} "
            f"network_success={self._network_success} timeouts={self._timeouts} "
            f"network_errors={self._network_errors} decode_errors={self._decode_errors} "
            f"failed={self._failed} cancelled={self._cancelled} "
            f"first_thumbnail_ms={self._first_thumbnail_ms or 0.0:.1f} "
            f"total_elapsed_ms={elapsed_ms:.1f} avg_network_ms="
            f"{mean(durations) if durations else 0.0:.1f} max_network_ms="
            f"{max(durations) if durations else 0.0:.1f} p95_network_ms={p95:.1f} "
            f"avg_decode_ms={mean(decode_durations) if decode_durations else 0.0:.1f} "
            f"max_decode_ms={max(decode_durations) if decode_durations else 0.0:.1f}",
        )
