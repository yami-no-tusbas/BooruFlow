"""Qt worker for Gelbooru tagging review."""

from __future__ import annotations

import urllib.error
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QInputDialog, QMessageBox

from booruflow.application.database_paths import gelbooru_tag_database
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
from booruflow.domain.booru_sites import site_definition
from booruflow.domain.image_analysis import DecisionState
from booruflow.infrastructure.e621_client import E621Client, MalformedE621Response
from booruflow.infrastructure.e621_tagging import E621TaggingScanner
from booruflow.infrastructure.gelbooru_tagging import GelbooruTaggingScanner, post_tags
from booruflow.infrastructure.image_sources import (
    E621PostProvider,
    GelbooruPostProvider,
    ImageSourceError,
)
from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.infrastructure.tag_browser import TagSearch, search_tags
from booruflow.infrastructure.tag_category_lookup import LocalTagCategoryLookup
from booruflow.presentation.pyside6.task_manager import TaskManager
from booruflow.presentation.pyside6.ui_logging import log_event


def _controller_site(controller) -> str:
    return str(getattr(getattr(controller, "page", None), "active_site", "gelbooru"))


class TaggingWorker(QThread):
    progress = Signal(int, int, int, int, int)
    completed = Signal(list, int, int, bool, str, bool, str)

    def __init__(self, request: TaggingRequest, user_id: str, api_key: str) -> None:
        super().__init__()
        self.request = request
        self.user_id = user_id
        self.api_key = api_key

    def run(self) -> None:
        try:
            if self.request.site == "e621":
                posts, examined, next_page, reached_end = E621TaggingScanner(
                    E621Client(self.user_id, self.api_key)
                ).scan(
                    self.request,
                    cancelled=self.isInterruptionRequested,
                    progress=lambda *values: self.progress.emit(*values),
                )
            else:
                posts, examined, next_page, reached_end = GelbooruTaggingScanner().scan(
                    self.request,
                    self.user_id,
                    self.api_key,
                    cancelled=self.isInterruptionRequested,
                    progress=lambda *values: self.progress.emit(*values),
                )
            self.completed.emit(
                posts, examined, next_page, reached_end, "", self.isInterruptionRequested(),
                self.request.site,
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                error = "invalid_credentials"
            elif exc.code in {429, 503}:
                error = "rate_limited"
            elif exc.code == 403 or exc.code >= 500:
                error = "server_error"
            else:
                error = "network_error"
            self.completed.emit([], 0, self.request.start_page, False, error, False, self.request.site)
        except MalformedE621Response:
            self.completed.emit(
                [], 0, self.request.start_page, False, "malformed_response", False,
                self.request.site,
            )
        except (urllib.error.URLError, TimeoutError, OSError):
            self.completed.emit(
                [], 0, self.request.start_page, False, "network_error", False,
                self.request.site,
            )
        except Exception:  # noqa: BLE001 - do not expose credential-bearing exception text
            self.completed.emit(
                [], 0, self.request.start_page, False, "unexpected", False,
                self.request.site,
            )


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

    def _site(self) -> str:
        return _controller_site(self)

    def _log(self, message: str, *, level: str = "INFO", item_id: int | None = None) -> None:
        context = f"{self._site()}:{self.current_post_id or '-'}"
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
            return self.catalog.text("tagging.analysis.startup_timeout", detail=detail).strip()
        if state == "failed":
            return self.catalog.text("tagging.analysis.unavailable", detail=detail).strip()
        return None

    def _poll_current(self) -> None:
        if self.current_post_id is None or not self.image_analysis: return
        try:
            item = self.image_analysis.repository.item_by_remote_source(
                _controller_site(self), str(self.current_post_id)
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
            self.page.set_analysis_request_state(self.catalog.text("tagging.analysis.error", error=exc), False)

    def select_post(self, post_id: int, post: dict) -> None:
        self.current_post_id = post_id; self.current_post = dict(post)
        self._last_polled_state = None
        self._requested_post_id = None
        self._log("Selected post")
        try:
            item = self.image_analysis.repository.item_by_remote_source(
                _controller_site(self), str(post_id)
            ) if self.image_analysis else None
            if item is None:
                self.page.set_analysis_request_state(self.catalog.text("tagging.analysis.pending"), True)
                self._log("No existing analysis; automatic local analysis requested")
                self.analyze(post_id)
            else:
                self.refresh_local_review()
                self._last_polled_state = (
                    item.id, item.state.value, str(item.cached_path or "")
                )
                if item.state.value in {"ready_for_review", "reviewed"}:
                    self.page.analysis_state.setText(self.catalog.text("tagging.analysis.cached"))
        except Exception as exc:  # noqa: BLE001 - Qt signal boundary
            self._log(f"Could not open local review: {exc}", level="ERROR")
            self.page.set_analysis_request_state(self.catalog.text("tagging.analysis.error", error=exc), False)

    def analyze(self, post_id: int) -> None:
        if not self.image_analysis: return
        self._requested_post_id = post_id
        self.page.set_analysis_request_state(self.catalog.text("tagging.analysis.pending"), True)
        self._log("Local analysis requested; looking for existing AnalysisItem")
        try:
            repository = self.image_analysis.repository
            known = repository.item_by_remote_source(_controller_site(self), str(post_id))
            if known is None:
                self._log("No existing item found; creating analysis source")
                ids = self.image_analysis.add_remote_ids(
                    _controller_site(self), [str(post_id)], priority=100
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
            self.page.set_analysis_request_state(self.catalog.text("tagging.analysis.error", error=exc), False)

    def _local_names(self, names: list[str]) -> set[str]:
        path_value = str(self.image_analysis.settings.get(
            site_definition(_controller_site(self)).database_setting_key, ""
        ))
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
        if _controller_site(self) == "gelbooru":
            return gelbooru_tag_database(self.image_analysis.settings)
        value = str(self.image_analysis.settings.get(
            site_definition(_controller_site(self)).database_setting_key, ""
        ))
        return Path(value) if value else None

    def lookup_manual_tags(self, text: str) -> None:
        database = self._tag_database()
        if database is None or len(text.strip()) < 2:
            self.page.set_manual_suggestions([]); return
        try:
            self.page.set_manual_suggestions([row.name for row in lookup_tags(_controller_site(self), database, text.strip(), limit=20)])
        except (FileNotFoundError, ValueError):
            self.page.set_manual_suggestions([])

    def add_manual_tag(self, value: str) -> None:
        if not self.image_analysis or self.current_post_id is None: return
        database = self._tag_database()
        if database is None: return
        try:
            name = exact_tag(_controller_site(self), database, normalize_booru_tag(value))
            if name is None:
                self.page.analysis_state.setText(self.catalog.text("tagging.review.tag_unavailable")); return
            item = self.image_analysis.repository.item_by_remote_source(_controller_site(self), str(self.current_post_id))
            if item is None: return
            existing = {normalize_booru_tag(tag) for tag in post_tags(self.current_post)}
            observations = self.image_analysis.repository.observations(item.id)
            if normalize_booru_tag(name.name) in existing or any(normalize_booru_tag(o.reviewed_name or o.name) == normalize_booru_tag(name.name) for _id, o in observations):
                self.page.clear_manual_entry(); return
            self.image_analysis.workflow.add_manual_tag(item.id, name.name)
            self.page.clear_manual_entry(); self.refresh_local_review()
        except (FileNotFoundError, ValueError) as exc:
            self.page.analysis_state.setText(self.catalog.text("tagging.review.manual_error", error=exc))

    def open_tag_search(self, tag: str) -> None:
        url = site_definition(_controller_site(self)).search_url(tag)
        if self.page.browser_launcher: self.page.browser_launcher.open(url)

    def refresh_local_review(self) -> None:
        if not self.image_analysis or not self.current_post_id: return
        repository = self.image_analysis.repository
        item = repository.item_by_remote_source(_controller_site(self), str(self.current_post_id))
        current_tags = set(post_tags(self.current_post))
        if item is None:
            label = self._worker_failure_label() or self.catalog.text("tagging.analysis.not_analyzed")
            self.page.show_local_review(label, None, sorted(current_tags), [], [], [])
            return
        persisted_tags = {tag.name for tag in repository.source_tags(item.id) if tag.source.value == _controller_site(self)}
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
            row = {"id": f"existing:{tag}", "tag": tag, "confidence": "", "decision": "remove" if tag in summary["removals"] else "keep", "match": self.catalog.text("tagging.match.existing")}
            rows.append(row)
            existing_rows[normalize_booru_tag(tag)] = row
        for observation_id, observation in observations:
            name = observation.reviewed_name or observation.name
            if is_rating_observation(observation.name, observation.category):
                continue
            mapping = repository.tag_mapping("wd14", observation.name, _controller_site(self)) if observation.source.value == "wd14" else None
            match = match_local_tag(name, local, current_tags, mapping)
            existing_key = normalize_booru_tag(
                match.target_tag if match.state is LocalMatchState.ALREADY_PRESENT and match.target_tag else name
            )
            existing_row = existing_rows.get(existing_key)
            if existing_row is not None:
                confidence = "" if observation.confidence is None else f"{observation.confidence:.3f}"
                if confidence and (not existing_row["confidence"] or float(confidence) > float(existing_row["confidence"])):
                    existing_row["confidence"] = confidence
                existing_row["match"] = self.catalog.text("tagging.match.existing_wd14")
                continue
            rows.append({
                "id": observation_id, "tag": name,
                "confidence": "" if observation.confidence is None else f"{observation.confidence:.3f}",
                "decision": observation.decision.value,
                "match": {
                    LocalMatchState.EXACT: "exact", LocalMatchState.MAPPING: f"mapping → {match.target_tag}",
                    LocalMatchState.MISSING: self.catalog.text("tagging.match.missing"),
                    LocalMatchState.ALREADY_PRESENT: self.catalog.text("tagging.match.already_present"),
                }[match.state],
            })
        labels = {
            "pending": self.catalog.text("tagging.analysis.pending"), "processing": self.catalog.text("tagging.analysis.processing"),
            "ready_for_review": self.catalog.text("tagging.analysis.ready"), "reviewed": self.catalog.text("tagging.analysis.reviewed"),
            "failed": self.catalog.text("tagging.analysis.failed", error=item.last_error or self.catalog.text("tagging.unknown")),
            "skipped": self.catalog.text("tagging.analysis.skipped"),
        }
        if item.state.value == "pending" and hasattr(repository, "scheduler_diagnostic"):
            diagnostic = repository.scheduler_diagnostic(
                int(self.image_analysis.policy.analysis_prefetch)
            )
            labels["pending"] = {
                "prefetch_limit": self.catalog.text("tagging.analysis.pending_prefetch"),
                "interactive_eligible": self.catalog.text("tagging.analysis.pending_busy"),
                "no_eligible_pending_item": self.catalog.text("tagging.analysis.pending_source"),
            }.get(str(diagnostic["reason"]), self.catalog.text("tagging.analysis.pending"))
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
            item_id = self.image_analysis.repository.item_by_remote_source(_controller_site(self), str(self.current_post_id)).id
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
            self.page.analysis_state.setText(self.catalog.text("tagging.review.decision_error", error=exc))

    def map_selected(self, observation_id: int) -> None:
        if not self.image_analysis: return
        item = self.image_analysis.repository.item_by_remote_source(
            _controller_site(self), str(self.current_post_id)
        )
        if item is None: return
        row = next(
            (value for value in self.image_analysis.repository.observations(item.id)
             if value[0] == observation_id),
            None,
        )
        if row is None: return
        source = row[1].name
        target, accepted = QInputDialog.getText(self.page, self.catalog.text("tagging.mapping.title"), self.catalog.text("tagging.mapping.prompt", source=source))
        if not accepted or not target.strip(): return
        if not self._local_names([target.strip()]):
            self._log(f"Mapping rejected; local tag not found: {target.strip()}", level="WARNING")
            QMessageBox.warning(self.page, self.catalog.text("tagging.mapping.impossible"), self.catalog.text("tagging.mapping.missing"))
            return
        self.image_analysis.repository.set_tag_mapping("wd14", source, _controller_site(self), target.strip())
        self._log(f"Mapping saved: {source} -> {target.strip()}")
        try:
            self.refresh_local_review()
        except Exception as exc:  # noqa: BLE001 - Qt action boundary
            self._log(f"Could not refresh mapping results: {exc}", level="ERROR")

    def refresh_metadata(self, post_id: int) -> None:
        if not self.image_analysis: return
        repository = self.image_analysis.repository
        site = self._site()
        item = repository.item_by_remote_source(site, str(post_id))
        if item is None: return
        credentials = self.credentials().get(site, {})
        credentials = credentials if isinstance(credentials, dict) else {}
        from pathlib import Path
        database_value = str(self.image_analysis.settings.get(
            site_definition(site).database_setting_key, ""
        ))
        lookup = LocalTagCategoryLookup(Path(database_value)) if database_value else None
        try:
            normalized = (
                GelbooruPostProvider(
                    str(credentials.get("user_id", "")), str(credentials.get("api_key", "")),
                    category_lookup=lookup,
                )
                if site == "gelbooru"
                else E621PostProvider(
                    str(credentials.get("user_id", "")),
                    str(credentials.get("api_key", "")),
                )
            ).fetch_post(str(post_id))
            self._log("Metadata request finished")
            repository.replace_source_metadata(item.id, site, normalized.tags, normalized.artist_tags)
            self.current_post["tags"] = " ".join(tag.name for tag in normalized.tags)
            self.refresh_local_review()
        except (ImageSourceError, OSError, ValueError) as exc:
            self._log(f"Metadata refresh failed; cached data kept: {exc}", level="WARNING")
            self.page.analysis_state.setText(self.catalog.text("tagging.review.refresh_error", error=exc))

    def start(self, request: TaggingRequest) -> None:
        site = request.site
        site_credentials = self.credentials().get(site, {})
        if (
            not isinstance(site_credentials, dict)
            or not site_credentials.get("user_id")
            or not site_credentials.get("api_key")
        ):
            self.page.state.setText(self.catalog.text("tagging.credentials_missing", site=site))
            return
        self.page.set_running(True)
        if self.task_manager:
            self.task_id = self.task_manager.start(
                "tagging", self.catalog.text("nav.tagging"), request.query
            )
        self._log(f"site={site} search started query={request.query}")
        self.worker = TaggingWorker(
            request,
            str(site_credentials["user_id"]),
            str(site_credentials["api_key"]),
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
        completed_site: str = "gelbooru",
    ) -> None:
        if completed_site != self._site():
            self.page.set_running(False)
            completed_worker = self.sender()
            if isinstance(completed_worker, TaggingWorker):
                completed_worker.deleteLater()
            if self.worker is completed_worker:
                self.worker = None
            self._log(f"site={completed_site} stale search result ignored")
            return
        self.page.set_running(False)
        self.page.spins["start"].setValue(max(1, next_page))
        self.page.show_results(posts)
        if error:
            message = self.catalog.text(f"tagging.error.{error}")
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
        self._log(
            f"site={completed_site} page={max(1, next_page - 1)} posts={len(posts)} "
            f"examined={examined}"
        )
        if self.task_manager and self.task_id:
            self.task_manager.finish(self.task_id, task_state, self.page.state.text())
            self.task_id = None
        if self.worker:
            self.worker.deleteLater()
            self.worker = None
