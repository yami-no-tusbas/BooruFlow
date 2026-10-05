"""Targeted WD14 confidence analysis for the Tagging results grid."""

from __future__ import annotations

import math
import queue
import re
import socket
import sqlite3
import threading
import urllib.error
from collections import Counter
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from statistics import fmean
from time import perf_counter

from booruflow.application.tag_canonicalization import canonicalize_new_gelbooru_tag
from booruflow.application.tagging import canonical_tag_value
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import (
    GelbooruPostProvider,
    ImageSourceError,
    ImageSourceService,
)
from booruflow.infrastructure.tag_category_lookup import LocalTagCategoryLookup
from booruflow.infrastructure.wd14 import (
    WD14Backend,
    WD14Config,
    load_selected_tags,
    resolve_wd14_batch_size,
    wd14_config_identity,
)

CONFIDENCE_BUCKETS = (
    "90-100", "80-89", "70-79", "60-69", "50-59",
    "40-49", "30-39", "20-29", "10-19", "under-10",
    "not-analyzed", "failed",
)

WD14_ACQUISITION_WORKERS = 2
WD14_ACQUISITION_QUEUE_CAPACITY = 2
MAX_WD14_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class TargetedWD14Failure:
    post_id: str
    source_kind: str
    stage: str
    attempt: int
    max_attempts: int
    error_type: str
    error_message: str
    retryable: bool
    status: int | None = None


def _failure_stage(exc: BaseException, default: str) -> str:
    message = str(exc).casefold()
    if "metadata" in message or "post" in message and "read" in message:
        return "metadata"
    if "no usable" in message or "no preview" in message or "unsupported" in message:
        return "source_selection"
    if "download" in message or "http" in message or "connection" in message:
        return "download"
    if "hash" in message or "sha" in message:
        return "hash"
    if "cache" in message or "permission" in message or "disk" in message:
        return "cache_write"
    if "image" in message or "decode" in message or "unreadable" in message:
        return "inspect/decode"
    return default


def _failure_details(
    post_id: str,
    source_kind: str,
    stage: str,
    attempt: int,
    exc: BaseException,
) -> TargetedWD14Failure:
    message = str(exc) or repr(exc)
    status = getattr(exc, "code", None)
    if not isinstance(status, int):
        match = re.search(r"\b(?:status|http)[ =:]+(\d{3})\b", message, re.IGNORECASE)
        status = int(match.group(1)) if match else None
    lowered = message.casefold()
    retryable = (
        status == 429 or status is not None and 500 <= status <= 599
        or isinstance(
            exc, (TimeoutError, socket.timeout, ConnectionResetError,
                  ConnectionError, urllib.error.URLError)
        )
        or isinstance(exc, sqlite3.OperationalError) and "locked" in lowered
        or isinstance(exc, ImageSourceError) and any(
            token in lowered for token in ("timeout", "connection", "reset", "tempor", "429", "5")
        )
    )
    if status == 404 or any(
        token in lowered for token in ("unsupported", "no preview", "no usable", "unreadable")
    ):
        retryable = False
    return TargetedWD14Failure(
        post_id, source_kind, _failure_stage(exc, stage), attempt,
        MAX_WD14_ATTEMPTS, type(exc).__name__, message, retryable, status,
    )


class PersistentWD14Session:
    """Own one reusable backend for sequential targeted analyses."""

    def __init__(self, backend_factory=WD14Backend) -> None:
        self.backend_factory = backend_factory
        self.backend = None
        self._key = None

    def acquire(self, config: WD14Config, diagnostic_callback=None):
        started = perf_counter()
        if diagnostic_callback:
            diagnostic_callback("WD14 session acquire started")
        key = (
            str(config.model_directory.resolve()), config.model_id,
            config.provider_preference, config.store_threshold, config.input_strategy,
        )
        if self.backend is not None and self._key != key:
            self.close()
        if self.backend is None:
            self.backend = self.backend_factory(config)
            if diagnostic_callback and self.backend_factory is WD14Backend:
                self.backend.prepare(trace=diagnostic_callback)
            else:
                self.backend.prepare()
            self._key = key
            if diagnostic_callback:
                diagnostic_callback(
                    f"WD14 session acquire completed created=true elapsed_ms={(perf_counter() - started) * 1000:.1f}"
                )
        elif diagnostic_callback:
            diagnostic_callback(
                f"WD14 session acquire completed reused=true elapsed_ms={(perf_counter() - started) * 1000:.1f}"
            )
        return self.backend

    def close(self) -> None:
        if self.backend is not None:
            self.backend.close()
        self.backend = None
        self._key = None


