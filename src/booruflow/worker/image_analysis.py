"""Separate-process worker for modular image-analysis backends."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import uuid4

from booruflow.application.embedding import EmbeddingResult
from booruflow.application.tag_canonicalization import canonicalize_new_gelbooru_tag
from booruflow.domain.image_analysis import (
    AnalysisState,
    ColorStatistics,
    ModelIdentity,
    ObservationSource,
)
from booruflow.infrastructure.classic_image_analysis import ClassicImageAnalyzer
from booruflow.infrastructure.hydra import HydraBackend, HydraConfig, HydraResult
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import ImageSourceService
from booruflow.infrastructure.media_frames import representative_frames
from booruflow.infrastructure.wd14 import (
    DEFAULT_MODEL_ID,
    WD14Backend,
    WD14Config,
    WD14OutOfMemoryError,
    WD14Result,
    WD14UnavailableError,
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

    def __init__(self, database: Path, item_id: int, interval: float) -> None:
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
                repository.heartbeat(self.item_id)


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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.repository = repository
        self.backends = tuple(backends)
        self.analysis_prefetch = analysis_prefetch
        self.heartbeat_interval = heartbeat_interval
        self.alias_database = alias_database
        self.require_wd14 = require_wd14
        self.clock = clock
        self.sources = ImageSourceService(repository, repository.path.parent.parent / "cache")
        self.last_scheduler_signature: tuple | None = None
        self.completed_since_summary = 0
        self.failed_since_summary = 0

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
        item = self.repository.claim_next(self.analysis_prefetch)
        if item is None:
            self.log_scheduler_if_changed()
            return False
        self.last_scheduler_signature = None
        print(f"[DEBUG] [Worker] [item:{item.id}] Item claimed", flush=True)
        run_id: int | None = None
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
            path = self.sources.validate_item_file(item)
            last_heartbeat = self.clock()
            for backend in selected_backends:
                identity = backend.identity
                name = getattr(identity, "name", getattr(identity, "model", ""))
                config_hash = getattr(identity, "configuration_hash", getattr(identity, "key", ""))
                device = getattr(identity, "device", getattr(backend, "device", ""))
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
                frames, temporary, media_log = representative_frames(path)
                if media_log:
                    print(f"[INFO] [Worker] [item:{item.id}] {media_log}", flush=True)
                try:
                    # WD14 has a stable tag-confidence merge.  Other backends
                    # preserve their existing one-image result contract.
                    analysis_frames = frames if identity.backend == "wd14" else frames[:1]
                    with ItemHeartbeat(self.repository.path, item.id, self.heartbeat_interval):
                        results = [backend.analyze(frame) for frame in analysis_frames]
                finally:
                    if temporary is not None:
                        temporary.cleanup()
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
            self.failed_since_summary += 1
            message = str(exc)
            if run_id is not None:
                self.repository.fail_model_run(run_id, message)
            current = self.repository.get_item(item.id)
            if current and current.state is AnalysisState.PROCESSING:
                self.repository.transition(item.id, AnalysisState.FAILED, message)
            else:
                self.repository.fail_source(item.id, message)
            if isinstance(exc, WD14OutOfMemoryError):
                print(f"[ERROR] [WD14] [item:{item.id}] OOM: {exc}", flush=True)
                raise
            print(f"[ERROR] [Worker] [item:{item.id}] Item failed: {exc}", flush=True)
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
                worked = worker.process_one()
                completed += int(worked)
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
            return 1
        repository.stop_worker(worker_id)
        print(f"[INFO] [Worker] Exited pid={os.getpid()} code=0", flush=True)
        for backend in backends:
            close = getattr(backend, "close", None)
            if close:
                close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
