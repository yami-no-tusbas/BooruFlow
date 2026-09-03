"""Qt coordination for the persistent Image Analysis workflow."""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QThread, QTimer, Signal
from PySide6.QtWidgets import QMessageBox

from booruflow.application.database_paths import gelbooru_alias_database, gelbooru_tag_database
from booruflow.application.hydra_model_manager import hydra_directory
from booruflow.application.image_analysis import ImageAnalysisWorkflow, QueuePolicy
from booruflow.domain.image_analysis import DecisionState, InputKind, detect_local_source
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import (
    E621PostProvider,
    GelbooruPostProvider,
    ImageSourceService,
)
from booruflow.infrastructure.tag_category_lookup import LocalTagCategoryLookup
from booruflow.presentation.pyside6.ui_logging import StreamingLogSanitizer, log_event

WORKER_RESTART_DELAYS_MS = (250, 1_000, 5_000)
WORKER_DETERMINISTIC_FAILURE_CODES = frozenset({2})


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _force_stop_pid(pid: int) -> bool:
    """Last-resort stop for the real worker when a venv launcher detached first."""
    if not _pid_is_running(pid):
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x0001 | 0x00100000, False, pid)
        if not handle:
            return False
        try:
            return bool(kernel32.TerminateProcess(handle, 1))
        finally:
            kernel32.CloseHandle(handle)
    import signal

    os.kill(pid, signal.SIGTERM)
    return True


class SourcePreparationWorker(QThread):
    completed = Signal(list, list, dict, list, list)

    def __init__(
        self,
        database: Path,
        cache: Path,
        local_paths: list[Path],
        download_prefetch: int,
        credentials: dict[str, object],
        gelbooru_tag_database: Path | None,
    ) -> None:
        super().__init__()
        self.database = database
        self.cache = cache
        self.local_paths = local_paths
        self.download_prefetch = download_prefetch
        self.credentials = credentials
        self.gelbooru_tag_database = gelbooru_tag_database

    def run(self) -> None:
        errors: list[str] = []
        enrichment_errors: list[str] = []
        outcomes: dict[str, int] = {}
        reused_ids: list[int] = []
        events: list[tuple[str, str, str]] = []
        with ImageAnalysisRepository(self.database) as repository:
            sources = ImageSourceService(repository, self.cache)
            gel = self.credentials.get("gelbooru", {})
            gel = gel if isinstance(gel, dict) else {}
            e621 = self.credentials.get("e621", {})
            e621 = e621 if isinstance(e621, dict) else {}
            lookup = (
                LocalTagCategoryLookup(self.gelbooru_tag_database)
                if self.gelbooru_tag_database
                else None
            )
            providers = {
                "gelbooru": GelbooruPostProvider(
                    str(gel.get("user_id", "")),
                    str(gel.get("api_key", "")),
                    category_lookup=lookup,
                ),
                "e621": E621PostProvider(
                    str(e621.get("user_id", "")), str(e621.get("api_key", ""))
                ),
            }
            for path in self.local_paths:
                try:
                    result = sources.add_local_with_result(path)
                    item_id = result.item_id
                    outcomes[result.outcome] = outcomes.get(result.outcome, 0) + 1
                    if result.outcome != "new":
                        reused_ids.append(item_id)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{path}: {exc}")
                    continue
                detected = detect_local_source(path)
                if detected:
                    try:
                        sources.enrich_local(item_id, detected, providers[detected.site])
                    except Exception as exc:  # noqa: BLE001 - optional metadata boundary
                        repository.fail_local_enrichment(item_id, str(exc))
                        enrichment_errors.append(f"{detected.site} #{detected.post_id}: {exc}")
            for item_id in repository.claim_sources_to_resolve(self.download_prefetch):
                item = repository.get_item(item_id)
                context = f"{item.source.site}:{item.source.post_id} item:{item_id}"
                events.append(("INFO", context, "Metadata request started"))
                try:
                    sources.resolve_post(
                        item_id, providers[item.source.site], str(item.source.post_id)
                    )
                    canonical = repository.item_by_remote_source(
                        str(item.source.site), str(item.source.post_id)
                    )
                    canonical_id = canonical.id if canonical else item_id
                    message = "Metadata and image resolved"
                    if canonical_id != item_id:
                        message += f"; deduplicated to item:{canonical_id}"
                    events.append(("INFO", context, message))
                except Exception as exc:  # noqa: BLE001 - per-source failure boundary
                    repository.fail_source(item_id, str(exc))
                    errors.append(str(exc))
                    events.append(("ERROR", context, f"Metadata/source failed: {exc}"))
        self.completed.emit(errors, enrichment_errors, outcomes, reused_ids, events)


