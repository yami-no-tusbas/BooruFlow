"""Separate-process worker for modular image-analysis backends."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from booruflow.application.embedding import EmbeddingResult
from booruflow.application.tag_canonicalization import canonicalize_new_gelbooru_tag
from booruflow.domain.image_analysis import (
    AnalysisItem,
    AnalysisState,
    ColorStatistics,
    ModelIdentity,
    ObservationSource,
)
from booruflow.infrastructure.classic_image_analysis import ClassicImageAnalyzer
from booruflow.infrastructure.hydra import HydraBackend, HydraConfig, HydraResult
from booruflow.infrastructure.image_analysis_cache import ImageCacheMissError
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository, utc_now
from booruflow.infrastructure.image_sources import (
    ImageSourceService,
    analysis_cache_directory_for_repository,
)
from booruflow.infrastructure.media_frames import representative_frames
from booruflow.infrastructure.wd14 import (
    DEFAULT_MODEL_ID,
    WD14Backend,
    WD14Config,
    WD14OutOfMemoryError,
    WD14Result,
    WD14UnavailableError,
    resolve_wd14_batch_size,
)


def wd14_canonicalizer(site: str | None, alias_database: Path | None):
    """Use Gelbooru aliases only for Gelbooru/local analysis contexts."""
    if site not in {None, "gelbooru"}:
        return None
    return lambda name: canonicalize_new_gelbooru_tag(
        name, alias_database
    ).canonical_name


class AnalysisBackend:
    identity: ModelIdentity

    def analyze(self, path: Path) -> ColorStatistics | WD14Result | HydraResult:
        raise NotImplementedError


class ItemHeartbeat:
    """Keep a claim alive while a backend performs a long blocking inference."""

    def __init__(self, database: Path, item_id: int | Sequence[int], interval: float) -> None:
        self.database = database
        self.item_id = item_id
        self.interval = max(0.1, interval)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args) -> None:
        self.stop.set()
        self.thread.join(timeout=self.interval + 1.0)

    def _run(self) -> None:
        with ImageAnalysisRepository(self.database) as repository:
            while not self.stop.wait(self.interval):
                if isinstance(self.item_id, int):
                    repository.heartbeat(self.item_id)
                else:
                    # Batch members can finish or fail independently while inference continues.
                    placeholders = ','.join('?' for _ in self.item_id)
                    with repository.connection:
                        repository.connection.execute(
                            f"""UPDATE analysis_items SET processing_heartbeat_at=?,updated_at=?
                                WHERE state='processing' AND id IN ({placeholders})""",
                            (utc_now(), utc_now(), *self.item_id),
                        )


@dataclass
class _BatchContext:
    item: AnalysisItem
    backends: tuple
    frames: list[Path] = field(default_factory=list)
    runs: dict[str, int | None] = field(default_factory=dict)
    resources: ExitStack = field(default_factory=ExitStack)


class ImageAnalysisWorker:
    def __init__(
        self,
        repository: ImageAnalysisRepository,
        backends: Sequence[AnalysisBackend],
        *,
        analysis_prefetch: int = 2,
        heartbeat_interval: float = 2.0,
        alias_database: Path | None = None,
        require_wd14: bool = False,
        wd14_batch_size: str = "auto",
        cache_directory: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.repository = repository
        self.backends = tuple(backends)
        self.analysis_prefetch = analysis_prefetch
        self.heartbeat_interval = heartbeat_interval
        self.alias_database = alias_database
        self.require_wd14 = require_wd14
        self.wd14_batch_size = str(wd14_batch_size or "auto")
        self.clock = clock
        if cache_directory is None:
            cache_directory = analysis_cache_directory_for_repository(repository)
        self.sources = ImageSourceService(repository, cache_directory)
        self.last_scheduler_signature: tuple | None = None
        self.completed_since_summary = 0
        self.failed_since_summary = 0
        self.last_processed_count = 0

    def close(self) -> None:
        self.sources.close()

    def log_scheduler_if_changed(self) -> None:
        diagnostic = self.repository.scheduler_diagnostic(self.analysis_prefetch)
        signature = tuple(
            diagnostic[key]
            for key in (
                "pending",
                "processing",
                "ready_ahead",
                "review_active",
                "eligible",
                "interactive",
                "reason",
            )
        ) + tuple(diagnostic["candidate_ids"])
        if signature == self.last_scheduler_signature:
            return
        self.last_scheduler_signature = signature
        print(
            "[DEBUG] [Worker] Scheduler: "
            + "; ".join(
                f"{key}={diagnostic[key]}"
                for key in (
                    "pending",
                    "processing",
                    "ready_ahead",
                    "review_active",
                    "analysis_prefetch",
                    "eligible",
                    "interactive",
                    "reason",
                )
            ),
            flush=True,
        )
        for excluded in diagnostic["exclusions"]:
            print(
                "[DEBUG] [Worker] "
                f"[item:{excluded['id']}] Excluded: "
                f"analysis_requested={excluded['analysis_requested']}; "
                f"source_state={excluded['source_state']}; "
                f"queue_visible={excluded['queue_visible']}",
                flush=True,
            )

    def process_one(self) -> bool:
        self.last_processed_count = 0
        item = self.repository.claim_next(self.analysis_prefetch)
        if item is None:
            self.log_scheduler_if_changed()
            return False
        self.last_scheduler_signature = None
        self.last_processed_count = 1
        print(f"[DEBUG] [Worker] [item:{item.id}] Item claimed", flush=True)
        run_id: int | None = None
        source_context = None
        phase = "backend_selection"
        try:
            active_backends = ", ".join(
                backend.identity.backend for backend in self.backends
            ) or "none"
            print(
                f"[DEBUG] [Worker] [item:{item.id}] Active backends: {active_backends}",
                flush=True,
            )
            item_site = item.source.site
            selected_backends = tuple(
                backend for backend in self.backends
                if not hasattr(backend, "supported_sites") or item_site in backend.supported_sites
            )
            if item_site == "e621" and not any(
                backend.identity.backend == "hydra" for backend in selected_backends
            ):
                raise RuntimeError("Hydra is required for e621 but no Hydra backend is active")
            if item_site != "e621" and self.require_wd14 and not any(
                backend.identity.backend == "wd14" for backend in self.backends
            ):
                raise RuntimeError("WD14 was required but no WD14 backend is active")
            phase = "source_materialization"
            source_context = self.sources.materialize_item(item)
            path = source_context.__enter__()
            last_heartbeat = self.clock()
            for backend in selected_backends:
                identity = backend.identity
                name = getattr(identity, "name", getattr(identity, "model", ""))
                config_hash = getattr(identity, "configuration_hash", getattr(identity, "key", ""))
                device = getattr(identity, "device", getattr(backend, "device", ""))
                phase = "begin_model_run"
                run_id = self.repository.begin_model_run(
                    item.id,
                    identity.backend,
                    name,
                    identity.version,
                    config_hash,
                    str(getattr(backend, "runtime", "")),
                    device,
                )
                if run_id is None:
                    print(
                        f"[DEBUG] [{name}] [item:{item.id}] Cached run reused",
                        flush=True,
                    )
                    continue
                started = self.clock()
                print(
                    f"[DEBUG] [{name}] [item:{item.id}] "
                    f"Backend started; device={device or 'default'}",
                    flush=True,
                )
                self.repository.heartbeat(item.id)
                phase = "representative_frames"
                frames, temporary, media_log = representative_frames(path)
                if media_log:
                    print(f"[INFO] [Worker] [item:{item.id}] {media_log}", flush=True)
                try:
                    # WD14 has a stable tag-confidence merge.  Other backends
                    # preserve their existing one-image result contract.
                    analysis_frames = frames if identity.backend == "wd14" else frames[:1]
                    phase = f"{identity.backend}_inference"
                    with ItemHeartbeat(self.repository.path, item.id, self.heartbeat_interval):
                        if identity.backend == "wd14" and len(analysis_frames) > 1:
                            results = list(backend.analyze_batch(
                                analysis_frames,
                                batch_size=resolve_wd14_batch_size(
                                    self.wd14_batch_size, backend.provider
                                ),
                            ))
                        else:
                            results = [backend.analyze(frame) for frame in analysis_frames]
                finally:
                    if temporary is not None:
                        temporary.cleanup()
                phase = f"save_{identity.backend}_results"
                result = _merge_results(results)
                if isinstance(result, ColorStatistics):
                    self.repository.save_statistics(item.id, run_id, result)
                elif isinstance(result, WD14Result):
                    predictions = [
                        (tag.raw_name, tag.category, tag.score) for tag in result.predictions
                    ]
                    self.repository.save_tag_predictions(
                        item.id,
                        run_id,
                        predictions,
                        canonicalize=wd14_canonicalizer(
                            item.source.site, self.alias_database
                        ),
                    )
                    print(
                        f"[DEBUG] [WD14] [item:{item.id}] {len(predictions)} observations stored",
                        flush=True,
                    )
                elif isinstance(result, HydraResult):
                    predictions = [(tag.raw_name, tag.category, tag.score) for tag in result.predictions]
                    self.repository.save_tag_predictions(
                        item.id, run_id, predictions, source=ObservationSource.HYDRA
                    )
                    print(
                        f"[DEBUG] [Hydra] [item:{item.id}] {len(predictions)} e621 observations stored",
                        flush=True,
                    )
                elif isinstance(result, EmbeddingResult):
                    from array import array

                    values = array("f", result.vector)
                    self.repository.save_embedding(
                        item.id, run_id, values.tobytes(), len(values), "float32", True
                    )
                else:
                    raise TypeError(f"unsupported analysis result: {type(result).__name__}")
                run_id = None
                print(
                    f"[DEBUG] [{name}] [item:{item.id}] "
                    f"Backend finished in {(self.clock() - started) * 1000:.0f} ms",
                    flush=True,
                )
                if self.clock() - last_heartbeat >= self.heartbeat_interval:
                    self.repository.heartbeat(item.id)
                    last_heartbeat = self.clock()
            phase = "ready_transition"
            self.repository.transition(item.id, AnalysisState.READY_FOR_REVIEW)
            self.completed_since_summary += 1
            print(f"[DEBUG] [Worker] [item:{item.id}] Item ready", flush=True)
            if self.completed_since_summary % 100 == 0:
                print(
                    f"[INFO] [Worker] WD14: {self.completed_since_summary} images processed; "
                    f"{self.failed_since_summary} errors",
                    flush=True,
                )
        except Exception as exc:
            self._fail_item(item, {"active": run_id}, exc, batch_ids=(item.id,), phase=phase)
            if isinstance(exc, WD14OutOfMemoryError):
                print(f"[ERROR] [WD14] [item:{item.id}] OOM: {exc}", flush=True)
                raise
        finally:
            if source_context is not None:
                source_context.__exit__(None, None, None)
        return True

    def _fail_item(self, item, runs, exc, *, batch_ids, phase) -> None:
        """Finish every claimed failure, including failures before a context is prepared."""
        message = f"{type(exc).__name__}: {exc}"
        for run_id in runs.values():
            if run_id is not None:
                self.repository.fail_model_run(run_id, message)
        current = self.repository.get_item(item.id)
        if isinstance(exc, ImageCacheMissError) and self.repository.recover_missing_source(item.id, message):
            print(
                f"[WARNING] [Worker] [item:{item.id}] Source recovery requested: {message}; "
                f"batch={batch_ids}; post={item.source.site}:{item.source.post_id}; "
                f"cache_key={item.content_sha256}; phase={phase}; "
                "automatic_retry=source_reacquisition_once", flush=True,
            )
            for line in traceback.format_exception(exc):
                for detail in line.rstrip().splitlines():
                    print(f"[WARNING] [WorkerTraceback] {detail}", flush=True)
            return
        if current and current.state is AnalysisState.PROCESSING:
            self.repository.transition(item.id, AnalysisState.FAILED, message)
        self.failed_since_summary += 1
        print(
            f"[ERROR] [Worker] [item:{item.id}] Item failed: {message}; "
            f"batch={batch_ids}; post={item.source.site}:{item.source.post_id}; "
            f"source={'path' if item.cached_path else 'blob'}; "
            f"cache_key={item.content_sha256 or 'none'}; phase={phase}; "
            "automatic_retry=none (manual retry required)",
            flush=True,
        )
        for line in traceback.format_exception(exc):
            for detail in line.rstrip().splitlines():
                print(f"[ERROR] [WorkerTraceback] {detail}", flush=True)

    def process_batch(self, *, max_items: int | None = None) -> bool:
        """Process each claim independently; preserve frame/result ownership in one batch."""
        self.last_processed_count = 0
        first = self.repository.claim_next(self.analysis_prefetch)
        if first is None:
            self.log_scheduler_if_changed()
            return False
        self.last_scheduler_signature = None
        backend = next((value for value in self.backends
                        if getattr(value, "identity", None)
                        and value.identity.backend == "wd14"), None)
        if backend is None:
            self.last_processed_count = 1
            return self._process_claimed_fallback(first)
        requested = resolve_wd14_batch_size(self.wd14_batch_size, backend.provider)
        priority = self.repository.connection.execute(
            "SELECT priority FROM analysis_items WHERE id=?", (first.id,)
        ).fetchone()[0]
        if priority >= 50:
            requested = 1
        if max_items is not None:
            requested = min(requested, max(1, max_items))
        items = [first]
        while len(items) < requested:
            item = self.repository.claim_next(None)
            if item is None:
                break
            items.append(item)
        self.last_processed_count = len(items)
        batch_ids = tuple(item.id for item in items)
        contexts = []
        # Keep all claims alive during preprocessing and inference, including long batches.
        with (
            ItemHeartbeat(self.repository.path, batch_ids, self.heartbeat_interval),
            ExitStack() as resources,
        ):
            for item in items:
                context = _BatchContext(item, tuple(
                    value for value in self.backends
                    if not hasattr(value, "supported_sites")
                    or item.source.site in value.supported_sites
                ))
                resources.enter_context(context.resources)
                phase = "source_materialization"
                try:
                    path = context.resources.enter_context(self.sources.materialize_item(item))
                    phase = "representative_frames"
                    frames, temporary, media_log = representative_frames(path)
                    context.frames = frames
                    if temporary is not None:
                        context.resources.callback(temporary.cleanup)
                    if media_log:
                        print(f"[INFO] [Worker] [item:{item.id}] {media_log}", flush=True)
                    phase = "begin_model_runs"
                    for value in context.backends:
                        identity = value.identity
                        context.runs[identity.backend] = self.repository.begin_model_run(
                            item.id, identity.backend, identity.name, identity.version,
                            identity.configuration_hash, str(getattr(value, "runtime", "")),
                            getattr(identity, "device", getattr(value, "device", "")),
                        )
                    contexts.append(context)
                except Exception as exc:  # noqa: BLE001 - per-item preparation boundary
                    self._fail_item(item, context.runs, exc, batch_ids=batch_ids, phase=phase)
                    context.resources.close()
            wd14_contexts = [context for context in contexts
                             if context.runs.get("wd14") is not None]
            paths = [frame for context in wd14_contexts for frame in context.frames]
            results = []
            if paths:
                try:
                    results = list(backend.analyze_batch(paths, batch_size=requested))
                    if len(results) != len(paths):
                        raise ValueError(
                            f"WD14 result count mismatch: paths={len(paths)} results={len(results)}"
                        )
                except Exception as exc:  # noqa: BLE001 - bounded one-image fallback
                    print(
                        f"[WARNING] [WD14] Batch fallback to per-image analysis: "
                        f"{type(exc).__name__}: {exc}; batch={batch_ids}; phase=wd14_inference",
                        flush=True,
                    )
                    for detail in traceback.format_exception(exc):
                        print(f"[ERROR] [WorkerTraceback] {detail.rstrip()}", flush=True)
                    results = []
                    for path in paths:
                        try:
                            results.append(backend.analyze(path))
                        except Exception as image_exc:  # noqa: BLE001 - per-frame boundary
                            results.append(image_exc)
                metrics = getattr(backend, "last_batch_metrics", {})
                print(
                    "[INFO] [WD14] Batch "
                    f"provider={metrics.get('provider', backend.provider)} "
                    f"requested={requested} used={metrics.get('batch_size', len(paths))} "
                    f"images={len(paths)} preprocess_ms={metrics.get('preprocess_ms', 0):.1f} "
                    f"session_run_ms={metrics.get('session_run_ms', 0):.1f} "
                    f"postprocess_ms={metrics.get('postprocess_ms', 0):.1f} "
                    f"total_ms={metrics.get('total_ms', 0):.1f}", flush=True,
                )
            # Consume exactly each context's frame count, even when that item fails.
            # Failed items must never shift the next item's result association.
            result_index = 0
            for context in contexts:
                item, runs = context.item, context.runs
                phase = "wd14_results"
                try:
                    if runs.get("wd14") is not None:
                        frame_count = len(context.frames)
                        group = results[result_index:result_index + frame_count]
                        result_index += frame_count
                        if any(isinstance(value, Exception) for value in group):
                            raise next(value for value in group if isinstance(value, Exception))
                        result = _merge_results(group)
                        phase = "save_wd14_results"
                        self.repository.save_tag_predictions(
                            item.id, runs["wd14"],
                            [(tag.raw_name, tag.category, tag.score) for tag in result.predictions],
                            canonicalize=wd14_canonicalizer(item.source.site, self.alias_database),
                        )
                        self.repository.complete_model_run(runs["wd14"])
                        runs["wd14"] = None
                    for value in context.backends:
                        if value.identity.backend == "wd14":
                            continue
                        run_id = runs[value.identity.backend]
                        if run_id is None:
                            continue
                        phase = f"{value.identity.backend}_inference"
                        result = value.analyze(context.frames[0])
                        phase = f"save_{value.identity.backend}_results"
                        if isinstance(result, ColorStatistics):
                            self.repository.save_statistics(item.id, run_id, result)
                        self.repository.complete_model_run(run_id)
                        runs[value.identity.backend] = None
                    phase = "ready_transition"
                    self.repository.transition(item.id, AnalysisState.READY_FOR_REVIEW)
                    self.completed_since_summary += 1
                    print(f"[DEBUG] [Worker] [item:{item.id}] Item ready", flush=True)
                except Exception as exc:  # noqa: BLE001 - per-item result boundary
                    self._fail_item(item, runs, exc, batch_ids=batch_ids, phase=phase)
        return True

    def _process_claimed_fallback(self, item) -> bool:
        """Keep the legacy single-item path when WD14 is not active."""
        # A non-WD14 worker cannot safely be re-claimed through process_one().
        # Its normal callers use process_one directly; this defensive path only
        # applies when a batch was requested with an unexpected backend set.
        self.repository.transition(item.id, AnalysisState.FAILED, "WD14 batch backend unavailable")
        return True


def _merge_results(results):
    if not results:
        raise ValueError("no representative frame was available for analysis")
    if len(results) == 1:
        return results[0]
    if all(isinstance(result, WD14Result) for result in results):
        merged = {}
        for result in results:
            for tag in result.predictions:
                previous = merged.get((tag.raw_name, tag.category))
                if previous is None or tag.score > previous.score:
                    merged[(tag.raw_name, tag.category)] = tag
        return WD14Result(
            tuple(sorted(merged.values(), key=lambda tag: (tag.raw_name, tag.category)))
        )
    return results[0]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="BooruFlow image-analysis worker")
    result.add_argument("--database", type=Path, required=True)
    result.add_argument("--gelbooru-alias-database", type=Path)
    result.add_argument("--analysis-prefetch", type=int, default=2)
    result.add_argument("--heartbeat-interval", type=float, default=2.0)
    result.add_argument("--poll-interval", type=float, default=1.0)
    result.add_argument("--parent-pid", type=int, default=0)
    result.add_argument("--session-id", default="")
    result.add_argument("--once", action="store_true")
    result.add_argument("--wd14-enabled", action="store_true")
    result.add_argument("--wd14-model-directory", type=Path)
    result.add_argument("--wd14-model-id", default=DEFAULT_MODEL_ID)
    result.add_argument("--wd14-store-threshold", type=float, default=0.10)
    result.add_argument("--wd14-batch-size", default="auto")
    result.add_argument("--hydra-enabled", action="store_true")
    result.add_argument("--hydra-source-directory", type=Path)
    result.add_argument("--hydra-model-path", type=Path)
    result.add_argument("--hydra-device", default="auto")
    result.add_argument("--hydra-seqlen", type=int, default=256)
    result.add_argument("--worker-recycle-after", type=int, default=100)
    return result


def _watch_parent(
    parent_pid: int,
    stop: threading.Event,
    log: Callable[[str], None],
) -> None:
    """Exit with the exact Windows parent, even if its PID is later reused."""
    if parent_pid <= 0:
        return
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x00100000, False, parent_pid)
        if not handle:
            log(
                f"WARNING parent handle open failed error={ctypes.get_last_error()}; watchdog disabled"
            )
            return
        log("parent handle opened")
        try:
            result = kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
            if result == 0:
                log("parent exit detected")
                os._exit(0)
            if result == 0xFFFFFFFF:
                log(
                    f"WARNING parent wait failed error={ctypes.get_last_error()}; watchdog disabled"
                )
        finally:
            kernel32.CloseHandle(handle)
        return
    while not stop.wait(1.0):
        try:
            os.kill(parent_pid, 0)
        except OSError:
            os._exit(0)


def _watch_stdin(stop: threading.Event) -> None:
    for line in sys.stdin:
        if line.strip().upper() == "STOP":
            stop.set()
            return


def main(
    argv: list[str] | None = None,
    *,
    bootstrap_log: Callable[[str], None] | None = None,
    external_stop: threading.Event | None = None,
    parent_watchdog_started: bool = False,
) -> int:
    args = parser().parse_args(argv)
    trace = bootstrap_log or (lambda _message: None)
    worker_id = args.session_id or uuid4().hex
    stop = external_stop or threading.Event()
    if stop.is_set():
        trace("stop observed before SQLite startup")
        return 0
    trace("SQLite connection begin")
    with ImageAnalysisRepository(args.database) as repository:
        trace("SQLite connected")
        repository.register_worker(worker_id, os.getpid())
        trace("worker session created")
        print(
            f"[INFO] [Worker] Worker started pid={os.getpid()} parent={args.parent_pid} "
            f"session={worker_id} interpreter={sys.executable!r} "
            f"base_interpreter={getattr(sys, '_base_executable', sys.executable)!r}",
            flush=True,
        )
        if stop.is_set():
            repository.stop_worker(worker_id)
            trace("stop observed before backend startup")
            return 0
        backends: list[AnalysisBackend] = [ClassicImageAnalyzer()]
        if args.wd14_enabled:
            if args.wd14_model_directory is None:
                message = "model directory was not provided"
                print(f"WD14_UNAVAILABLE {message}", flush=True)
                trace(f"WD14 unavailable: {message}")
                trace("Worker startup failed code=2")
                repository.stop_worker(worker_id, message)
                return 2
            else:
                trace("WD14 initialization begin")
                trace("WD14 init begin")
                wd14 = WD14Backend(
                    WD14Config(
                        args.wd14_model_directory, args.wd14_model_id, args.wd14_store_threshold
                    )
                )
                try:
                    trace("ONNX/WD14 prepare begin")
                    wd14.prepare(trace)
                    trace("ONNX/WD14 prepare complete")
                    if stop.is_set():
                        repository.stop_worker(worker_id)
                        wd14.close()
                        trace("stop observed after WD14 prepare")
                        return 0
                    trace("WD14 model loaded")
                    trace("WD14 init complete")
                    backends.append(wd14)
                    print(
                        f"[INFO] [WD14] Model loaded; provider={wd14.provider}; "
                        f"device={wd14.device}",
                        flush=True,
                    )
                    print(f"WD14_READY {wd14.provider} {wd14.device} {wd14.runtime}", flush=True)
                    diagnostic = wd14.runtime_diagnostic
                    print(
                        "WD14_RUNTIME "
                        f"ORT={diagnostic.runtime_version}; CUDA={diagnostic.expected_cuda}; "
                        f"cuDNN={diagnostic.expected_cudnn}; "
                        f"runtime={'installed' if diagnostic.cuda_runtime_installed else 'missing'}; "
                        f"cudnn={'installed' if diagnostic.cudnn_installed else 'missing'}; "
                        f"GPU={', '.join(diagnostic.gpu_devices) or 'not detected'}; "
                        f"provider={diagnostic.effective_provider or 'none'}",
                        flush=True,
                    )
                except WD14UnavailableError as exc:
                    print(f"WD14_UNAVAILABLE {exc}", flush=True)
                    trace(f"WD14 model load failed: {exc}")
                    trace(f"WD14 unavailable: {exc}")
                    trace("Worker startup failed code=2")
                    wd14.close()
                    repository.stop_worker(worker_id, str(exc))
                    return 2
        if args.hydra_enabled:
            hydra = HydraBackend(HydraConfig(
                args.hydra_source_directory or Path(),
                args.hydra_model_path or Path(),
                args.hydra_device,
                args.hydra_seqlen,
            ))
            backends.append(hydra)
            print("[INFO] [Hydra] Registered lazily for e621; batch_size=1", flush=True)
        if not parent_watchdog_started:
            trace("watchdog thread start requested")
            threading.Thread(
                target=_watch_parent,
                args=(args.parent_pid, stop, trace),
                daemon=True,
            ).start()
            trace("watchdog initialized non-blocking")
        threading.Thread(target=_watch_stdin, args=(stop,), daemon=True).start()
        trace("cooperative stop watcher started")
        worker = ImageAnalysisWorker(
            repository,
            backends,
            analysis_prefetch=args.analysis_prefetch,
            heartbeat_interval=args.heartbeat_interval,
            alias_database=args.gelbooru_alias_database,
            require_wd14=args.wd14_enabled,
            wd14_batch_size=args.wd14_batch_size,
        )
        print(
            "[INFO] [Worker] Active backends: "
            + ", ".join(
                getattr(
                    getattr(backend, "identity", None),
                    "backend",
                    getattr(backend, "backend", type(backend).__name__),
                )
                for backend in backends
            ),
            flush=True,
        )
        print(f"READY {worker_id} pid={os.getpid()}", flush=True)
        completed = 0
        try:
            while not stop.is_set():
                repository.worker_heartbeat(worker_id)
                use_batch = (
                    args.wd14_enabled
                    and not args.hydra_enabled
                    and str(args.wd14_batch_size).casefold() != "1"
                    and any(getattr(backend, "identity", None)
                            and backend.identity.backend == "wd14" for backend in backends)
                )
                remaining = (
                    args.worker_recycle_after - completed if args.worker_recycle_after > 0 else None
                )
                worked = (
                    worker.process_batch(max_items=remaining) if use_batch else worker.process_one()
                )
                completed += worker.last_processed_count
                if args.once:
                    break
                if args.worker_recycle_after > 0 and completed >= args.worker_recycle_after:
                    print(f"RECYCLE {completed}", flush=True)
                    print(f"[INFO] [Worker] Worker recycled after {completed} items", flush=True)
                    repository.stop_worker(worker_id)
                    return 75
                if not worked:
                    stop.wait(max(0.5, args.poll_interval))
        except KeyboardInterrupt:
            pass
        except WD14OutOfMemoryError as exc:
            repository.stop_worker(worker_id, str(exc))
            print(f"WD14_OOM {exc}", file=sys.stderr, flush=True)
            return 70
        except Exception as exc:  # noqa: BLE001 - process boundary
            repository.stop_worker(worker_id, str(exc))
            print(f"WORKER_ERROR {exc}", file=sys.stderr, flush=True)
            traceback.print_exception(exc, file=sys.stderr)
            return 1
        finally:
            worker.close()
            for backend in backends:
                close = getattr(backend, "close", None)
                if close:
                    close()
        repository.stop_worker(worker_id)
        print(f"[INFO] [Worker] Exited pid={os.getpid()} code=0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
