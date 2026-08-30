"""Qt worker for Gelbooru tagging review."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QInputDialog, QMessageBox

from booruflow.application.tag_lookup import exact_tag, lookup_tags
from booruflow.application.tagging import (
    LocalMatchState,
    TaggingRequest,
    analysis_resume_action,
    is_rating_observation,
    match_local_tag,
    normalize_booru_tag,
    parse_review_row_token,
)
from booruflow.domain.image_analysis import DecisionState
from booruflow.infrastructure.gelbooru_tagging import GelbooruTaggingScanner, post_tags
from booruflow.infrastructure.image_sources import GelbooruPostProvider, ImageSourceError
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.tag_browser import TagSearch, search_tags
from booruflow.infrastructure.tag_category_lookup import LocalTagCategoryLookup
from booruflow.presentation.pyside6.task_manager import TaskManager
from booruflow.presentation.pyside6.ui_logging import log_event


class TaggingWorker(QThread):
    progress = Signal(int, int, int, int, int)
    completed = Signal(list, int, int, bool, str, bool)

    def __init__(self, request: TaggingRequest, user_id: str, api_key: str) -> None:
        super().__init__()
        self.request = request
        self.user_id = user_id
        self.api_key = api_key

    def run(self) -> None:
        try:
            posts, examined, next_page, reached_end = GelbooruTaggingScanner().scan(
                self.request,
                self.user_id,
                self.api_key,
                cancelled=self.isInterruptionRequested,
                progress=lambda *values: self.progress.emit(*values),
            )
            self.completed.emit(
                posts, examined, next_page, reached_end, "", self.isInterruptionRequested()
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary reports scanner failures
            self.completed.emit([], 0, self.request.start_page, False, str(exc), False)


class TaggingLegacyController(QObject):
    def __init__(
        self,
        catalog: LanguageCatalog,
        page,
        credentials: Callable[[], dict[str, object]],
        log: Callable[[str], None],
        task_manager: TaskManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.page = page
        self.credentials = credentials
        self.log = log
        self.task_manager = task_manager
        self.task_id: str | None = None
        self.worker: TaggingWorker | None = None
        self.image_analysis = None
        self.current_post_id: int | None = None
        self.current_post: dict = {}
        self._last_polled_state: tuple | None = None
        self._requested_post_id: int | None = None
        page.post_selected.connect(self.select_post)
        page.analyze_requested.connect(self.analyze)
        page.decision_requested.connect(self.decide)
        page.mapping_requested.connect(self.map_selected)
        page.refresh_metadata_requested.connect(self.refresh_metadata)
        page.activity_logged.connect(self._page_activity)
        page.manual_lookup_requested.connect(self.lookup_manual_tags)
        page.manual_add_requested.connect(self.add_manual_tag)
        page.tag_search_requested.connect(self.open_tag_search)

    def _log(self, message: str, *, level: str = "INFO", item_id: int | None = None) -> None:
        context = f"gelbooru:{self.current_post_id or '-'}"
        if item_id is not None:
            context += f" item:{item_id}"
        self.log(log_event("Tagging", message, level=level, context=context))

    # Compatibility hooks for the new Tagging subclass.  Legacy no longer
    # renders the obsolete pool panel, while the persisted pool itself remains.
    def refresh_pool(self) -> None:
        return

    def reopen_pool_items(self, item_ids: list[int]) -> None:
        _ = item_ids

    def _page_activity(self, action: str, detail: str) -> None:
        self._log(f"{action}: {detail}")

    def bind_image_analysis(self, controller) -> None:
        self.image_analysis = controller
        controller.timer.timeout.connect(self._poll_current)
        controller.worker_state_changed.connect(self._image_analysis_state_changed)

    def _image_analysis_state_changed(self, state: str, detail: str) -> None:
        if self.current_post_id is None or state not in {"failed", "startup_timeout"}:
            return
        self._log(detail or f"ImageAnalysis {state}", level="ERROR")
        self.refresh_local_review()

    def _worker_failure_label(self) -> str | None:
        if not self.image_analysis:
            return None
        state = getattr(self.image_analysis, "worker_startup_state", "ready")
        detail = getattr(self.image_analysis, "worker_startup_detail", "")
        if state == "startup_timeout":
            return f"Erreur : délai de démarrage ImageAnalysis dépassé. {detail}".strip()
        if state == "failed":
            return f"Erreur : ImageAnalysis indisponible. {detail}".strip()
        return None

    def _poll_current(self) -> None:
        if self.current_post_id is None or not self.image_analysis: return
        try:
            item = self.image_analysis.repository.item_by_remote_source(
                "gelbooru", str(self.current_post_id)
            )
            signature = None if item is None else (
                item.id, item.state.value, str(item.cached_path or "")
            )
            if signature != self._last_polled_state:
                self._last_polled_state = signature
                if item is not None:
                    self._log(f"Analysis state changed to {item.state.value}", item_id=item.id)
                self.refresh_local_review()
        except Exception as exc:  # noqa: BLE001 - timer boundary
            self._log(f"Could not follow analysis state: {exc}", level="ERROR")
            self.page.set_analysis_request_state(f"Erreur : {exc}", False)

    def select_post(self, post_id: int, post: dict) -> None:
        self.current_post_id = post_id; self.current_post = dict(post)
        self._last_polled_state = None
        self._requested_post_id = None
        self._log("Selected post")
        try:
            item = self.image_analysis.repository.item_by_remote_source(
                "gelbooru", str(post_id)
            ) if self.image_analysis else None
            if item is None:
                self.page.set_analysis_request_state("Analyse en attente…", True)
                self._log("No existing analysis; automatic local analysis requested")
                self.analyze(post_id)
            else:
                self.refresh_local_review()
                self._last_polled_state = (
                    item.id, item.state.value, str(item.cached_path or "")
                )
                if item.state.value in {"ready_for_review", "reviewed"}:
                    self.page.analysis_state.setText("Analyse disponible — cache réutilisé")
        except Exception as exc:  # noqa: BLE001 - Qt signal boundary
            self._log(f"Could not open local review: {exc}", level="ERROR")
            self.page.set_analysis_request_state(f"Erreur : {exc}", False)

    def analyze(self, post_id: int) -> None:
        if not self.image_analysis: return
        self._requested_post_id = post_id
        self.page.set_analysis_request_state("Analyse en attente…", True)
        self._log("Local analysis requested; looking for existing AnalysisItem")
        try:
            repository = self.image_analysis.repository
            known = repository.item_by_remote_source("gelbooru", str(post_id))
            if known is None:
                self._log("No existing item found; creating analysis source")
                ids = self.image_analysis.add_remote_ids(
                    "gelbooru", [str(post_id)], priority=100
                )
                if hasattr(repository, "add_to_tagging_pool"): repository.add_to_tagging_pool(ids, "tagging_remote")
                self._log(f"Item queued: {ids[0] if ids else 'pending lookup'}")
            else:
                visible = repository.item_queue_visible(known.id)
                self._log(
                    f"Existing analysis found; state={known.state.value}; queue_visible={int(visible)}",
                    item_id=known.id,
                )
                action = analysis_resume_action(known.state.value)
                if hasattr(repository, "add_to_tagging_pool"): repository.add_to_tagging_pool([known.id], "tagging_remote")
                if action == "restore_review":
                    repository.requeue_skipped(known.id)
                    self._log("Skipped analysis restored as ready", item_id=known.id)
                elif action == "retry":
                    repository.request_analysis(known.id, 100)
                    self.image_analysis.retry(known.id)
                    self._log("Failed analysis requeued", item_id=known.id)
                elif action == "restore_pending":
                    repository.request_analysis(known.id, 100)
                    self.image_analysis._start_source_preparation()
                    self._log("Pending analysis made queue-visible", item_id=known.id)
                elif action == "reuse":
                    self._log("Existing analysis reused", item_id=known.id)
            self._last_polled_state = None
            self.refresh_local_review()
        except Exception as exc:  # noqa: BLE001 - Qt action boundary must surface every failure
            self._log(f"Local analysis failed: {exc}", level="ERROR")
            self.page.set_analysis_request_state(f"Erreur : {exc}", False)

    def _local_names(self, names: list[str]) -> set[str]:
        path_value = str(self.image_analysis.settings.get("gelbooru_database", ""))
        if not path_value: return set()
        from pathlib import Path
        database = Path(path_value)
        result: set[str] = set()
        for name in names:
            try:
                result.update(row.name for row in search_tags(
                    database, TagSearch(text=name, mode="exact", limit=2)
                ))
            except (FileNotFoundError, ValueError):
                return set()
        return result

    def _tag_database(self) -> Path | None:
        if not self.image_analysis: return None
        value = str(self.image_analysis.settings.get("gelbooru_database", ""))
        return Path(value) if value else None

    def lookup_manual_tags(self, text: str) -> None:
        database = self._tag_database()
        if database is None or len(text.strip()) < 2:
            self.page.set_manual_suggestions([]); return
        try:
            self.page.set_manual_suggestions([row.name for row in lookup_tags("gelbooru", database, text.strip(), limit=20)])
        except (FileNotFoundError, ValueError):
            self.page.set_manual_suggestions([])

    def add_manual_tag(self, value: str) -> None:
        if not self.image_analysis or self.current_post_id is None: return
        database = self._tag_database()
        if database is None: return
        try:
            name = exact_tag("gelbooru", database, normalize_booru_tag(value))
            if name is None:
                self.page.analysis_state.setText("Tag absent ou deprecated dans la base Gelbooru."); return
            item = self.image_analysis.repository.item_by_remote_source("gelbooru", str(self.current_post_id))
            if item is None: return
            existing = {normalize_booru_tag(tag) for tag in post_tags(self.current_post)}
            observations = self.image_analysis.repository.observations(item.id)
            if normalize_booru_tag(name.name) in existing or any(normalize_booru_tag(o.reviewed_name or o.name) == normalize_booru_tag(name.name) for _id, o in observations):
                self.page.clear_manual_entry(); return
            self.image_analysis.workflow.add_manual_tag(item.id, name.name)
            self.page.clear_manual_entry(); self.refresh_local_review()
        except (FileNotFoundError, ValueError) as exc:
            self.page.analysis_state.setText(f"Ajout manuel impossible : {exc}")

    def open_tag_search(self, tag: str) -> None:
        from urllib.parse import urlencode
        url = "https://gelbooru.com/index.php?" + urlencode({"page": "post", "s": "list", "tags": tag})
        if self.page.browser_launcher: self.page.browser_launcher.open(url)

    def refresh_local_review(self) -> None:
        if not self.image_analysis or not self.current_post_id: return
        repository = self.image_analysis.repository
        item = repository.item_by_remote_source("gelbooru", str(self.current_post_id))
        current_tags = set(post_tags(self.current_post))
        if item is None:
            label = self._worker_failure_label() or "Non analysée"
            self.page.show_local_review(label, None, sorted(current_tags), [], [], [])
            return
        persisted_tags = {tag.name for tag in repository.source_tags(item.id) if tag.source.value == "gelbooru"}
        if persisted_tags:
            current_tags = persisted_tags
        observations = [
            row for row in repository.observations(item.id) if row[1].source.value in {"wd14", "manual"}
        ]
        names = [
            observation.reviewed_name or observation.name
            for _oid, observation in observations
            if not is_rating_observation(observation.name, observation.category)
        ]
        local = self._local_names(names)
        rows = []
        summary = repository.tag_review_summary(item.id, sorted(current_tags)) if hasattr(repository, "tag_review_summary") else {"removals": [], "final_tags": sorted(current_tags)}
        existing_rows: dict[str, dict] = {}
        for tag in sorted(current_tags):
            row = {"id": f"existing:{tag}", "tag": tag, "confidence": "", "decision": "remove" if tag in summary["removals"] else "keep", "match": "Existant"}
            rows.append(row)
            existing_rows[normalize_booru_tag(tag)] = row
        for observation_id, observation in observations:
            name = observation.reviewed_name or observation.name
            if is_rating_observation(observation.name, observation.category):
                continue
            mapping = repository.tag_mapping("wd14", observation.name, "gelbooru") if observation.source.value == "wd14" else None
            match = match_local_tag(name, local, current_tags, mapping)
            existing_key = normalize_booru_tag(
                match.target_tag if match.state is LocalMatchState.ALREADY_PRESENT and match.target_tag else name
            )
            existing_row = existing_rows.get(existing_key)
            if existing_row is not None:
                confidence = "" if observation.confidence is None else f"{observation.confidence:.3f}"
                if confidence and (not existing_row["confidence"] or float(confidence) > float(existing_row["confidence"])):
                    existing_row["confidence"] = confidence
                existing_row["match"] = "Existant · également détecté par WD14"
                continue
            rows.append({
                "id": observation_id, "tag": name,
                "confidence": "" if observation.confidence is None else f"{observation.confidence:.3f}",
                "decision": observation.decision.value,
                "match": {
                    LocalMatchState.EXACT: "exact", LocalMatchState.MAPPING: f"mapping → {match.target_tag}",
                    LocalMatchState.MISSING: "introuvable localement",
                    LocalMatchState.ALREADY_PRESENT: "déjà présent",
                }[match.state],
            })
        labels = {
            "pending": "Analyse en attente", "processing": "Analyse en cours",
            "ready_for_review": "Analyse disponible", "reviewed": "Déjà analysée",
            "failed": f"Erreur d’analyse : {item.last_error or 'inconnue'}",
            "skipped": "Analyse ignorée",
        }
        if item.state.value == "pending" and hasattr(repository, "scheduler_diagnostic"):
            diagnostic = repository.scheduler_diagnostic(
                int(self.image_analysis.policy.analysis_prefetch)
            )
            labels["pending"] = {
                "prefetch_limit": "Analyse en attente — file de préchargement pleine",
                "interactive_eligible": "Analyse en attente — worker occupé",
                "no_eligible_pending_item": "Analyse en attente — source en préparation",
            }.get(str(diagnostic["reason"]), "Analyse en attente")
        if item.state.value in {"pending", "processing"}:
            labels[item.state.value] = self._worker_failure_label() or labels[item.state.value]
        self.page.show_local_review(labels[item.state.value], item.cached_path,
                                    sorted(current_tags), rows, [], summary["final_tags"])
        if item.state.value in {"ready_for_review", "reviewed"}:
            self._log("Opening local review", item_id=item.id)

    def decide(self, observation_id: object, value: str) -> None:
        if not self.image_analysis: return
        try:
            tokens = observation_id if isinstance(observation_id, list) else [observation_id]
            item_id = self.image_analysis.repository.item_by_remote_source("gelbooru", str(self.current_post_id)).id
            for token_value in tokens:
                target_kind, target = parse_review_row_token(token_value)
                if target_kind == "existing":
                    self.image_analysis.repository.set_existing_tag_decision(
                        item_id, target, "keep" if value == "accepted" else "remove"
                    )
                else:
                    self.image_analysis.workflow.decide(
                        target,
                        DecisionState.ACCEPTED if value == "accepted" else DecisionState.REJECTED,
                        None,
                    )
            self._log(f"Review entries {len(tokens)} {value}")
            self.refresh_local_review()
        except Exception as exc:  # noqa: BLE001 - Qt action boundary
            self._log(f"Could not save decision {observation_id}: {exc}", level="ERROR")
            self.page.analysis_state.setText(f"Erreur de décision : {exc}")

    def map_selected(self, observation_id: int) -> None:
        if not self.image_analysis: return
        item = self.image_analysis.repository.item_by_remote_source(
            "gelbooru", str(self.current_post_id)
        )
        if item is None: return
        row = next(
            (value for value in self.image_analysis.repository.observations(item.id)
             if value[0] == observation_id),
            None,
        )
        if row is None: return
        source = row[1].name
        target, accepted = QInputDialog.getText(self.page, "Associer le tag", f"Tag Gelbooru pour {source} :")
        if not accepted or not target.strip(): return
        if not self._local_names([target.strip()]):
            self._log(f"Mapping rejected; local tag not found: {target.strip()}", level="WARNING")
            QMessageBox.warning(self.page, "Association impossible", "Ce tag est absent de la base locale.")
            return
        self.image_analysis.repository.set_tag_mapping("wd14", source, "gelbooru", target.strip())
        self._log(f"Mapping saved: {source} -> {target.strip()}")
        try:
            self.refresh_local_review()
        except Exception as exc:  # noqa: BLE001 - Qt action boundary
            self._log(f"Could not refresh mapping results: {exc}", level="ERROR")

    def refresh_metadata(self, post_id: int) -> None:
        if not self.image_analysis: return
        repository = self.image_analysis.repository
        item = repository.item_by_remote_source("gelbooru", str(post_id))
        if item is None: return
        credentials = self.credentials().get("gelbooru", {})
        credentials = credentials if isinstance(credentials, dict) else {}
        from pathlib import Path
        database_value = str(self.image_analysis.settings.get("gelbooru_database", ""))
        lookup = LocalTagCategoryLookup(Path(database_value)) if database_value else None
        try:
            normalized = GelbooruPostProvider(
                str(credentials.get("user_id", "")), str(credentials.get("api_key", "")),
                category_lookup=lookup,
            ).fetch_post(str(post_id))
            self._log("Metadata request finished")
            repository.replace_source_metadata(item.id, "gelbooru", normalized.tags, normalized.artist_tags)
            self.current_post["tags"] = " ".join(tag.name for tag in normalized.tags)
            self.refresh_local_review()
        except (ImageSourceError, OSError, ValueError) as exc:
            self._log(f"Metadata refresh failed; cached data kept: {exc}", level="WARNING")
            self.page.analysis_state.setText(f"Actualisation impossible, données conservées : {exc}")

    def start(self, request: TaggingRequest) -> None:
        gelbooru = self.credentials().get("gelbooru", {})
        if (
            not isinstance(gelbooru, dict)
            or not gelbooru.get("user_id")
            or not gelbooru.get("api_key")
        ):
            self.page.state.setText(self.catalog.text("review.credentials_missing"))
            return
        self.page.set_running(True)
        if self.task_manager:
            self.task_id = self.task_manager.start(
                "tagging", self.catalog.text("nav.tagging"), request.query
            )
        self.log(self.catalog.text("tagging.log_start", query=request.query))
        self.worker = TaggingWorker(
            request,
            str(gelbooru["user_id"]),
            str(gelbooru["api_key"]),
        )
        self.worker.progress.connect(self.progress)
        self.worker.completed.connect(self.finished)
        self.worker.start()

    def stop(self) -> None:
        if self.worker:
            self.worker.requestInterruption()
            self.page.state.setText(self.catalog.text("tagging.stopping"))

    def progress(self, page: int, current: int, total: int, examined: int, retained: int) -> None:
        self.page.set_progress(page, current, total, examined, retained)
        if self.task_manager and self.task_id:
            self.task_manager.progress(
                self.task_id, current, total, f"page {page}", f"{examined} / {retained}"
            )

    def finished(
        self,
        posts: list,
        examined: int,
        next_page: int,
        reached_end: bool,
        error: str,
        stopped: bool,
    ) -> None:
        self.page.set_running(False)
        self.page.spins["start"].setValue(max(1, next_page))
        self.page.show_results(posts)
        if error:
            message = self.catalog.text("tagging.failed", error=error)
            self.page.state.setText(message)
            self.log(message)
            task_state = "failed"
        elif stopped:
            self.page.state.setText(self.catalog.text("tagging.stopped", examined=examined))
            task_state = "cancelled"
        elif reached_end and not posts:
            self.page.state.setText(self.catalog.text("tagging.end", examined=examined))
            task_state = "completed"
        else:
            self.page.state.setText(
                self.catalog.text("tagging.finished", examined=examined, retained=len(posts))
            )
            task_state = "completed"
        if self.task_manager and self.task_id:
            self.task_manager.finish(self.task_id, task_state, self.page.state.text())
            self.task_id = None
        if self.worker:
            self.worker.deleteLater()
            self.worker = None