class DroppedSourceScanWorker(QThread):
    completed = Signal(list, int)

    def __init__(self, sources: list[Path]) -> None:
        super().__init__()
        self.sources = sources

    def run(self) -> None:
        from PIL import Image

        extensions = {value.casefold() for value in Image.registered_extensions()}
        result: list[str] = []
        seen: set[str] = set()
        ignored = 0

        def append(path: Path) -> None:
            nonlocal ignored
            try:
                key = os.path.normcase(str(path.resolve(strict=False)))
            except OSError:
                key = os.path.normcase(str(path.absolute()))
            if key in seen:
                ignored += 1
                return
            seen.add(key)
            result.append(str(path))

        for source in self.sources:
            if source.is_dir():
                try:
                    for directory, subdirectories, files in os.walk(source):
                        subdirectories.sort(key=str.casefold)
                        for name in sorted(files, key=str.casefold):
                            path = Path(directory) / name
                            if path.suffix.casefold() in extensions:
                                append(path)
                            else:
                                ignored += 1
                except OSError:
                    ignored += 1
            elif source.is_file():
                append(source)
            else:
                ignored += 1
        self.completed.emit(result, ignored)


class ImageAnalysisController(QObject):
    worker_state_changed = Signal(str, str)

    def __init__(
        self,
        project_root: Path,
        python_executable: str,
        page,
        settings: dict[str, object],
        credentials: Callable[[], dict[str, object]],
        log: Callable[[str], None],
        auto_start_worker: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.python_executable = python_executable
        self.page = page
        self.settings = settings
        self.credentials = credentials
        self.log = log
        self.database = project_root / "var" / "state" / "image_analysis.sqlite"
        self.cache = project_root / "var" / "cache" / "image_analysis"
        self.repository = ImageAnalysisRepository(self.database)
        self.policy = QueuePolicy(
            int(settings.get("image_analysis_download_prefetch", 10)),
            int(settings.get("image_analysis_analysis_prefetch", 2)),
        )
        self.sources = ImageSourceService(self.repository, self.cache)
        self.workflow = ImageAnalysisWorkflow(self.repository, self.sources, self.policy)
        self.stale_seconds = int(settings.get("image_analysis_worker_stale_timeout", 15))
        stale = (datetime.now(UTC) - timedelta(seconds=self.stale_seconds)).isoformat(
            timespec="seconds"
        )
        self.repository.recover_interrupted(stale)
        self.current = None
        self.pending_local_paths: list[Path] = []
        self.source_worker: SourcePreparationWorker | None = None
        self.drop_scan_worker: DroppedSourceScanWorker | None = None
        self.drop_ignored = 0
        self.drop_expected = 0
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._worker_output)
        self.process.finished.connect(self._worker_finished)
        self.process.started.connect(self._worker_started)
        self.process.errorOccurred.connect(self._worker_error)
        self._worker_session_id = ""
        self._restart_worker_on_exit = False
        self._restart_attempt = 0
        self._restart_reason = ""
        self._launcher_pid = 0
        self._worker_pid = 0
        self.worker_startup_state = "stopped"
        self.worker_startup_detail = ""
        self.worker_startup_timer = QTimer(self)
        self.worker_startup_timer.setSingleShot(True)
        self.worker_startup_timer.timeout.connect(self._worker_startup_timed_out)
        self.worker_restart_timer = QTimer(self)
        self.worker_restart_timer.setSingleShot(True)
        self.worker_restart_timer.timeout.connect(self._restart_worker_now)
        self._shutting_down = False
        self._last_queue_signature: tuple | None = None
        self._page_active = True
        self._page_dirty = False
        self._slow_refresh_last = 0.0
        self._slow_refresh_samples: list[float] = []
        self.worker_sanitizer = StreamingLogSanitizer()
        self.worker_text_buffer = ""
        self.model_process = QProcess(self)
        self.model_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.model_process.readyReadStandardOutput.connect(self._model_output)
        self.model_process.finished.connect(self._model_finished)
        self._install_operation = ""
        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        page.local_files_requested.connect(self.add_local_files)
        page.local_sources_dropped.connect(self.scan_dropped_sources)
        page.remote_ids_requested.connect(self.add_remote_ids)
        page.complete_requested.connect(self.complete)
        page.skip_requested.connect(self.skip)
        page.retry_requested.connect(self.retry)
        page.manual_tag_requested.connect(self.add_manual_tag)
        page.observation_decision_requested.connect(self.decide)
        page.bulk_observation_decision_requested.connect(self.decide_many)
        page.wd14_install_requested.connect(self.install_wd14)
        page.gpu_runtime_install_requested.connect(self.install_gpu_runtime)
        page.item_open_requested.connect(self.open_item)
        page.queue_navigation_requested.connect(self.open_item)
        page.item_requeue_requested.connect(self.requeue_item)
        page.queue_filter_changed.connect(self.queue_filter_changed)
        page.queue_cleanup_requested.connect(self.clean_queue)
        page.set_display_threshold(
            float(settings.get("image_analysis_wd14_display_threshold", 0.30))
        )
        if auto_start_worker:
            self.start_worker()
        else:
            self.page.worker_state.setText(self.page.catalog.text("image_analysis.worker_stopped"))
        self.refresh()

    def apply_settings(self, settings: dict[str, object]) -> None:
        """Apply saved analysis settings to this live controller and worker."""
        restart_keys = {
            "image_analysis_wd14_enabled", "image_analysis_wd14_model_directory",
            "image_analysis_wd14_model_id", "image_analysis_wd14_store_threshold",
            "image_analysis_worker_heartbeat_interval", "image_analysis_worker_recycle_after",
            "gelbooru_tag_database", "gelbooru_alias_database",
            "image_analysis_hydra_enabled", "image_analysis_hydra_source_directory",
            "image_analysis_hydra_model_path", "image_analysis_hydra_device",
            "image_analysis_hydra_seqlen",
        }
        restart = any(self.settings.get(key) != settings.get(key) for key in restart_keys)
        self.settings = settings
        self.policy = QueuePolicy(
            int(settings.get("image_analysis_download_prefetch", 10)),
            int(settings.get("image_analysis_analysis_prefetch", 2)),
        )
        self.workflow.policy = self.policy
        self.stale_seconds = int(settings.get("image_analysis_worker_stale_timeout", 15))
        self.page.set_display_threshold(
            float(settings.get("image_analysis_wd14_display_threshold", 0.30))
        )
        if not restart or self._shutting_down:
            return
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self.start_worker()
            return
        self._restart_worker_on_exit = True
        self._set_worker_state("stopping", "ImageAnalysis stopping for settings update")
        self.process.write(b"STOP\n")

    def add_local_files(self, paths: list[str]) -> None:
        self.pending_local_paths.extend(Path(path) for path in paths)
        self._start_source_preparation()

    def scan_dropped_sources(self, paths: list[str]) -> None:
        if self.drop_scan_worker and self.drop_scan_worker.isRunning():
            self.log("Image Analysis: a dropped-folder scan is already running")
            return
        self.page.drop_status.setText("Recherche des images déposées…")
        self.drop_scan_worker = DroppedSourceScanWorker([Path(path) for path in paths])
        self.drop_scan_worker.completed.connect(self._drop_scan_finished)
        self.drop_scan_worker.start()

    def _drop_scan_finished(self, paths: list[str], ignored: int) -> None:
        if self.drop_scan_worker:
            self.drop_scan_worker.deleteLater()
            self.drop_scan_worker = None
        threshold = int(self.settings.get("image_analysis_drop_confirmation_threshold", 250))
        if len(paths) > threshold:
            answer = QMessageBox.question(
                self.page,
                "Ajouter les images",
                f"{len(paths)} images détectées.\nLes ajouter à Image Analysis ?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.page.drop_status.setText("Import annulé")
                return
        self.drop_ignored = ignored
        self.drop_expected = len(paths)
        if not paths:
            self.page.drop_status.setText(f"Aucune image détectée · {ignored} ignorée(s)")
            return
        self.page.drop_status.setText(f"Import de {len(paths)} image(s)…")
        self.add_local_files(paths)

    def add_remote_ids(self, site: str, post_ids: list[str], *, priority: int = 0) -> list[int]:
        kind = InputKind.GELBOORU_POST if site == "gelbooru" else InputKind.E621_POST
        ids: list[int] = []
        try:
            ids = self.workflow.add_remote_ids(kind, post_ids, priority=priority)
            for post_id, item_id in zip(post_ids, ids, strict=False):
                self.log(
                    log_event(
                        "ImageAnalysis",
                        "Remote source queued",
                        context=f"{site}:{post_id} item:{item_id}",
                    )
                )
        except (ValueError, sqlite3.IntegrityError) as exc:
            self.log(log_event("ImageAnalysis", str(exc), level="ERROR"))
        self._start_source_preparation()
        if priority >= 100:
            self.ensure_worker_available("interactive Tagging request")
        self.refresh()
        return ids

    def ensure_worker_available(self, reason: str) -> None:
        """Start or recover the worker for an interactive analysis request."""
        if self._shutting_down or self.worker_startup_state == "ready":
            return
        if self.worker_startup_state == "unavailable":
            self.log(log_event("Worker", f"Unavailable; cannot recover for {reason}", level="WARNING"))
            return
        self._restart_reason = reason
        if self.process.state() == QProcess.ProcessState.NotRunning:
            if self.worker_restart_timer.isActive():
                return
            self._schedule_worker_restart(reason, immediate=True)

    def reanalyze_item(self, item_id: int) -> None:
        """Queue one existing item for a fresh run with the current worker settings."""
        self.repository.reanalyze(item_id, priority=100)
        self.ensure_worker_available("manual Tagging re-analysis")
        self.refresh()

    def _start_source_preparation(self) -> None:
        if self.source_worker and self.source_worker.isRunning():
            self.log(log_event("ImageAnalysis", "Source preparation already active", level="DEBUG"))
            return
        local, self.pending_local_paths = self.pending_local_paths, []
        gel_path = gelbooru_tag_database(self.settings)
        self.source_worker = SourcePreparationWorker(
            self.database,
            self.cache,
            local,
            self.policy.download_prefetch,
            self.credentials(),
            gel_path,
        )
        self.source_worker.completed.connect(self._sources_finished)
        self.source_worker.start()
        self.log(log_event("ImageAnalysis", "Metadata/source preparation started"))

    def _sources_finished(
        self,
        errors: list[str],
        enrichment_errors: list[str],
        outcomes: dict[str, int],
        reused_ids: list[int],
        events: list[tuple[str, str, str]],
    ) -> None:
        for level, context, message in events:
            self.log(log_event("Metadata", message, level=level, context=context))
        for error in errors:
            self.log(log_event("ImageAnalysis", error, level="ERROR"))
        for error in enrichment_errors:
            self.log(log_event("Metadata", error, level="WARNING"))
        if self.source_worker:
            self.source_worker.deleteLater()
            self.source_worker = None
        if self.pending_local_paths or self.repository.queue_counts().get("unresolved", 0):
            self._start_source_preparation()
        elif self.drop_expected:
            invalid = len(errors)
            new = outcomes.get("new", 0)
            queued = outcomes.get("already_queued", 0)
            known = sum(count for name, count in outcomes.items() if name.startswith("known_"))
            self.page.drop_status.setText(
                f"{self.drop_expected} fichier(s) déposé(s) · {new} nouvelle(s) · "
                f"{known} déjà connue(s) · {queued} déjà dans la file · "
                f"{self.drop_ignored} doublon(s) de chemin · {invalid} invalide(s)"
            )
            self.drop_expected = 0
            self.drop_ignored = 0
        self.refresh()
        self.log(log_event("ImageAnalysis", f"Source preparation finished; errors={len(errors)}"))
        if len(reused_ids) == 1:
            self.open_item(reused_ids[0])
            state = self.current.state.value if self.current else "known"
            message = (
                "Image déjà connue — analyse et revue réutilisées"
                if state == "reviewed"
                else "Image déjà connue — actuellement ignorée"
                if state == "skipped"
                else "Image déjà présente dans la file"
            )
            self.page.drop_status.setText(message)
        if self.current:
            self._show_current()

    def start_worker(self) -> None:
        if self._shutting_down or self.process.state() != QProcess.ProcessState.NotRunning:
            return
        self._worker_session_id = uuid4().hex
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        environment.insert("NO_COLOR", "1")
        environment.insert("FORCE_COLOR", "0")
        environment.insert("TERM", "dumb")
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(self.project_root))
        arguments = [
            "-u",
            "-m",
            "booruflow.worker.image_analysis_bootstrap",
            "--database",
            str(self.database),
            "--analysis-prefetch",
            str(self.policy.analysis_prefetch),
            "--heartbeat-interval",
            str(self.settings.get("image_analysis_worker_heartbeat_interval", 2)),
            "--worker-recycle-after",
            str(self.settings.get("image_analysis_worker_recycle_after", 100)),
            "--poll-interval",
            "1.0",
            "--parent-pid",
            str(os.getpid()),
            "--session-id",
            self._worker_session_id,
        ]
        alias_database = gelbooru_alias_database(self.settings)
        if alias_database:
            arguments.extend(["--gelbooru-alias-database", str(alias_database)])
        if bool(self.settings.get("image_analysis_wd14_enabled", True)):
            model_directory = self.settings.get(
                "image_analysis_wd14_model_directory",
                self.project_root / "var" / "models" / "image_analysis" / "wd-vit-tagger-v3",
            )
            arguments.extend(
                [
                    "--wd14-enabled",
                    "--wd14-model-directory",
                    str(model_directory),
                    "--wd14-model-id",
                    str(
                        self.settings.get(
                            "image_analysis_wd14_model_id", "SmilingWolf/wd-vit-tagger-v3"
                        )
                    ),
                    "--wd14-store-threshold",
                    str(self.settings.get("image_analysis_wd14_store_threshold", 0.10)),
                ]
            )
        if bool(self.settings.get("image_analysis_hydra_enabled", False)):
            source_directory = self.settings.get(
                "image_analysis_hydra_source_directory",
                hydra_directory(self.project_root),
            )
            model_path = self.settings.get(
                "image_analysis_hydra_model_path",
                Path(source_directory) / "hydra-3.5.safetensors",
            )
            arguments.extend([
                "--hydra-enabled", "--hydra-source-directory", str(source_directory),
                "--hydra-model-path", str(model_path),
                "--hydra-device", str(self.settings.get("image_analysis_hydra_device", "auto")),
                "--hydra-seqlen", str(self.settings.get("image_analysis_hydra_seqlen", 256)),
            ])
        self.process.start(self.python_executable, arguments)
        self._set_worker_state("starting", "ImageAnalysis starting")
        self.log(
            log_event(
                "Worker", f"ImageAnalysis worker starting interpreter={self.python_executable!r}"
            )
        )
        self.page.worker_state.setText(self.page.catalog.text("image_analysis.worker_starting"))

    def _worker_started(self) -> None:
        self._launcher_pid = int(self.process.processId())
        timeout_ms = max(
            1000,
            int(self.settings.get("image_analysis_worker_startup_timeout_ms", 30_000)),
        )
        self.worker_startup_timer.start(timeout_ms)
        self.log(
            log_event(
                "Worker",
                f"Launcher started pid={self._launcher_pid}; parent={os.getpid()}; "
                f"session={self._worker_session_id}",
            )
        )

    def _worker_output(self) -> None:
        chunk = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.worker_text_buffer += self.worker_sanitizer.feed(chunk)
        lines = self.worker_text_buffer.split("\n")
        self.worker_text_buffer = lines.pop()
        for line in lines:
            self._handle_worker_line(line)

    def _handle_worker_line(self, line: str) -> None:
        clean = line.strip()
        if not clean:
            return
        if clean.startswith("BOOTSTRAP WD14 init begin"):
            self._set_worker_state("initializing", "ImageAnalysis WD14 initialization")
            self.page.worker_state.setText(self.page.catalog.text("image_analysis.worker_initializing"))
        if clean.startswith("READY "):
            marker = clean.rpartition("pid=")[2]
            if marker.isdigit():
                self._worker_pid = int(marker)
            self.page.worker_state.setText(self.page.catalog.text("image_analysis.worker_active"))
            self.worker_startup_timer.stop()
            self._restart_attempt = 0
            self._restart_reason = ""
            self._set_worker_state("ready", "ImageAnalysis ready")
            self.log(log_event("Worker", f"Ready pid={self._worker_pid}"))
        if clean.startswith("WD14_RUNTIME "):
            self.page.wd14_state.setText(clean.removeprefix("WD14_RUNTIME "))
        elif clean.startswith(("WD14_READY ", "WD14_UNAVAILABLE ", "WD14_OOM ")):
            self.page.wd14_state.setText(clean)
        protocol_prefixes = (
            "READY ",
            "WD14_RUNTIME ",
            "WD14_READY ",
            "WD14_UNAVAILABLE ",
            "WD14_OOM ",
            "RECYCLE ",
        )
        if clean.startswith("["):
            self.log(clean)
        elif not clean.startswith(protocol_prefixes):
            self.log(log_event("Worker", clean, level="DEBUG"))

    def _worker_finished(self, code: int, _status) -> None:
        self.worker_startup_timer.stop()
        self.worker_sanitizer.flush()
        if self.worker_text_buffer.strip():
            self._handle_worker_line(self.worker_text_buffer)
        self.worker_text_buffer = ""
        state_before_exit = self.worker_startup_state
        expected_stop = self._shutting_down or state_before_exit == "stopping"
        key = "image_analysis.worker_stopped" if expected_stop or code == 75 else "image_analysis.worker_error"
        self.page.worker_state.setText(self.page.catalog.text(key))
        level = "INFO" if expected_stop or code == 75 else "ERROR"
        old_pid = self._worker_pid
        old_launcher_pid = self._launcher_pid
        self._worker_pid = 0
        self._launcher_pid = 0
        event = "Worker recycle normal" if code == 75 else "Exited"
        self.log(
            log_event(
                "Worker",
                f"{event} pid={old_pid or 'not-ready'} launcher_pid={old_launcher_pid} code={code}",
                level=level,
            )
        )
        restart = self._restart_worker_on_exit
        self._restart_worker_on_exit = False
        if restart and not self._shutting_down:
            self._schedule_worker_restart("settings changed", immediate=True)
        if code == 75 and not self._shutting_down:
            self.log(log_event("Worker", f"Old worker pid={old_pid} confirmed terminated"))
            self._schedule_worker_restart("normal recycle", immediate=True)
        elif not expected_stop and not self._shutting_down and code in WORKER_DETERMINISTIC_FAILURE_CODES:
            detail = f"ImageAnalysis unavailable (exit code {code})"
            self._set_worker_state("unavailable", detail)
            self.page.worker_state.setText(self.page.catalog.text("image_analysis.worker_unavailable"))
            self.log(log_event("Worker", f"Deterministic startup failure; no retry: {detail}", level="ERROR"))
        elif not expected_stop and not self._shutting_down:
            detail = f"ImageAnalysis exited before Ready (exit code {code})"
            self._schedule_worker_restart(detail, immediate=False)

    def _schedule_worker_restart(self, reason: str, *, immediate: bool) -> None:
        if self._shutting_down or self.worker_restart_timer.isActive():
            return
        if not immediate and self._restart_attempt >= len(WORKER_RESTART_DELAYS_MS):
            detail = f"ImageAnalysis unavailable after {self._restart_attempt} restart attempts: {reason}"
            self._set_worker_state("unavailable", detail)
            self.page.worker_state.setText(self.page.catalog.text("image_analysis.worker_unavailable"))
            self.log(log_event("Worker", detail, level="ERROR"))
            return
        delay = 0 if immediate else WORKER_RESTART_DELAYS_MS[self._restart_attempt]
        if not immediate:
            self._restart_attempt += 1
        self._restart_reason = reason
        self._set_worker_state("restarting", f"ImageAnalysis restarting: {reason}")
        self.page.worker_state.setText(self.page.catalog.text("image_analysis.worker_restarting"))
        self.log(
            log_event(
                "Worker",
                f"Restart scheduled in {delay} ms; attempt={self._restart_attempt}; reason={reason}",
                level="WARNING",
            )
        )
        self.worker_restart_timer.start(delay)

    def _restart_worker_now(self) -> None:
        if self._shutting_down or self.process.state() != QProcess.ProcessState.NotRunning:
            return
        self.start_worker()

    def _set_worker_state(self, state: str, detail: str = "") -> None:
        if state == self.worker_startup_state and detail == self.worker_startup_detail:
            return
        self.worker_startup_state = state
        self.worker_startup_detail = detail
        self.worker_state_changed.emit(state, detail)

    def _worker_error(self, _error) -> None:
        if self._shutting_down or self.worker_startup_state == "startup_timeout":
            return
        self.worker_startup_timer.stop()
        detail = f"ImageAnalysis failed to start: {self.process.errorString()}"
        self._set_worker_state("failed", detail)
        self.page.worker_state.setText(self.page.catalog.text("image_analysis.worker_error"))
        self.log(log_event("Worker", detail, level="ERROR"))

    def _worker_startup_timed_out(self) -> None:
        if self._shutting_down or self.worker_startup_state not in {"starting", "initializing"}:
            return
        timeout_ms = int(self.settings.get("image_analysis_worker_startup_timeout_ms", 30_000))
        detail = f"ImageAnalysis startup timeout after {timeout_ms / 1000:g} s"
        self._set_worker_state("startup_timeout", detail)
        self.page.worker_state.setText("ImageAnalysis : délai de démarrage dépassé")
        self.log(log_event("Worker", detail, level="ERROR"))
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.write(b"STOP\n")
            self.process.waitForBytesWritten(500)
            QTimer.singleShot(5000, self._terminate_timed_out_worker)

    def _terminate_timed_out_worker(self) -> None:
        if (
            self.worker_startup_state == "startup_timeout"
            and self.process.state() != QProcess.ProcessState.NotRunning
        ):
            self.log(
                log_event(
                    "Worker",
                    "Startup-timeout cooperative stop did not finish; terminating",
                    level="WARNING",
                )
            )
            self.process.terminate()

    def install_wd14(self) -> None:
        if self.model_process.state() != QProcess.ProcessState.NotRunning:
            return
        directory = Path(
            str(
                self.settings.get(
                    "image_analysis_wd14_model_directory",
                    self.project_root / "var" / "models" / "image_analysis" / "wd-vit-tagger-v3",
                )
            )
        )
        answer = QMessageBox.question(
            self.page,
            "Installer WD14",
            f"Télécharger environ 380 Mo depuis SmilingWolf vers :\n{directory}\n\nContinuer ?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.page.wd14_install.setEnabled(False)
        self.page.gpu_runtime_install.setEnabled(False)
        self._install_operation = "model"
        self.page.wd14_state.setText("WD14 : téléchargement…")
        self.model_process.setWorkingDirectory(str(self.project_root))
        self.model_process.start(
            self.python_executable,
            [
                "-u",
                "-m",
                "booruflow.cli.wd14_model",
                "install",
                "--directory",
                str(directory),
                "--model-id",
                str(
                    self.settings.get(
                        "image_analysis_wd14_model_id", "SmilingWolf/wd-vit-tagger-v3"
                    )
                ),
            ],
        )

    def install_gpu_runtime(self) -> None:
        if self.model_process.state() != QProcess.ProcessState.NotRunning:
            return
        answer = QMessageBox.question(
            self.page,
            "Installer le runtime GPU",
            "Installer CUDA 13 et cuDNN 9 dans l’environnement Python isolé de BooruFlow ?\n\n"
            "Aucune modification du PATH Windows ne sera effectuée.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.page.wd14_install.setEnabled(False)
        self.page.gpu_runtime_install.setEnabled(False)
        self.page.wd14_state.setText("Runtime GPU : installation…")
        self._install_operation = "runtime"
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()
            self.process.waitForFinished(3000)
        self.model_process.setWorkingDirectory(str(self.project_root))
        self.model_process.start(
            self.python_executable,
            [
                "-u",
                "-m",
                "pip",
                "install",
                "onnxruntime-gpu[cuda,cudnn]>=1.27,<2",
            ],
        )

    def _model_output(self) -> None:
        output = (
            bytes(self.model_process.readAllStandardOutput())
            .decode("utf-8", errors="replace")
            .strip()
        )
        if output:
            self.page.wd14_state.setText(output.splitlines()[-1])

    def _model_finished(self, code: int, _status) -> None:
        self.page.wd14_install.setEnabled(True)
        self.page.gpu_runtime_install.setEnabled(True)
        if code == 0:
            label = (
                "Runtime GPU installé" if self._install_operation == "runtime" else "WD14 installé"
            )
            self.page.wd14_state.setText(f"{label}, redémarrage du worker…")
            if self.process.state() != QProcess.ProcessState.NotRunning:
                self.process.terminate()
            QTimer.singleShot(500, self.start_worker)
        else:
            self.page.wd14_state.setText("Installation WD14/runtime GPU : échec")
        self._install_operation = ""

    def refresh(self) -> None:
        started = time.perf_counter()
        if not self._page_active:
            self._page_dirty = True
            return
        stale = (datetime.now(UTC) - timedelta(seconds=self.stale_seconds)).isoformat(
            timespec="seconds"
        )
        self.repository.recover_interrupted(stale)
        after_recovery = time.perf_counter()
        items = self.repository.list_items()
        after_repository = time.perf_counter()
        signature = tuple(
            (row["id"], row["state"], str(row["cached_path"] or ""), row["last_error"])
            for row in items
        )
        if signature != self._last_queue_signature:
            self._last_queue_signature = signature
            self.page.show_sources(items)
        after_model = time.perf_counter()
        status = self.workflow.status()
        after_counters = time.perf_counter()
        self.page.pipeline.setText(
            self.page.catalog.text(
                "image_analysis.pipeline",
                unresolved=status.unresolved,
                resolved=status.resolved,
                pending=status.pending,
                processing=status.processing,
                ready=status.ready_for_review,
                failed=status.failed,
            )
        )
        if self.current is None:
            self.current = self.workflow.next_for_review()
            if self.current:
                self._show_current()
        after_selection = time.perf_counter()
        active = bool(status.unresolved or status.pending or status.processing)
        desired_interval = 500 if active else 3000
        if self.timer.interval() != desired_interval:
            self.timer.setInterval(desired_interval)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms >= 50:
            detail = f"recovery={(after_recovery - started) * 1000:.0f}ms repository={(after_repository - after_recovery) * 1000:.0f}ms model={(after_model - after_repository) * 1000:.0f}ms counters={(after_counters - after_model) * 1000:.0f}ms selection={(after_selection - after_counters) * 1000:.0f}ms"
            self._log_slow_refresh(elapsed_ms, detail)

    def set_page_active(self, active: bool) -> None:
        was_active = self._page_active
        self._page_active = active
        if active and (not was_active or self._page_dirty):
            self._page_dirty = False
            self._last_queue_signature = None
            QTimer.singleShot(0, self.refresh)

    def _log_slow_refresh(self, elapsed_ms: float, detail: str) -> None:
        now = time.monotonic()
        if elapsed_ms < 150:
            self.log(
                log_event(
                    "UI",
                    f"Image Analysis refresh slow: {elapsed_ms:.0f} ms; {detail}",
                    level="DEBUG",
                )
            )
            return
        if elapsed_ms >= 500 or now - self._slow_refresh_last >= 10:
            samples = [*self._slow_refresh_samples, elapsed_ms]
            level = "ERROR" if elapsed_ms >= 2000 else "WARNING"
            message = (
                f"Image Analysis refresh remained slow: count={len(samples)} avg={sum(samples) / len(samples):.0f} ms max={max(samples):.0f} ms; {detail}"
                if self._slow_refresh_samples
                else f"Image Analysis refresh slow: {elapsed_ms:.0f} ms; {detail}"
            )
            self.log(log_event("UI", message, level=level))
            self._slow_refresh_last = now
            self._slow_refresh_samples.clear()
        else:
            self._slow_refresh_samples.append(elapsed_ms)

    def _show_current(self) -> None:
        self.page.show_review(
            self.current,
            self.repository.source_tags(self.current.id),
            self.repository.observations(self.current.id),
            self.repository.statistics(self.current.id),
        )

    def complete(self) -> None:
        if not self.current:
            return
        current = self.repository.get_item(self.current.id)
        if current is None or current.state.value != "ready_for_review":
            return
        try:
            self.current = self.repository.activate_review(current.id)
        except (ValueError, KeyError) as exc:
            self.log(f"Image Analysis: {exc}")
            return
        self.current = self.workflow.complete_review(self.current.id)
        self.page.show_review(None, (), [], None) if not self.current else self._show_current()
        self.refresh()

    def skip(self) -> None:
        if not self.current:
            return
        current = self.repository.get_item(self.current.id)
        if current is None or current.state.value != "ready_for_review":
            return
        try:
            self.current = self.repository.activate_review(current.id)
        except (ValueError, KeyError) as exc:
            self.log(f"Image Analysis: {exc}")
            return
        self.current = self.workflow.skip_review(self.current.id)
        self.page.show_review(None, (), [], None) if not self.current else self._show_current()
        self.refresh()

    def open_item(self, item_id: int) -> None:
        item = self.repository.get_item(item_id)
        if item is None:
            return
        if item.state.value == "ready_for_review":
            try:
                item = self.repository.activate_review(item_id)
            except (ValueError, KeyError) as exc:
                self.log(f"Image Analysis: {exc}")
                return
        self.current = item
        self._show_current()
        self.refresh()

    def requeue_item(self, item_id: int) -> None:
        item = self.repository.get_item(item_id)
        if item is None or item.state.value != "skipped":
            return
        try:
            self.repository.requeue_skipped(item_id)
            self.current = self.repository.activate_review(item_id)
        except (ValueError, KeyError) as exc:
            self.log(f"Image Analysis: {exc}")
            return
        self._show_current()
        self.refresh()

    def queue_filter_changed(self, mode: str) -> None:
        if mode != "active":
            return
        active = self.repository.active_review() or self.workflow.next_for_review()
        if active:
            if self.current and self.current.id == active.id:
                return
            self.current = active
            self._show_current()
            self.refresh()

    def clean_queue(self, mode: str) -> None:
        if mode == "active":
            answer = QMessageBox.question(
                self.page,
                "Vider la file active",
                "Retirer les tâches en attente, prêtes et en erreur de la file ?\n\n"
                "Les analyses, décisions et provenances seront conservées. "
                "Les traitements en cours et la revue active seront laissés terminer.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            changed, retained = self.repository.clean_queue(mode)
        except ValueError as exc:
            self.log(f"Image Analysis: {exc}")
            return
        if self.current and not self.repository.item_queue_visible(self.current.id):
            self.current = self.repository.active_review() or self.workflow.next_for_review()
            if self.current:
                self._show_current()
            else:
                self.page.show_review(None, (), [], None)
        self.page.drop_status.setText(
            f"{changed} élément(s) retiré(s) de la file · analyses conservées"
            + (f" · {retained} traitement(s)/revue active conservé(s)" if retained else "")
        )
        self.refresh()

    def retry(self, item_id: int) -> None:
        try:
            self.workflow.retry(item_id)
        except (ValueError, KeyError) as exc:
            self.log(f"Image Analysis: {exc}")
        self.refresh()

    def add_manual_tag(self, name: str) -> None:
        if not self.current:
            return
        try:
            self.workflow.add_manual_tag(self.current.id, name)
            self.page.manual_tag.clear()
            self._show_current()
        except ValueError as exc:
            self.log(f"Image Analysis: {exc}")

    def decide(self, observation_id: int, decision: DecisionState, name: str | None) -> None:
        self.workflow.decide(observation_id, decision, name)
        if self.current:
            self._show_current()

    def decide_many(self, observation_ids: list[int], decision: DecisionState) -> None:
        for observation_id in observation_ids:
            self.workflow.decide(observation_id, decision, None)
        if self.current:
            self._show_current()

    def shutdown(self) -> None:
        self._shutting_down = True
        self.worker_startup_timer.stop()
        self.worker_restart_timer.stop()
        self.timer.stop()
        if self.source_worker and self.source_worker.isRunning():
            self.source_worker.requestInterruption()
            self.source_worker.wait(3000)
        if self.drop_scan_worker and self.drop_scan_worker.isRunning():
            self.drop_scan_worker.requestInterruption()
            self.drop_scan_worker.wait(3000)
        if self.process.state() != QProcess.ProcessState.NotRunning:
            launcher_pid = int(self.process.processId())
            pid = self._worker_pid or launcher_pid
            self.log(
                log_event(
                    "Worker",
                    f"ImageAnalysis worker shutdown requested pid={pid} launcher_pid={launcher_pid}",
                )
            )
            self.process.write(b"STOP\n")
            self.process.waitForBytesWritten(500)
            if not self.process.waitForFinished(7000):
                self.log(
                    log_event(
                        "Worker",
                        f"Cooperative shutdown timed out; terminating launcher_pid={launcher_pid}",
                        level="WARNING",
                    )
                )
                self.process.terminate()
            if (
                self.process.state() != QProcess.ProcessState.NotRunning
                and not self.process.waitForFinished(2000)
            ):
                self.log(
                    log_event(
                        "Worker",
                        f"Launcher refused termination; killing launcher_pid={launcher_pid}",
                        level="ERROR",
                    )
                )
                self.process.kill()
                self.process.waitForFinished(2000)
            if pid != launcher_pid and _pid_is_running(pid):
                self.log(
                    log_event(
                        "Worker",
                        f"Worker still alive after launcher shutdown; final fallback pid={pid}",
                        level="ERROR",
                    )
                )
                _force_stop_pid(pid)
            self.log(log_event("Worker", f"ImageAnalysis worker shutdown complete pid={pid}"))
        if self.model_process.state() != QProcess.ProcessState.NotRunning:
            self.model_process.terminate()
            self.model_process.waitForFinished(2000)
        self.repository.close()