def confidence_bucket(score: float | None, *, failed: bool = False) -> str:
    if failed:
        return "failed"
    if score is None:
        return "not-analyzed"
    value = min(1.0, max(0.0, float(score)))
    if value >= 0.9:
        return "90-100"
    if value < 0.1:
        return "under-10"
    lower = math.floor((value + 1e-12) * 10) * 10
    return f"{lower}-{lower + 9}"


def resolve_wd14_target(
    target: str, model_directory: Path, alias_database: Path | None = None
) -> tuple[str, ...]:
    normalized = canonical_tag_value(target).replace(" ", "_")
    canonical = canonicalize_new_gelbooru_tag(
        normalized, alias_database
    ).canonical_name
    vocabulary = {
        name.replace(" ", "_").casefold(): name
        for name, _category in load_selected_tags(model_directory / "selected_tags.csv")
    }
    matches = []
    for candidate in (normalized, canonical):
        raw = vocabulary.get(candidate.casefold())
        if raw is not None and raw not in matches:
            matches.append(raw)
    return tuple(matches)


@dataclass(frozen=True, slots=True)
class TargetedWD14Progress:
    total: int
    completed: int
    reused: int
    analyzed: int
    failed: int
    post_id: str = ""
    retrying: int = 0


@dataclass(frozen=True, slots=True)
class TargetedWD14Result:
    tag: str
    scores: dict[int, float]
    failed_post_ids: frozenset[int]
    progress: TargetedWD14Progress
    elapsed_seconds: float
    cancelled: bool = False
    scores_by_tag: dict[str, dict[int, float]] | None = None
    failures: tuple[TargetedWD14Failure, ...] = ()
    attempted_retries: int = 0
    recovered_after_retry: int = 0
    failure_summary: dict[str, int] | None = None


