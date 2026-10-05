"""Bounded, observable, memory-only thumbnail download queue."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from statistics import mean
from threading import Event
from time import monotonic, perf_counter

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QObject,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QImage, QImageReader
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
PERSISTENT_THUMBNAIL_MAX_EDGE = 320


@dataclass(frozen=True, slots=True)
class PersistentThumbnailSources:
    database: Path
    cache_directory: Path
    max_edge: int = PERSISTENT_THUMBNAIL_MAX_EDGE


def decode_thumbnail_bytes(data: bytes, max_edge: int) -> QImage:
    """Decode directly near the grid size instead of materializing a full image."""
    payload = QByteArray(data)
    buffer = QBuffer(payload)
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        return QImage()
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and max(size.width(), size.height()) > max_edge:
        size.scale(QSize(max_edge, max_edge), Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(size)
    image = reader.read()
    buffer.close()
    if image.isNull():
        return image
    return image.convertToFormat(QImage.Format.Format_RGBA8888)


class _PersistentThumbnailThread(QThread):
    planned = Signal(int, object, object)
    image_ready = Signal(int, object, object)
    fallback = Signal(int, object, str)
    diagnostic = Signal(str, str)

    def __init__(
        self, wave: int, keys: list[ThumbnailCacheKey], config: PersistentThumbnailSources
    ) -> None:
        super().__init__()
        self.wave = wave
        self.keys = keys
        self.config = config
        self._cancelled = Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.infrastructure.image_sources import ImageSourceService

        plan_emitted = False
        pending_local: list[ThumbnailCacheKey] = []
        try:
            with ImageAnalysisRepository(self.config.database) as repository, ImageSourceService(
                repository, self.config.cache_directory
            ) as sources:
                identities = [(key.site, str(key.post_id)) for key in self.keys]
                available = sources.cached_items_for_posts(identities)
                local_keys = [key for key in self.keys if (key.site, str(key.post_id)) in available]
                network_keys = [key for key in self.keys if (key.site, str(key.post_id)) not in available]
                if self._cancelled.is_set():
                    return
                self.planned.emit(self.wave, local_keys, network_keys)
                plan_emitted = True
                pending_local = list(local_keys)
                for key in local_keys:
                    if self._cancelled.is_set():
                        return
                    item = available[(key.site, str(key.post_id))]
                    try:
                        image = decode_thumbnail_bytes(
                            sources.cached_bytes(item), max(1, int(self.config.max_edge))
                        )
                        if image.isNull():
                            raise ValueError("decode_error")
                    except Exception as exc:  # noqa: BLE001 - one bad source must fall back
                        self.fallback.emit(self.wave, key, type(exc).__name__)
                    else:
                        self.image_ready.emit(self.wave, key, image)
                    pending_local.remove(key)
        except Exception as exc:  # noqa: BLE001 - worker boundary preserves network fallback
            if not self._cancelled.is_set():
                self.diagnostic.emit(
                    "WARNING", f"Persistent thumbnail lookup failed error={type(exc).__name__}"
                )
                if not plan_emitted:
                    self.planned.emit(self.wave, [], self.keys)
                else:
                    for key in pending_local:
                        self.fallback.emit(self.wave, key, type(exc).__name__)


@dataclass(slots=True)
class _ThumbnailJob:
    key: ThumbnailCacheKey
    wave: int
    attempts: int = 0
    timed_out: bool = False
    started_at: float = 0.0
    request_id: int = 0


class ThumbnailLoader(QObject):
    """Download thumbnails with bounded concurrency and aggregate diagnostics."""

    image_ready = Signal(object, object)
    failed = Signal(object, str)
    diagnostic = Signal(str, str)
    progress = Signal(object)

    def __init__(
        self,
        cache: ThumbnailMemoryCache,
        parent: QObject | None = None,
        *,
        network: QNetworkAccessManager | None = None,
        concurrency: int = THUMBNAIL_CONCURRENCY,
        timeout_ms: int = THUMBNAIL_TIMEOUT_MS,
        max_retries: int = THUMBNAIL_MAX_RETRIES,
        diagnostic_stage: str = "full",
        rate_limit: float | None = 8.0,
        clock=None,
        rate_limit_sites: frozenset[str] = frozenset({"gelbooru"}),
        persistent_sources: PersistentThumbnailSources | None = None,
    ) -> None:
        super().__init__(parent)
        self.cache = cache
        self.network = network or QNetworkAccessManager(self)
        self.network.setCache(None)
        self.concurrency = max(1, int(concurrency))
        self.timeout_ms = max(1, int(timeout_ms))
        self.max_retries = max(0, int(max_retries))
        if rate_limit is not None and float(rate_limit) <= 0:
            raise ValueError("rate_limit must be positive or None")
        self.rate_limit = None if rate_limit is None else float(rate_limit)
        self._clock = clock or monotonic
        self.rate_limit_sites = frozenset(rate_limit_sites)
        self.persistent_sources = persistent_sources
        if diagnostic_stage not in {"full", "network", "decode"}:
            raise ValueError("Unknown thumbnail diagnostic stage")
        self.diagnostic_stage = diagnostic_stage
        self._queue: deque[_ThumbnailJob] = deque()
        self._active: dict[QNetworkReply, tuple[_ThumbnailJob, QTimer]] = {}
        self._retry_timers: dict[QTimer, _ThumbnailJob] = {}
        self._request_seq = 0
        self._wave = 0
        self._wave_started = 0.0
        self._last_progress = 0.0
        self._total = 0
        self._done = 0
        self._cache_hits = 0
        self._memory_cache_hits = 0
        self._persistent_source_hits = 0
        self._persistent_source_candidates = 0
        self._persistent_source_errors = 0
        self._network_requests = 0
        self._local_pending = 0
        self._resolving_keys: set[ThumbnailCacheKey] = set()
        self._persistent_threads: set[_PersistentThumbnailThread] = set()
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
        self._next_request_at = 0.0
        self._request_start_times: list[float] = []
        self._rate_timer = QTimer(self)
        self._rate_timer.setSingleShot(True)
        self._rate_timer.timeout.connect(self._pump)
        self.stall_timer = QTimer(self)
        self.stall_timer.setInterval(5_000)
        self.stall_timer.timeout.connect(self._check_stall)

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def queued_count(self) -> int:
        return len(self._queue)

    @property
    def current_wave(self) -> int:
        return self._wave

    def begin_wave(self, keys: list[ThumbnailCacheKey]) -> None:
        self.cancel()
        self._wave += 1
        self._wave_started = self._last_progress = perf_counter()
        unique_keys = list(dict.fromkeys(keys))
        duplicate_count = len(keys) - len(unique_keys)
        self._total = len(unique_keys)
        self._done = self._cache_hits = self._memory_cache_hits = self._network_success = 0
        self._persistent_source_hits = self._persistent_source_candidates = 0
        self._persistent_source_errors = 0
        self._network_requests = self._local_pending = 0
        self._resolving_keys.clear()
        self._timeouts = self._network_errors = self._decode_errors = self._failed = 0
        self._cancelled = 0
        self._network_durations = []
        self._decode_durations = []
        self._milestones = set()
        self._first_thumbnail_ms = None
        self._request_start_times = []
        self.cache.prepare_for_items(self._total)
        cached_items = []
        for key in unique_keys:
            cached = self.cache.get(key)
            if cached is not None:
                self._cache_hits += 1
                self._memory_cache_hits += 1
                cached_items.append((key, cached))
                self.cache.observe_image(cached)
        self.progress.emit({
            "wave": self._wave, "total": self._total, "done": 0,
            "cache_hits": self._memory_cache_hits,
            "memory_cache_hits": self._memory_cache_hits,
            "persistent_source_hits": 0, "network_requests": 0, "network_success": 0,
            "failed": 0, "cancelled": 0, "duplicates": duplicate_count,
        })
        self.diagnostic.emit(
            "INFO",
            f"Thumbnail loading started total={self._total} memory_cache_hits={self._memory_cache_hits} "
            f"persistent_lookup_pending={self._total - self._memory_cache_hits} concurrency={self.concurrency} "
            f"timeout_ms={self.timeout_ms} retries={self.max_retries} "
            f"cache_target_bytes={self.cache.target_bytes} cache_current_bytes={self.cache.byte_count} "
            f"cache_items={len(self.cache)} cache_avg_image_bytes={self.cache.average_image_bytes} "
            f"duplicates={duplicate_count}",
        )
        for key, cached in cached_items:
            self._record_completion()
            self.image_ready.emit(key, cached)
        cached_keys = {item[0] for item in cached_items}
        misses = [key for key in unique_keys if key not in cached_keys]
        if misses:
            self.stall_timer.start()
            self._resolve_or_queue(misses)
        else:
            self._complete_wave()

    def extend_wave(self, keys: list[ThumbnailCacheKey]) -> int:
        """Add missing keys to the active wave without cancelling in-flight work."""
        if not keys:
            return 0
        existing = {
            job.key for job in self._queue
        } | {job.key for job, _timer in self._active.values()}
        existing |= {job.key for job in self._retry_timers.values()}
        existing |= self._resolving_keys
        additions = []
        for key in dict.fromkeys(keys):
            if key in existing or self.cache.get(key) is not None:
                continue
            existing.add(key)
            additions.append(key)
        if not additions:
            return 0
        self._total += len(additions)
        self._resolve_or_queue(additions)
        self.diagnostic.emit(
            "INFO",
            f"Thumbnail loading extended wave={self._wave} added={len(additions)} "
            f"total={self._total} persistent_lookup_pending={len(additions)}",
        )
        self.stall_timer.start()
        self._pump()
        return len(additions)

    def prioritize(self, keys: list[ThumbnailCacheKey]) -> None:
        priorities = {key: index for index, key in enumerate(keys)}
        self._queue = deque(
            sorted(self._queue, key=lambda job: priorities.get(job.key, len(priorities)))
        )

    def cancel(self) -> None:
        remaining = len(self._queue) + len(self._active) + self._local_pending
        if remaining and self._total:
            self._cancelled += remaining
            self.diagnostic.emit(
                "DEBUG",
                f"Thumbnail load cancelled loaded={self._done}/{self._total} "
                f"cancelled={remaining}",
            )
        self._queue.clear()
        self._local_pending = 0
        self._resolving_keys.clear()
        for thread in tuple(self._persistent_threads):
            thread.cancel()
        self._rate_timer.stop()
        for timer in self._retry_timers:
            timer.stop()
            timer.deleteLater()
        self._retry_timers.clear()
        active = list(self._active.items())
        self._active.clear()
        for reply, (_job, timer) in active:
            timer.stop()
            timer.deleteLater()
            reply.abort()
            reply.deleteLater()
        self.stall_timer.stop()

    def shutdown(self, timeout_ms: int = 5_000) -> bool:
        """Stop local-source workers before their owning Qt page is destroyed."""
        self.cancel()
        deadline = perf_counter() + max(0, timeout_ms) / 1000
        threads = tuple(self._persistent_threads)
        for thread in threads:
            remaining_ms = max(0, int((deadline - perf_counter()) * 1000))
            if thread.isRunning() and not thread.wait(remaining_ms):
                return False
        return True

    def _pump(self) -> None:
        while self._queue and len(self._active) < self.concurrency:
            job = self._queue[0]
            if self._is_rate_limited(job):
                now = self._clock()
                delay = self._next_request_at - now
                if delay > 0:
                    self._rate_timer.start(max(1, int(delay * 1000 + 0.999)))
                    return
            self._start(self._queue.popleft())
        if self._total and self._done >= self._total and not self._local_pending:
            self._complete_wave()

    def _resolve_or_queue(self, keys: list[ThumbnailCacheKey]) -> None:
        if not keys:
            return
        if self.persistent_sources is None:
            self._network_requests += len(keys)
            self._queue.extend(_ThumbnailJob(key, self._wave) for key in keys)
            self._pump()
            return
        self._local_pending += len(keys)
        self._resolving_keys.update(keys)
        thread = _PersistentThumbnailThread(self._wave, keys, self.persistent_sources)
        self._persistent_threads.add(thread)
        thread.planned.connect(self._persistent_planned)
        thread.image_ready.connect(self._persistent_ready)
        thread.fallback.connect(self._persistent_fallback)
        thread.diagnostic.connect(self.diagnostic)
        thread.finished.connect(lambda worker=thread: self._persistent_thread_finished(worker))
        thread.start()

    def _persistent_planned(
        self, wave: int, local_keys: list[ThumbnailCacheKey], network_keys: list[ThumbnailCacheKey]
    ) -> None:
        if wave != self._wave:
            return
        self._persistent_source_candidates += len(local_keys)
        self._resolving_keys.difference_update(network_keys)
        self._local_pending = max(0, self._local_pending - len(network_keys))
        self._network_requests += len(network_keys)
        self._queue.extend(_ThumbnailJob(key, wave) for key in network_keys)
        self.diagnostic.emit(
            "INFO",
            f"Thumbnail sources planned total={self._total} memory_cache_hits={self._memory_cache_hits} "
            f"persistent_source_candidates={self._persistent_source_candidates} "
            f"network_requests={self._network_requests}",
        )
        self._pump()

    def _persistent_ready(self, wave: int, key: ThumbnailCacheKey, image: QImage) -> None:
        if wave != self._wave:
            return
        self._local_pending = max(0, self._local_pending - 1)
        self._resolving_keys.discard(key)
        self._persistent_source_hits += 1
        self.cache.put(key, image)
        self.cache.observe_image(image)
        self._record_completion()
        if self.diagnostic_stage == "full":
            self.image_ready.emit(key, image)
        self._pump()

    def _persistent_fallback(self, wave: int, key: ThumbnailCacheKey, reason: str) -> None:
        if wave != self._wave:
            return
        self._local_pending = max(0, self._local_pending - 1)
        self._resolving_keys.discard(key)
        self._network_requests += 1
        self._persistent_source_errors += 1
        self._queue.append(_ThumbnailJob(key, wave))
        self.diagnostic.emit(
            "WARNING",
            f"Persistent thumbnail decode failed site={key.site} post_id={key.post_id} "
            f"fallback=network error={reason}",
        )
        self._pump()

    def _persistent_thread_finished(self, thread: _PersistentThumbnailThread) -> None:
        self._persistent_threads.discard(thread)
        thread.deleteLater()

    def _start(self, job: _ThumbnailJob) -> None:
        job.attempts += 1
        self._request_seq += 1
        job.request_id = self._request_seq
        job.timed_out = False
        job.started_at = perf_counter()
        now = self._clock()
        self._request_start_times.append(now)
        if self._is_rate_limited(job):
            self._next_request_at = now + (1.0 / self.rate_limit)
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
        self.diagnostic.emit("DEBUG", f"Thumbnail request created id={job.request_id} wave={job.wave} site={job.key.site} post_id={job.key.post_id}")
        reply = self.network.get(request)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._timeout)
        reply.finished.connect(self._reply_finished)
        self._active[reply] = (job, timer)
        self.diagnostic.emit("DEBUG", f"Thumbnail request started id={job.request_id} wave={job.wave} post_id={job.key.post_id} attempt={job.attempts}")
        timer.start(self.timeout_ms)

    def _is_rate_limited(self, job: _ThumbnailJob) -> bool:
        return self.rate_limit is not None and job.key.site in self.rate_limit_sites

    def _timeout(self, reply: QNetworkReply | None = None) -> None:
        if reply is None:
            timer = self.sender()
            reply = next((current for current, (_, active_timer) in self._active.items()
                          if active_timer is timer), None)
            if reply is None:
                return
        active = self._active.get(reply)
        if active is None:
            return
        active[0].timed_out = True
        reply.abort()

    def _reply_finished(self, reply: QNetworkReply | None = None) -> None:
        if reply is None:
            reply = self.sender()
        if QThread.currentThread() != self.thread():
            self.diagnostic.emit("ERROR", "Thumbnail reply callback arrived off loader thread")
        active = self._active.pop(reply, None)
        if active is None:
            return
        job, timer = active
        self.diagnostic.emit("DEBUG", f"Thumbnail reply received id={job.request_id} wave={job.wave} post_id={job.key.post_id}")
        timer.stop()
        timer.deleteLater()
        elapsed_ms = (perf_counter() - job.started_at) * 1000
        status_code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        error = reply.error()
        data = bytes(reply.readAll())
        raw_header = getattr(reply, "rawHeader", lambda _name: b"")
        content_type = bytes(raw_header("Content-Type")).decode("latin-1", errors="replace")
        self.diagnostic.emit(
            "DEBUG",
            f"Thumbnail HTTP response id={job.request_id} wave={job.wave} post_id={job.key.post_id} status={status_code} "
            f"content_type={content_type or 'unknown'} bytes={len(data)}",
        )
        reply.deleteLater()
        self.diagnostic.emit("DEBUG", f"Thumbnail reply cleanup id={job.request_id} wave={job.wave} post_id={job.key.post_id}")
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
            if self.diagnostic_stage == "network":
                self._network_success += 1
                self._record_completion()
                self._pump()
                return
            decode_started = perf_counter()
            image = QImage()
            self.diagnostic.emit("DEBUG", f"Thumbnail decode started id={job.request_id} wave={job.wave} post_id={job.key.post_id}")
            if not data or not image.loadFromData(data):
                self._decode_durations.append((perf_counter() - decode_started) * 1000)
                self._finish_failure(job, "decode_error")
            else:
                image = image.convertToFormat(QImage.Format.Format_RGBA8888)
                self._decode_durations.append((perf_counter() - decode_started) * 1000)
                self.diagnostic.emit("DEBUG", f"Thumbnail decode completed id={job.request_id} wave={job.wave} post_id={job.key.post_id}")
                self.cache.put(job.key, image)
                self.cache.observe_image(image)
                self._network_success += 1
                self._network_durations.append(elapsed_ms)
                self._record_completion()
                if self.diagnostic_stage == "full":
                    self.image_ready.emit(job.key, image)
                self.diagnostic.emit("DEBUG", f"Thumbnail request completed id={job.request_id} wave={job.wave} post_id={job.key.post_id}")
        self._pump()

    def _retry_or_finish(self, job: _ThumbnailJob, reason: str, *, permanent: bool = False) -> None:
        if not permanent and job.attempts <= self.max_retries:
            timer = QTimer(self)
            timer.setSingleShot(True)
            self._retry_timers[timer] = job
            timer.timeout.connect(self._retry_ready)
            timer.start(THUMBNAIL_RETRY_BACKOFF_MS)
            return
        self._finish_failure(job, reason)

    def _retry_ready(self) -> None:
        timer = self.sender()
        job = self._retry_timers.pop(timer, None)
        timer.deleteLater()
        if job is None:
            return
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
        self.progress.emit({
            "wave": self._wave, "total": self._total, "done": self._done,
            "cache_hits": self._memory_cache_hits,
            "memory_cache_hits": self._memory_cache_hits,
            "persistent_source_hits": self._persistent_source_hits,
            "persistent_source_errors": self._persistent_source_errors,
            "network_requests": self._network_requests,
            "network_success": self._network_success,
            "failed": self._failed, "cancelled": self._cancelled,
        })
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
                    f"memory_cache_hits={self._memory_cache_hits} "
                    f"persistent_source_hits={self._persistent_source_hits} failed={self._failed} "
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
        elapsed_seconds = elapsed_ms / 1000 if elapsed_ms else 0.0
        average_start_rate = (
            len(self._request_start_times) / elapsed_seconds
            if elapsed_seconds else 0.0
        )
        peak_start_rate = 0.0
        left = 0
        for right, started in enumerate(self._request_start_times):
            while started - self._request_start_times[left] >= 1.0:
                left += 1
            peak_start_rate = max(
                peak_start_rate,
                (right - left + 1) / max(1.0, started - self._request_start_times[left]),
            )
        p95 = durations[min(len(durations) - 1, ceil(len(durations) * 0.95) - 1)] if durations else 0.0
        self.diagnostic.emit(
            "INFO",
            f"Thumbnail load complete total={self._total} memory_cache_hits={self._memory_cache_hits} "
            f"persistent_source_hits={self._persistent_source_hits} "
            f"persistent_source_errors={self._persistent_source_errors} "
            f"network_requests={self._network_requests} network_success={self._network_success} timeouts={self._timeouts} "
            f"network_errors={self._network_errors} decode_errors={self._decode_errors} "
            f"failed={self._failed} cancelled={self._cancelled} "
            f"first_thumbnail_ms={self._first_thumbnail_ms or 0.0:.1f} "
            f"total_elapsed_ms={elapsed_ms:.1f} avg_network_ms="
            f"{mean(durations) if durations else 0.0:.1f} max_network_ms="
            f"{max(durations) if durations else 0.0:.1f} p95_network_ms={p95:.1f} "
            f"avg_decode_ms={mean(decode_durations) if decode_durations else 0.0:.1f} "
            f"max_decode_ms={max(decode_durations) if decode_durations else 0.0:.1f} "
            f"average_request_start_rate={average_start_rate:.2f} "
            f"peak_request_start_rate={peak_start_rate:.2f} "
            f"rate_limit={self.rate_limit if self.rate_limit is not None else 'disabled'} "
            f"cache_target_bytes={self.cache.target_bytes} cache_current_bytes={self.cache.byte_count} "
            f"cache_items={len(self.cache)} cache_avg_image_bytes={self.cache.average_image_bytes} "
            f"cache_evictions={self.cache.evictions} cache_hits_total={self.cache.hits} "
            f"cache_misses_total={self.cache.misses}",
        )
        self.cache.settle_working_set(calm=True)