class TargetedWD14Analyzer:
    """Load WD14 once, reuse compatible vectors, and analyze only missing posts."""

    def __init__(
        self,
        database: Path,
        cache_directory: Path,
        model_directory: Path,
        model_id: str,
        alias_database: Path | None,
        credentials: dict[str, object],
        tag_database: Path | None,
        *,
        backend_factory=WD14Backend,
        batch_size: str = "auto",
        session: PersistentWD14Session | None = None,
        preprocess_workers: int = 1,
        diagnostic_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.database = database
        self.cache_directory = cache_directory
        self.model_directory = model_directory
        self.model_id = model_id
        self.alias_database = alias_database
        self.credentials = credentials
        self.tag_database = tag_database
        self.backend_factory = backend_factory
        self.batch_size = str(batch_size or "auto")
        self.session = session
        self.preprocess_workers = max(1, int(preprocess_workers))
        self.diagnostic_callback = diagnostic_callback

    def analyze(
        self,
        tag: str,
        raw_names: tuple[str, ...],
        posts: list[dict],
        progress_callback: Callable[[TargetedWD14Progress], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> TargetedWD14Result:
        result = self.analyze_many(
            ((tag, raw_names),), posts, progress_callback, cancelled
        )
        return result

    def analyze_many(
        self,
        tag_requests: tuple[tuple[str, tuple[str, ...]], ...],
        posts: list[dict],
        progress_callback: Callable[[TargetedWD14Progress], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> TargetedWD14Result:
        if not tag_requests:
            raise ValueError("at least one WD14 tag is required")
        primary_tag = tag_requests[0][0]
        started = perf_counter()
        total = len(posts)
        callback = progress_callback or (lambda _progress: None)
        is_cancelled = cancelled or (lambda: False)
        config = WD14Config(
            self.model_directory, self.model_id, 0.0,
            input_strategy="gelbooru_sample_v1",
        )
        backend = None
        owns_backend = self.session is None
        scores_by_tag: dict[str, dict[int, float]] = {
            tag: {} for tag, _raw_names in tag_requests
        }
        failed: set[int] = set()
        analyzed = 0
        was_cancelled = False
        failure_records: list[TargetedWD14Failure] = []
        attempted_retries = 0
        recovered_after_retry = 0
        source_counts = {"sample": 0, "original": 0, "preview": 0, "fallback_fetch": 0}
        source_kind_by_post: dict[str, str] = {}
        total_downloaded_bytes = 0
        acquisition_times: list[float] = []
        batch_started_times: list[float] = []
        waiting_for_pending: list[float] = []
        try:
            if self.session is not None:
                backend = self.session.acquire(config, self.diagnostic_callback)
                identity = backend.identity
            elif self.backend_factory is WD14Backend:
                identity = wd14_config_identity(config)
            else:
                backend = self.backend_factory(config); backend.prepare(); identity = backend.identity
            # A reused session owns the exact labels used by its loaded model.
            # Cold cache lookups read only the CSV, without loading ONNX.
            labels = tuple(name for name, _category in (
                backend.tags if backend is not None else
                load_selected_tags(self.model_directory / "selected_tags.csv")
            ))
            target_labels = {
                tag: next((name for name in labels
                           if name.casefold() in {value.casefold() for value in raw_names}), None)
                for tag, raw_names in tag_requests
            }
            post_ids = [str(post.get("id", "")) for post in posts]
            with ImageAnalysisRepository(self.database) as repository:
                cache_started = perf_counter()
                vectors = repository.wd14_vectors_for_remote_posts(
                    "gelbooru", post_ids, identity.name, identity.version,
                    identity.configuration_hash, labels=labels,
                )
                complete_cached: set[int] = set()
                for post_id, vector in vectors.items():
                    numeric_id = int(post_id)
                    if all(name in vector for name in target_labels.values()):
                        for tag, name in target_labels.items():
                            scores_by_tag[tag][numeric_id] = float(vector[name])
                        complete_cached.add(numeric_id)
                reused = len(complete_cached)
                if self.diagnostic_callback:
                    self.diagnostic_callback(
                        f"WD14 cache/vector lookup completed existing={reused} "
                        f"elapsed_ms={(perf_counter() - cache_started) * 1000:.1f}"
                    )
                callback(TargetedWD14Progress(total, reused, reused, 0, 0))
                if reused < total and backend is None:
                    backend = self.backend_factory(config)
                    if self.diagnostic_callback and self.backend_factory is WD14Backend:
                        backend.prepare(trace=self.diagnostic_callback)
                    else:
                        backend.prepare()
                    identity = backend.identity
                    labels = tuple(name for name, _category in backend.tags)
                if reused < total:
                    gel = self.credentials.get("gelbooru", {})
                    gel = gel if isinstance(gel, dict) else {}
                    requested = resolve_wd14_batch_size(
                        self.batch_size, getattr(backend, "provider", "CPUExecutionProvider")
                    )
                    # Preparation owns separate SQLite connections. The consumer
                    # keeps the original connection for vector/model-run writes.
                    # Network concurrency and buffering are intentionally independent
                    # from the inference batch size.
                    pending_queue: queue.Queue[tuple] = queue.Queue(
                        maxsize=WD14_ACQUISITION_QUEUE_CAPACITY
                    )
                    pending_started = perf_counter()
                    prepared_count = 0
                    first_pending_logged = False
                    first_batch_logged = False
                    waiting_since: float | None = None

                    def _queue_put(entry: tuple) -> bool:
                        while True:
                            if is_cancelled():
                                return False
                            try:
                                pending_queue.put(entry, timeout=0.1)
                                return True
                            except queue.Full:
                                continue

                    def _acquire_one_once(sequence: int, post: dict) -> tuple:
                        raw_post = post
                        post_id = str(post.get("id", ""))
                        if not post_id:
                            return ("error", sequence, post_id, RuntimeError("missing post id"), None, {})
                        timings: dict[str, object] = {}
                        run_id = None
                        with ImageAnalysisRepository(self.database) as worker_repo, ImageSourceService(
                            worker_repo, self.cache_directory
                        ) as worker_sources:
                            worker_lookup = LocalTagCategoryLookup(self.tag_database) if self.tag_database else None
                            worker_provider = GelbooruPostProvider(
                                str(gel.get("user_id", "")), str(gel.get("api_key", "")),
                                category_lookup=worker_lookup,
                            )
                            lookup_started = perf_counter()
                            item = worker_repo.item_by_remote_source("gelbooru", post_id)
                            timings["lookup_ms"] = (perf_counter() - lookup_started) * 1000
                            timings["sample_width"] = int(raw_post.get("sample_width") or 0)
                            timings["sample_height"] = int(raw_post.get("sample_height") or 0)
                            timings["original_width"] = int(raw_post.get("width") or 0)
                            timings["original_height"] = int(raw_post.get("height") or 0)
                            if item is None:
                                acquisition_started = perf_counter()
                                item_id = worker_sources.add_post_data(
                                    worker_provider, raw_post, post_id, request_analysis=False,
                                    trace=lambda name, value: timings.__setitem__(name, value),
                                )
                                item = worker_repo.get_item(item_id)
                                timings["acquisition_ms"] = (perf_counter() - acquisition_started) * 1000
                            elif not (
                                item.cached_path is not None and item.cached_path.is_file()
                            ) and not (
                                item.content_sha256
                                and worker_sources.cache.contains(item.content_sha256)
                            ):
                                acquisition_started = perf_counter()
                                worker_sources.resolve_post_data(
                                    item.id, worker_provider, raw_post, post_id,
                                    trace=lambda name, value: timings.__setitem__(name, value),
                                )
                                item = worker_repo.item_by_remote_source("gelbooru", post_id)
                                timings["acquisition_ms"] = (perf_counter() - acquisition_started) * 1000
                            else:
                                timings["acquisition_ms"] = 0.0
                            if item is None:
                                raise RuntimeError("original image is unavailable")
                            source = (
                                item.cached_path
                                if item.cached_path is not None and item.cached_path.is_file()
                                else worker_sources.cached_bytes(
                                    item,
                                    trace=lambda name, value: timings.__setitem__(name, value),
                                )
                            )
                            suppress_started = perf_counter()
                            worker_repo.suppress_analysis_request(item.id)
                            timings["suppress_ms"] = (perf_counter() - suppress_started) * 1000
                            run_started = perf_counter()
                            run_id = worker_repo.begin_model_run(
                                item.id, identity.backend, identity.name, identity.version,
                                identity.configuration_hash, backend.runtime, backend.device,
                            )
                            if run_id is None:
                                run_id = worker_repo.model_run_id_for_identity(
                                    item.id, identity.backend, identity.name, identity.version,
                                    identity.configuration_hash,
                                )
                            timings["model_run_ms"] = (perf_counter() - run_started) * 1000
                            if run_id is None:
                                raise RuntimeError("could not create the WD14 model run")
                            return ("ready", sequence, post, post_id, source, run_id, timings)

                    def _acquire_one(sequence: int, post: dict) -> tuple:
                        failures: list[TargetedWD14Failure] = []
                        source_kind = (
                            "sample"
                            if str(post.get("sample_url") or "").strip()
                            and int(post.get("sample_width") or 0) >= 448
                            and int(post.get("sample_height") or 0) >= 448
                            else "original"
                        )
                        for attempt in range(1, MAX_WD14_ATTEMPTS + 1):
                            if is_cancelled():
                                return ("cancelled", sequence, str(post.get("id", "")), None, None, {}, ())
                            try:
                                entry = _acquire_one_once(sequence, post)
                                if entry[0] == "ready":
                                    source_kind = str(entry[6].get("source_kind", "original"))
                                    if failures and self.diagnostic_callback:
                                        self.diagnostic_callback(
                                            f"WD14 retry recovered post_id={post.get('id', '')} "
                                            f"attempt={attempt}/{MAX_WD14_ATTEMPTS}"
                                        )
                                    return (*entry, tuple(failures))
                            except Exception as exc:  # noqa: BLE001 - per-image retry boundary
                                failure = _failure_details(
                                    str(post.get("id", "")), source_kind,
                                    "acquisition", attempt, exc,
                                )
                                failures.append(failure)
                                self.diagnostic_callback and self.diagnostic_callback(
                                    "WD14 acquisition failed "
                                    f"post_id={failure.post_id} source_kind={failure.source_kind} "
                                    f"stage={failure.stage} attempt={failure.attempt}/{failure.max_attempts} "
                                    f"error_type={failure.error_type} error={failure.error_message!r}"
                                )
                                if is_cancelled() or not failure.retryable or attempt >= MAX_WD14_ATTEMPTS:
                                    return (
                                        "error", sequence, str(post.get("id", "")), exc,
                                        None, {}, tuple(failures),
                                    )
                                threading.Event().wait(0.1 * attempt)
                        return (
                            "error", sequence, str(post.get("id", "")),
                            RuntimeError("WD14 acquisition retry exhausted"), None, {}, tuple(failures),
                        )

                    def _prepare_pending() -> None:
                        nonlocal prepared_count, first_pending_logged, total_downloaded_bytes, attempted_retries, recovered_after_retry
                        producer_started = perf_counter()
                        candidates = [
                            (sequence, post) for sequence, post in enumerate(posts)
                            if str(post.get("id", "")) and int(str(post.get("id"))) not in complete_cached
                        ]
                        futures: dict[Future, tuple[int, dict]] = {}
                        candidate_index = 0
                        try:
                            worker_count = min(
                                WD14_ACQUISITION_WORKERS, max(1, len(candidates))
                            )
                            if self.diagnostic_callback:
                                self.diagnostic_callback(
                                    f"WD14 acquisition worker count={worker_count} queue_capacity={pending_queue.maxsize}"
                                )
                            with ThreadPoolExecutor(
                                max_workers=worker_count, thread_name_prefix="wd14-acquire"
                            ) as executor:
                                while candidate_index < len(candidates) and len(futures) < pending_queue.maxsize:
                                    sequence, post = candidates[candidate_index]
                                    futures[executor.submit(_acquire_one, sequence, post)] = (sequence, post)
                                    candidate_index += 1
                                while futures:
                                    done_futures, _ = wait(futures, return_when=FIRST_COMPLETED)
                                    for future in done_futures:
                                        sequence, post = futures.pop(future)
                                        try:
                                            entry = future.result()
                                        except Exception as exc:  # noqa: BLE001 - per-image boundary
                                            failure = _failure_details(
                                                str(post.get("id", "")), "unknown", "other", 1, exc
                                            )
                                            self.diagnostic_callback and self.diagnostic_callback(
                                                "WD14 acquisition failed "
                                                f"post_id={failure.post_id} source_kind={failure.source_kind} "
                                                f"stage={failure.stage} attempt=1/{MAX_WD14_ATTEMPTS} "
                                                f"error_type={failure.error_type} error={failure.error_message!r}"
                                            )
                                            entry = (
                                                "error", sequence, str(post.get("id", "")), exc,
                                                None, {}, (failure,),
                                            )
                                        if entry[0] == "ready":
                                            prepared_count += 1
                                            timings = entry[6]
                                            acquisition_failures = entry[7] if len(entry) > 7 else ()
                                            if acquisition_failures:
                                                attempted_retries += len(acquisition_failures)
                                                recovered_after_retry += 1
                                            acquisition_times.append(float(timings.get("acquisition_ms", 0.0)))
                                            source_kind = str(timings.get("source_kind", "original"))
                                            source_kind_by_post[str(entry[3])] = source_kind
                                            if source_kind in source_counts:
                                                source_counts[source_kind] += 1
                                            if bool(timings.get("fallback_fetch", False)):
                                                source_counts["fallback_fetch"] += 1
                                            total_downloaded_bytes += int(
                                                timings.get("download_size_bytes", 0)
                                            )
                                            if not first_pending_logged:
                                                first_pending_logged = True
                                                elapsed_ms = (perf_counter() - pending_started) * 1000
                                                self.diagnostic_callback and self.diagnostic_callback(
                                                    f"WD14 first pending image/model-run ready "
                                                    f"time_to_first_pending_ms={elapsed_ms:.1f} "
                                                    f"source_kind={timings.get('source_kind', 'original')} "
                                                    f"acquisition_ms={timings.get('acquisition_ms', 0.0):.1f} "
                                                    f"metadata_fetch_ms={timings.get('metadata_fetch_ms', 0.0):.1f} "
                                                    f"download_bytes_ms={timings.get('download_bytes_ms', 0.0):.1f} "
                                                    f"hash_ms={timings.get('hash_ms', 0.0):.1f} "
                                                    f"cache_write_ms={timings.get('cache_write_ms', 0.0):.1f} "
                                                    f"inspect_image_ms={timings.get('inspect_image_ms', 0.0):.1f}"
                                                )
                                        if not _queue_put(entry):
                                            break
                                        while (
                                            candidate_index < len(candidates)
                                            and len(futures) < pending_queue.maxsize
                                            and not is_cancelled()
                                        ):
                                            next_sequence, next_post = candidates[candidate_index]
                                            futures[executor.submit(_acquire_one, next_sequence, next_post)] = (
                                                next_sequence, next_post
                                            )
                                            candidate_index += 1
                                    if is_cancelled():
                                        for future in futures:
                                            future.cancel()
                                        break
                        except Exception as exc:  # noqa: BLE001 - keep later UI errors visible
                            self.diagnostic_callback and self.diagnostic_callback(
                                f"WD14 pending preparation worker failed error={exc}"
                            )
                        finally:
                            if self.diagnostic_callback:
                                average = fmean(acquisition_times) if acquisition_times else 0.0
                                ordered = sorted(acquisition_times)
                                p95 = ordered[min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))] if ordered else 0.0
                                self.diagnostic_callback(
                                    f"WD14 pending image/model-run preparation completed count={prepared_count} "
                                    f"total_pending_prepare_time_ms={(perf_counter() - producer_started) * 1000:.1f} "
                                    f"average_acquisition_ms={average:.1f} p95_acquisition_ms={p95:.1f} "
                                    f"sample_count={source_counts['sample']} "
                                    f"original_count={source_counts['original']} "
                                    f"preview_count={source_counts['preview']} "
                                    f"fallback_fetch_count={source_counts['fallback_fetch']} "
                                    f"total_downloaded_bytes={total_downloaded_bytes} "
                                    f"average_downloaded_bytes_per_image="
                                    f"{(total_downloaded_bytes / len(acquisition_times)) if acquisition_times else 0.0:.1f}"
                                )
                            while True:
                                try:
                                    pending_queue.put(("done",), timeout=0.1)
                                    break
                                except queue.Full:
                                    continue

                    if self.diagnostic_callback:
                        self.diagnostic_callback(
                            f"WD14 pending image/model-run preparation started queue_capacity={pending_queue.maxsize}"
                        )
                    producer = threading.Thread(target=_prepare_pending, name="wd14-pending", daemon=True)
                    producer.start()

                    def _run_one_with_retries(post_id: str, path: Path, initial: Exception | None = None):
                        inference_failures: list[TargetedWD14Failure] = []
                        first_attempt = 1
                        if initial is not None:
                            first_failure = _failure_details(
                                post_id, source_kind_by_post.get(post_id, "original"), "inference", 1, initial
                            )
                            inference_failures.append(first_failure)
                            if self.diagnostic_callback:
                                self.diagnostic_callback(
                                    "WD14 inference failed "
                                    f"post_id={post_id} source_kind={source_kind_by_post.get(post_id, 'original')} stage=inference "
                                    f"attempt=1/{MAX_WD14_ATTEMPTS} "
                                    f"error_type={first_failure.error_type} "
                                    f"error={first_failure.error_message!r}"
                                )
                            if not first_failure.retryable:
                                return initial, tuple(inference_failures)
                            first_attempt = 2
                        for attempt in range(first_attempt, MAX_WD14_ATTEMPTS + 1):
                            try:
                                result = backend.analyze(path)
                                if inference_failures and self.diagnostic_callback:
                                    self.diagnostic_callback(
                                        f"WD14 retry recovered post_id={post_id} "
                                        f"attempt={attempt}/{MAX_WD14_ATTEMPTS}"
                                    )
                                return result, tuple(inference_failures)
                            except Exception as exc:  # noqa: BLE001 - per-image retry boundary
                                failure = _failure_details(
                                    post_id, source_kind_by_post.get(post_id, "original"), "inference", attempt, exc
                                )
                                inference_failures.append(failure)
                                if self.diagnostic_callback:
                                    self.diagnostic_callback(
                                        "WD14 inference failed "
                                        f"post_id={post_id} source_kind={source_kind_by_post.get(post_id, 'original')} stage=inference "
                                        f"attempt={attempt}/{MAX_WD14_ATTEMPTS} "
                                        f"error_type={failure.error_type} error={failure.error_message!r}"
                                    )
                                if not failure.retryable or attempt >= MAX_WD14_ATTEMPTS:
                                    return exc, tuple(inference_failures)
                                threading.Event().wait(0.1 * attempt)
                        return RuntimeError("WD14 inference retry exhausted"), tuple(inference_failures)

                    group: list[tuple] = []
                    waiting_since = None
                    while True:
                        entry = pending_queue.get()
                        kind = entry[0]
                        if kind == "done":
                            if is_cancelled():
                                was_cancelled = True
                            if group and not was_cancelled:
                                batches = (group,)
                            else:
                                batches = ()
                            done = True
                        elif kind == "error":
                            post_id = entry[2]
                            failed.add(int(post_id))
                            error_failures = entry[6] if len(entry) > 6 else ()
                            if error_failures:
                                failure_records.append(error_failures[-1])
                            if error_failures:
                                attempted_retries += max(
                                    0, int(error_failures[-1].attempt) - 1
                                )
                            callback(TargetedWD14Progress(
                                total, reused + analyzed + len(failed), reused,
                                analyzed, len(failed), post_id,
                            ))
                            continue
                        else:
                            if is_cancelled():
                                was_cancelled = True
                                repository.fail_model_run(entry[5], "cancelled")
                                continue
                            group.append(entry[1:6])
                            batches = ()
                            done = False
                            if len(group) >= requested:
                                batches = (group,)
                                group = []
                        for batch_group in batches:
                            if is_cancelled():
                                was_cancelled = True
                                for _sequence, _post, _post_id, _path, pending_run_id in batch_group:
                                    repository.fail_model_run(pending_run_id, "cancelled")
                                continue
                            batch_started = perf_counter()
                            batch_started_times.append(batch_started)
                            if waiting_since is not None:
                                waiting_for_pending.append((batch_started - waiting_since) * 1000)
                                waiting_since = None
                            if self.diagnostic_callback and not first_batch_logged:
                                first_batch_logged = True
                                self.diagnostic_callback(
                                    f"WD14 first batch started time_to_first_batch_ms="
                                    f"{(perf_counter() - started) * 1000:.1f} size={len(batch_group)}"
                                )
                            paths = [entry[3] for entry in batch_group]
                            try:
                                results = backend.analyze_batch(
                                    paths, batch_size=requested,
                                    preprocess_workers=(
                                        self.preprocess_workers
                                        if getattr(backend, "provider", "CPUExecutionProvider")
                                        == "CUDAExecutionProvider" else 1
                                    ),
                                )
                            except AttributeError:
                                results = tuple(None for _path in paths)
                            except Exception:  # noqa: BLE001 - isolate a batch failure per image
                                results = tuple(None for _path in paths)
                            if len(results) != len(batch_group):
                                results = tuple(None for _path in paths)
                            for (_sequence, post, post_id, _path, run_id), result in zip(
                                batch_group, results, strict=True
                            ):
                                item_failures: tuple[TargetedWD14Failure, ...] = ()
                                if result is None or isinstance(result, Exception):
                                    result, item_failures = _run_one_with_retries(
                                        post_id, _path, result if isinstance(result, Exception) else None
                                    )
                                    attempted_retries += len(item_failures)
                                    if not isinstance(result, Exception) and item_failures:
                                        recovered_after_retry += 1
                                try:
                                    if isinstance(result, Exception):
                                        raise result
                                    vector = {
                                        prediction.raw_name: prediction.score
                                        for prediction in result.predictions
                                    }
                                    repository.save_wd14_score_vector(run_id, vector, labels=labels)
                                    store_finished = perf_counter()
                                    repository.complete_model_run(run_id)
                                    missing = []
                                    for tag, raw_names in tag_requests:
                                        match = next(
                                            (score for name, score in vector.items()
                                             if name.casefold() in {value.casefold() for value in raw_names}),
                                            None,
                                        )
                                        if match is None:
                                            missing.append(tag)
                                        else:
                                            scores_by_tag[tag][int(post_id)] = float(match)
                                    if missing:
                                        raise RuntimeError(
                                            "missing WD14 scores: " + ", ".join(missing)
                                        )
                                    analyzed += 1
                                    if analyzed == 1 and self.diagnostic_callback:
                                        self.diagnostic_callback(
                                            f"WD14 first vector store completed elapsed_ms={(store_finished - started) * 1000:.1f}"
                                        )
                                except Exception as exc:  # noqa: BLE001 - per-image boundary
                                    failed.add(int(post_id))
                                    if not item_failures:
                                        item_failures = (
                                            _failure_details(
                                                post_id, source_kind_by_post.get(post_id, "original"), "vector_store", 1, exc
                                            ),
                                        )
                                        self.diagnostic_callback and self.diagnostic_callback(
                                            "WD14 analysis failed "
                                            f"post_id={post_id} source_kind={source_kind_by_post.get(post_id, 'original')} stage=vector_store "
                                            f"attempt=1/{MAX_WD14_ATTEMPTS} error_type={type(exc).__name__} "
                                            f"error={str(exc)!r}"
                                        )
                                    if item_failures:
                                        failure_records.append(item_failures[-1])
                                    repository.fail_model_run(run_id, str(exc))
                                callback(TargetedWD14Progress(
                                    total, reused + analyzed + len(failed), reused,
                                    analyzed, len(failed), post_id,
                                ))
                                if analyzed == 1 and self.diagnostic_callback:
                                    self.diagnostic_callback(
                                        f"WD14 first result emitted time_to_first_result_ms={(perf_counter() - started) * 1000:.1f}"
                                    )
                            if not group and not done:
                                waiting_since = perf_counter()
                        if done:
                            break
                    producer.join()
                repository.prune_wd14_score_vectors()
        finally:
            if backend is not None and owns_backend:
                backend.close()
        failure_summary = Counter(
            (
                f"http_{failure.status}"
                if failure.status is not None
                else failure.error_type
            )
            for failure in failure_records
        )
        if self.diagnostic_callback:
            intervals = [
                (later - earlier) * 1000
                for earlier, later in pairwise(batch_started_times)
            ]
            average_interval = fmean(intervals) if intervals else 0.0
            average_wait = fmean(waiting_for_pending) if waiting_for_pending else 0.0
            self.diagnostic_callback(
                f"WD14 summary tag={primary_tag} total={total} cached={reused} "
                f"analyzed={analyzed} failed={len(failed)} "
                f"attempted_retries={attempted_retries} recovered_after_retry={recovered_after_retry} "
                f"cancelled={was_cancelled} total_analysis_time_ms={(perf_counter() - started) * 1000:.1f} "
                f"sample_count={source_counts['sample']} original_count={source_counts['original']} "
                f"preview_count={source_counts['preview']} fallback_fetch_count={source_counts['fallback_fetch']} "
                f"total_downloaded_bytes={total_downloaded_bytes} "
                f"average_batch_interval_ms={average_interval:.1f} "
                f"average_waiting_for_pending_ms={average_wait:.1f} "
                f"failure_summary={dict(failure_summary)!r}"
            )
        ordered_post_ids = [
            int(str(post.get("id"))) for post in posts if str(post.get("id", "")).isdigit()
        ]
        for requested_tag, tag_scores in scores_by_tag.items():
            scores_by_tag[requested_tag] = {
                post_id: tag_scores[post_id]
                for post_id in ordered_post_ids
                if post_id in tag_scores
            }
        progress = TargetedWD14Progress(
            total, reused + analyzed + len(failed), reused,
            analyzed, len(failed),
        )
        return TargetedWD14Result(
            primary_tag, scores_by_tag[primary_tag], frozenset(failed), progress,
            perf_counter() - started, was_cancelled, scores_by_tag,
            tuple(failure_records), attempted_retries, recovered_after_retry,
            dict(failure_summary),
        )
