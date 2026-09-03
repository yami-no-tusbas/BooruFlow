"""Primary Tagging workflow controller.

New orchestration is added here; the fallback controller is frozen in
:mod:`tagging_legacy_controller`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QMessageBox

from booruflow.application.batch_publisher import BatchPublishSummary
from booruflow.application.database_paths import gelbooru_alias_database, gelbooru_tag_database
from booruflow.application.tag_canonicalization import canonicalize_new_gelbooru_tag
from booruflow.application.tag_lookup import exact_tag, lookup_gelbooru_suggestions, lookup_tags
from booruflow.application.tag_policy import is_deprecated
from booruflow.application.tagging import (
    LocalMatchState,
    match_local_tag,
    normalize_booru_tag,
    parse_review_row_token,
)
from booruflow.domain.booru_sites import category_name, site_definition
from booruflow.domain.image_analysis import AnalysisState, DecisionState, ObservationSource
from booruflow.infrastructure.gelbooru_edit_transport import (
    GelbooruSessionExpiredError,
    GelbooruSessionUnknownError,
)
from booruflow.infrastructure.tag_browser import TagRow, exact_tags
from booruflow.presentation.pyside6.tagging_legacy_controller import TaggingLegacyController


def _active_site(controller) -> str:
    """Return the selected item's stable site, then fall back to the selector."""
    selected = getattr(controller, "_current_remote_site", None)
    if selected and getattr(controller, "current_post_id", None) is not None:
        return str(selected)
    return str(getattr(getattr(controller, "page", None), "active_site", "gelbooru"))


def _category_values(controller, category: int | str | None) -> tuple[str, str]:
    name = category_name(_active_site(controller), category)
    native_id = str(category) if category is not None else ""
    return controller.catalog.text(f"tagging.category.{name}"), native_id


def _emit_perf(controller, step: str, started: float) -> None:
    controller._log(
        f"[TaggingPerf] site={_active_site(controller)} "
        f"post={getattr(controller, 'current_post_id', None) or '-'} "
        f"step={step} elapsed_ms={(perf_counter() - started) * 1000:.1f} "
        "gui_thread=true",
        level="DEBUG",
    )


class BatchPublishWorker(QThread):
    progress = Signal(int, int, str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, publisher, retry_ids: list[int] | None = None) -> None:
        super().__init__()
        self.publisher = publisher
        self.retry_ids = retry_ids

    def run(self) -> None:
        try:
            if callable(self.publisher):
                self.publisher = self.publisher()
            if hasattr(self.publisher, "cancel_check"):
                self.publisher.cancel_check = self.isInterruptionRequested
            callback = lambda current, total, post_id: self.progress.emit(current, total, post_id)
            result = (
                self.publisher.retry_failed(self.retry_ids, callback)
                if self.retry_ids is not None
                else self.publisher.publish_pending(callback)
            )
            self.completed.emit(result)
        except Exception as exc:  # noqa: BLE001 - Qt boundary
            self.failed.emit(str(exc))
        finally:
            repository = getattr(self.publisher, "repository", None)
            if repository is not None and hasattr(repository, "close"):
                repository.close()


class SessionTestWorker(QThread):
    completed = Signal(object)

    def __init__(self, factory) -> None:
        super().__init__()
        self.factory = factory

    def run(self) -> None:
        try:
            self.factory.validate()
            self.completed.emit("valid")
        except GelbooruSessionExpiredError:
            self.completed.emit("expired")
        except GelbooruSessionUnknownError:
            self.completed.emit("unknown")
        except Exception as exc:  # noqa: BLE001 - session boundary
            self.completed.emit(f"error:{exc}")


class MultiSiteSessionTestWorker(QThread):
    """Validate each represented site's authentication independently."""

    completed = Signal(object)

    def __init__(self, validators: dict[str, object | None]) -> None:
        super().__init__()
        self.validators = validators

    def run(self) -> None:
        results: dict[str, str] = {}
        for site, validator in self.validators.items():
            if validator is None:
                results[site] = "not_configured"
                continue
            try:
                validate = getattr(validator, "validate_credentials", None)
                (validate if callable(validate) else validator.validate)()
                results[site] = "valid"
            except GelbooruSessionExpiredError:
                results[site] = "expired"
            except GelbooruSessionUnknownError:
                results[site] = "unknown"
            except Exception as exc:  # noqa: BLE001 - authentication UI boundary
                status = getattr(exc, "status", None)
                results[site] = f"error:{f'HTTP {status}' if status else exc}"
        self.completed.emit(results)


@dataclass(frozen=True, slots=True)
class ReviewDecisionChange:
    kind: str
    target: str | int
    before: str
    after: str


@dataclass(frozen=True, slots=True)
class ReviewDecisionOperation:
    item_id: int
    changes: tuple[ReviewDecisionChange, ...]


@dataclass(frozen=True, slots=True)
class ManualAddOperation:
    item_id: int
    observation_id: int


class TaggingController(TaggingLegacyController):
    """Transition entry point for the new Tagging workflow."""

    def __init__(self, *args, **kwargs) -> None:
        self._publisher_factory = kwargs.pop("publisher_factory", None)
        self._session_factory = kwargs.pop("session_factory", None)
        self._session_factory_provider = kwargs.pop("session_factory_provider", None)
        self._e621_validation_factory = kwargs.pop("e621_validation_factory", None)
        self._publication_backend_provider = kwargs.pop("publication_backend_provider", None)
        self._diagnostic_mode_provider = kwargs.pop("diagnostic_mode_provider", None)
        self._http_diagnostic_mode_provider = kwargs.pop("http_diagnostic_mode_provider", None)
        super().__init__(*args, **kwargs)
        self._current_remote_site: str | None = None
        self._undo_stack: list[ReviewDecisionOperation | ManualAddOperation] = []
        self._redo_stack: list[ReviewDecisionOperation | ManualAddOperation] = []
        self.page.undo_requested.connect(self.undo)
        self.page.redo_requested.connect(self.redo)
        self.page.manual_lookup_requested.connect(self.lookup_manual_tags)
        self.page.manual_add_requested.connect(self.add_manual_tag)
        self.page.review_validation_requested.connect(self.validate_current_review)
        self.page.batch_refresh_requested.connect(self.refresh_batch)
        self.page.batch_review_requested.connect(self.review_batch_item)
        self.page.batch_remove_requested.connect(self.remove_batch_items)
        self.page.batch_open_requested.connect(self.open_batch_post)
        self.page.batch_publish_requested.connect(self.publish_batch)
        self.page.batch_retry_requested.connect(self.retry_failed_batch)
        self.page.batch_session_test_requested.connect(self.test_batch_sessions)
        self.page.batch_cancel_requested.connect(self.cancel_batch_publish)
        self.page.reanalyze_requested.connect(self.reanalyze_current)
        self.page.site_changed.connect(self.site_changed)
        self.publish_worker: BatchPublishWorker | None = None
        self.session_test_worker: SessionTestWorker | None = None

    def site_changed(self, site: str) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
        self.current_post_id = None
        self.current_post = {}
        self._current_remote_site = None
        self._local_batch_item_id = None
        self._last_polled_state = None
        self._undo_stack.clear()
        self._redo_stack.clear()
        if self.image_analysis:
            self.page.set_reviewed_post_ids(
                self.image_analysis.repository.reviewed_remote_post_ids(site)
            )
        self._log(f"site={site} context selected")

    def select_post(self, post_id: int, post: dict) -> None:
        started = perf_counter()
        self._local_batch_item_id = None
        self._current_remote_site = str(self.page.active_site)
        super().select_post(post_id, post)
        self._log(
            f"Tagging item selected: site={self._current_remote_site} post_id={post_id}",
            level="DEBUG",
        )
        self._log(
            f"existing_tags={len(str(post.get('tags', '')).split())} local analysis requested"
        )
        _emit_perf(self, "remote_metadata_apply", started)

    def _image_analysis_state_changed(self, state: str, detail: str) -> None:
        if self.current_post_id is None:
            return
        if state in {"failed", "startup_timeout", "unavailable"}:
            self._log(detail or f"ImageAnalysis {state}", level="ERROR")
        elif state in {"starting", "initializing", "restarting"}:
            self._log(detail or f"ImageAnalysis {state}")
        self.refresh_local_review()

    def _worker_pending_label(self) -> str | None:
        if not self.image_analysis:
            return None
        state = getattr(self.image_analysis, "worker_startup_state", "ready")
        detail = getattr(self.image_analysis, "worker_startup_detail", "")
        keys = {
            "starting": "tagging.analysis.worker_starting",
            "initializing": "tagging.analysis.worker_initializing",
            "restarting": "tagging.analysis.worker_restarting",
            "unavailable": "tagging.analysis.unavailable",
            "failed": "tagging.analysis.unavailable",
            "startup_timeout": "tagging.analysis.startup_timeout",
        }
        key = keys.get(state)
        return self.catalog.text(key, detail=detail).strip() if key else None

    def reanalyze_current(self) -> None:
        if not self.image_analysis:
            return
        item_id = self._current_item_id()
        if item_id is None:
            return
        try:
            self.image_analysis.reanalyze_item(item_id)
            # A re-analysis can finish before the next timer tick.  In that
            # case the item returns to the same ready signature that was last
            # observed, so force that tick to rebuild the current review.
            self._last_polled_state = None
            self._undo_stack.clear()
            self._redo_stack.clear()
            self._log("Re-analysis requested with current Image Analysis settings", item_id=item_id)
            self.refresh_local_review()
        except (KeyError, ValueError) as exc:
            self._log(f"Could not re-analyze item: {exc}", level="ERROR", item_id=item_id)
            self.refresh_local_review()

    def refresh_batch(self) -> None:
        if self.image_analysis:
            repository = self.image_analysis.repository
            self.page.show_batch_entries(repository.list_batch_entries())
            credentials = getattr(self, "credentials", dict)().get("e621", {})
            self.page.set_e621_publish_configured(
                isinstance(credentials, dict)
                and bool(str(credentials.get("user_id", "")).strip())
                and bool(str(credentials.get("api_key", "")).strip())
            )
            backend = (
                self._publication_backend_provider()
                if self._publication_backend_provider is not None
                else "configured"
            )
            self.page.set_gelbooru_publish_configured(backend != "disabled")
            try:
                reviewed = repository.reviewed_remote_post_ids(_active_site(self))
            except TypeError:  # compatibility with older lightweight repositories
                reviewed = repository.reviewed_remote_post_ids()
            self.page.set_reviewed_post_ids(reviewed)

    def review_batch_item(self, item_id: int) -> None:
        """Return to the existing review UI without altering its batch snapshot."""
        if not self.image_analysis:
            return
        repository = self.image_analysis.repository
        entry = repository.batch_entry(item_id)
        item = repository.get_item(item_id)
        if entry is None or item is None:
            self.refresh_batch()
            return
        if entry["site"] in {"gelbooru", "e621"} and entry["post_id"]:
            site = str(entry["site"])
            index = self.page.site_selector.findData(site)
            if index >= 0:
                self.page.site_selector.setCurrentIndex(index)
            tags = [
                tag.name
                for tag in repository.source_tags(item_id)
                if tag.source.value == site
            ]
            self.page._open_result_post(
                {
                    "id": int(entry["post_id"]),
                    "tags": " ".join(tags),
                }
            )
            return
        self._local_batch_item_id = item_id
        self.current_post_id = None
        self.current_post = {}
        self.refresh_local_review()

    def remove_batch_items(self, item_ids: list[int]) -> None:
        if not self.image_analysis:
            return
        repository = self.image_analysis.repository
        removed = 0
        for item_id in dict.fromkeys(item_ids):
            entry = repository.batch_entry(item_id)
            if entry is not None:
                removed += int(repository.remove_batch_entry(item_id))
        self._log(f"Batch entries removed: {removed}")
        self.refresh_batch()

    def open_batch_post(self, item_id: int) -> None:
        if not self.image_analysis:
            return
        entry = self.image_analysis.repository.batch_entry(item_id)
        if entry is None or entry["site"] not in {"gelbooru", "e621"} or not entry["post_id"]:
            return
        previous = self.page.active_site
        self.page.active_site = str(entry["site"])
        try:
            self.page._open_post(int(entry["post_id"]))
        finally:
            self.page.active_site = previous

    def publish_batch(self) -> None:
        self._start_batch_publish(None)

    def retry_failed_batch(self, item_ids: list[int]) -> None:
        self._start_batch_publish(item_ids)

    def cancel_batch_publish(self) -> None:
        if self.publish_worker is not None and self.publish_worker.isRunning():
            self.publish_worker.requestInterruption()
            self.page.show_batch_publish_summary(
                self.catalog.text("tagging.publish.cancelling")
            )

    def test_batch_sessions(self) -> None:
        sites = self.page.batch_sites_present()
        if not sites:
            self.page.show_batch_publish_summary(self.catalog.text("tagging.session.none"))
            return
        factory = (
            self._session_factory_provider()
            if self._session_factory_provider is not None
            else self._session_factory
        )
        if self.session_test_worker is not None and self.session_test_worker.isRunning():
            return
        self.page.show_batch_publish_summary(self.catalog.text("tagging.session.testing"))
        validators: dict[str, object | None] = {}
        if "gelbooru" in sites:
            validators["gelbooru"] = factory
        if "e621" in sites:
            validators["e621"] = (
                self._e621_validation_factory()
                if self._e621_validation_factory is not None
                else None
            )
        self.session_test_worker = MultiSiteSessionTestWorker(validators)
        self.session_test_worker.completed.connect(self._session_test_completed)
        self.session_test_worker.start()

    # Compatibility entry point retained for callers outside the batch UI.
    def test_gelbooru_session(self) -> None:
        factory = (
            self._session_factory_provider()
            if self._session_factory_provider is not None
            else self._session_factory
        )
        if factory is None:
            self.page.show_batch_publish_summary(self.catalog.text("tagging.publish.disabled"))
            return
        if self.session_test_worker is not None and self.session_test_worker.isRunning():
            return
        self.session_test_worker = SessionTestWorker(factory)
        self.session_test_worker.completed.connect(self._session_test_completed)
        self.session_test_worker.start()

    def _session_test_completed(self, results: dict[str, str] | str) -> None:
        if isinstance(results, str):
            results = {"gelbooru": results}
        messages = []
        for site, result in results.items():
            key, _, detail = result.partition(":")
            messages.append(
                self.catalog.text(f"tagging.session.{site}.{key}", error=detail)
            )
        self.page.show_batch_publish_summary("\n".join(messages))

    def _start_batch_publish(self, retry_ids: list[int] | None) -> None:
        if self.publish_worker is not None and self.publish_worker.isRunning():
            return
        if not self.image_analysis:
            return
        pending = [
            entry
            for entry in self.image_analysis.repository.list_batch_entries()
            if entry["site"] in {"gelbooru", "e621"}
            and entry["post_id"]
            and str(entry["publish_state"].value)
            == ("failed" if retry_ids is not None else "pending_publish")
            and bool(entry.get("additions") or entry.get("removals"))
            and (retry_ids is None or int(entry["item_id"]) in set(retry_ids))
        ]
        if not pending:
            self.page.show_batch_publish_summary(self.catalog.text("tagging.publish.none"))
            return
        e621_pending = [entry for entry in pending if entry["site"] == "e621"]
        credentials = getattr(self, "credentials", dict)().get("e621", {})
        e621_configured = (
            isinstance(credentials, dict)
            and bool(str(credentials.get("user_id", "")).strip())
            and bool(str(credentials.get("api_key", "")).strip())
        )
        backend = (
            self._publication_backend_provider()
            if getattr(self, "_publication_backend_provider", None) is not None
            else "configured"
        )
        gelbooru_configured = backend != "disabled"
        blocked_count = sum(
            1
            for entry in pending
            if (entry["site"] == "e621" and not e621_configured)
            or (entry["site"] == "gelbooru" and not gelbooru_configured)
        )
        pending = [
            entry
            for entry in pending
            if (entry["site"] == "e621" and e621_configured)
            or (entry["site"] == "gelbooru" and gelbooru_configured)
        ]
        has_gelbooru = any(entry["site"] == "gelbooru" for entry in pending)
        if not pending and e621_pending and not e621_configured:
            self.page.show_batch_publish_summary(
                self.catalog.text("tagging.publish.e621_credentials_missing")
            )
            return
        if not pending:
            self.page.show_batch_publish_summary(self.catalog.text("tagging.publish.disabled"))
            return
        if self._publisher_factory is None:
            self.page.show_batch_publish_summary(
                self.catalog.text("tagging.publish.session_missing")
            )
            return
        additions = sum(len(entry["additions"]) for entry in pending)
        removals = sum(len(entry["removals"]) for entry in pending)
        diagnostic_only = has_gelbooru and bool(
            self._diagnostic_mode_provider()
            if self._diagnostic_mode_provider is not None
            else False
        )
        http_diagnostic = has_gelbooru and bool(
            self._http_diagnostic_mode_provider()
            if self._http_diagnostic_mode_provider is not None
            else False
        )
        if http_diagnostic and len(pending) != 1:
            self.page.show_batch_publish_summary(
                self.catalog.text("tagging.publish.http_requires_one")
            )
            return
        if diagnostic_only:
            title = self.catalog.text("tagging.publish.confirm_diagnostic_title")
            message = self.catalog.text("tagging.publish.confirm_diagnostic", count=len(pending))
        elif http_diagnostic:
            title = self.catalog.text("tagging.publish.confirm_instrumented_title")
            message = self.catalog.text("tagging.publish.confirm_instrumented", count=len(pending), additions=additions, removals=removals)
        else:
            title = self.catalog.text("tagging.publish.confirm_title")
            message = self.catalog.text(
                "tagging.publish.confirm_sites",
                count=len(pending),
                sites=", ".join(sorted({str(entry["site"]) for entry in pending})),
                additions=additions,
                removals=removals,
            )
            if blocked_count:
                message += self.catalog.text(
                    "tagging.publish.confirm_blocked", count=blocked_count
                )
        answer = QMessageBox.question(self.page, title, message)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.page.set_batch_publish_running(True)
        self.publish_worker = BatchPublishWorker(self._publisher_factory, retry_ids)
        self.publish_worker.progress.connect(self.page.set_batch_publish_progress)
        self.publish_worker.completed.connect(self._batch_publish_completed)
        self.publish_worker.failed.connect(self._batch_publish_failed)
        self.publish_worker.start()

    def _batch_publish_completed(self, summary: BatchPublishSummary) -> None:
        self.page.set_batch_publish_running(False)
        suffix = self.catalog.text("tagging.publish.summary.expired") if summary.session_expired else ""
        if summary.session_unknown:
            suffix += self.catalog.text("tagging.publish.summary.unknown")
        if summary.cancelled:
            suffix += self.catalog.text("tagging.publish.summary.cancelled")
        if summary.deferred:
            suffix += self.catalog.text("tagging.publish.summary.deferred")
        if summary.sites:
            suffix += self.catalog.text(
                "tagging.publish.summary.sites", sites=", ".join(summary.sites)
            )
        self.page.show_batch_publish_summary(
            self.catalog.text("tagging.publish.summary", total=summary.total, published=summary.published, no_op=summary.no_op, failed=summary.failed, suffix=suffix)
        )
        self.refresh_batch()

    def _batch_publish_failed(self, error: str) -> None:
        self.page.set_batch_publish_running(False)
        self.page.show_batch_publish_summary(self.catalog.text("tagging.publish.interrupted", error=error))
        self.refresh_batch()

    def validate_current_review(self) -> None:
        """Save the review snapshot locally, then move within the Tagging pool.

        This deliberately has no network, browser, or publisher dependency.
        """
        item_id = self._current_item_id()
        if item_id is None or not self.image_analysis:
            return
        try:
            repository = self.image_analysis.repository
            local_entry = repository.batch_entry(item_id)
            original_tags = (
                list(local_entry["original_tags"])
                if getattr(self, "_local_batch_item_id", None) == item_id and local_entry
                else [
                    tag.name
                    for tag in repository.source_tags(item_id)
                    if tag.source.value == _active_site(self)
                ]
                or list(self.current_post.get("tags", "").split())
            )
            summary = repository.tag_review_summary(item_id, original_tags)
            state = repository.save_review_batch_entry(
                item_id,
                original_tags=summary["original_tags"],
                additions=summary["additions"],
                removals=summary["removals"],
                reviewed_final_tags=summary["final_tags"],
            )
            self._log(
                f"Review item: site={_active_site(self)} post_id={self.current_post_id}",
                level="DEBUG",
                item_id=item_id,
            )
            item = repository.get_item(item_id)
            if item is not None and item.state is AnalysisState.READY_FOR_REVIEW:
                repository.finish_review(item_id, AnalysisState.REVIEWED)
            self._log(f"Review snapshot saved ({state.value})", item_id=item_id)
            self.refresh_pool()
            self.refresh_batch()
            self._advance_after_validation(item_id)
        except Exception as exc:  # noqa: BLE001 - Qt action boundary
            self._log(f"Could not validate review: {exc}", level="ERROR", item_id=item_id)
            self.page.show_review_completion(f"Validation locale impossible : {exc}")

    def _advance_after_validation(self, item_id: int) -> None:
        repository = self.image_analysis.repository
        if self.current_post_id is not None and self.page.mark_reviewed_and_advance(
            self.current_post_id
        ):
            return
        scope = (
            str(self.page.pool_scope.currentData()) if hasattr(self.page, "pool_scope") else "all"
        )
        next_item = repository.next_tagging_pool_item(item_id, scope)
        if next_item is None:
            self.page.show_review_completion(self.catalog.text("tagging.review.pool_finished"))
            return
        site, post_id = next_item["source_site"], next_item["source_post_id"]
        if site in {"gelbooru", "e621"} and post_id:
            self.page.active_site = site
            index = self.page.site_selector.findData(site)
            self.page.site_selector.blockSignals(True)
            self.page.site_selector.setCurrentIndex(index)
            self.page.site_selector.blockSignals(False)
            tags = [
                tag.name
                for tag in repository.source_tags(int(next_item["id"]))
                if tag.source.value == site
            ]
            self.page._open_result_post({"id": int(post_id), "tags": " ".join(tags)})
            return
        self.page.show_review_completion(
            self.catalog.text("tagging.review.next_local", item_id=int(next_item["id"]))
        )

    def _tag_database(self) -> Path | None:
        if not self.image_analysis:
            return None
        site = _active_site(self)
        if site == "gelbooru":
            database = gelbooru_tag_database(self.image_analysis.settings)
        else:
            value = str(
                self.image_analysis.settings.get(site_definition(site).database_setting_key, "")
            ).strip()
            database = Path(value) if value else None
        self._log(
            f"Tag lookup backend: site={site} database={database or 'not-configured'}",
            level="DEBUG",
        )
        return database

    def _alias_database(self) -> Path | None:
        return gelbooru_alias_database(self.image_analysis.settings) if self.image_analysis else None

    def lookup_manual_tags(self, text: str) -> None:
        started = perf_counter()
        database = self._tag_database()
        if database is None or not text.strip():
            self.page.set_manual_suggestions([])
            return
        try:
            if _active_site(self) == "gelbooru":
                rows = lookup_gelbooru_suggestions(
                    database, self._alias_database(), text.strip(), limit=20
                )
                self.page.set_manual_suggestions([(row.value, row.alias_source) for row in rows])
            else:
                rows = lookup_tags("e621", database, text.strip(), limit=20)
                self.page.set_manual_suggestions([row.name for row in rows])
        except (FileNotFoundError, ValueError) as exc:
            self.page.set_manual_suggestions([])
            self._log(f"Manual tag lookup unavailable: {exc}", level="WARNING")
        finally:
            _emit_perf(self, "manual_tag_lookup", started)

    def _eligible_exact_name(self, value: str) -> str | None:
        database = self._tag_database()
        if database is None:
            return None
        row = exact_tag(_active_site(self), database, normalize_booru_tag(value))
        return row.name if row is not None else None

    def add_manual_tag(self, value: str) -> None:
        started = perf_counter()
        if not self.image_analysis:
            return
        item_id = self._current_item_id()
        if item_id is None:
            return
        try:
            alias_database = getattr(self, "_alias_database", lambda: None)()
            requested_name = (
                canonicalize_new_gelbooru_tag(value, alias_database).canonical_name
                if _active_site(self) == "gelbooru"
                else normalize_booru_tag(value)
            )
            name = self._eligible_exact_name(requested_name)
            if name is None:
                self._log(
                    f"Manual tag rejected; absent or deprecated: {value.strip()}",
                    level="WARNING",
                )
                self.page.analysis_state.setText(self.catalog.text("tagging.review.tag_unavailable"))
                return
            normalized = normalize_booru_tag(name)
            repository = self.image_analysis.repository
            source_names = {
                normalize_booru_tag(tag.name): tag.name for tag in repository.source_tags(item_id)
            }
            from booruflow.infrastructure.gelbooru_tagging import post_tags

            source_names.update(
                {normalize_booru_tag(tag): tag for tag in post_tags(self.current_post)}
            )
            observations = repository.observations(item_id)
            duplicate = next(
                (
                    (observation_id, observation)
                    for observation_id, observation in observations
                    if normalize_booru_tag(observation.reviewed_name or observation.name)
                    == normalized
                ),
                None,
            )
            if normalized in source_names:
                existing_name = source_names[normalized]
                before = repository.existing_tag_decision(item_id, existing_name)
                if before == "remove":
                    operation = ReviewDecisionOperation(
                        item_id,
                        (ReviewDecisionChange("existing", existing_name, "remove", "keep"),),
                    )
                    self._apply_changes(item_id, operation.changes, undo=False)
                    self._undo_stack.append(operation)
                    self._redo_stack.clear()
                    self.refresh_local_review()
                self.page.clear_manual_entry()
                self._log(f"Existing tag restored or already kept: {existing_name}")
                return
            if duplicate is not None:
                observation_id, observation = duplicate
                if observation.decision is not DecisionState.ACCEPTED:
                    operation = ReviewDecisionOperation(
                        item_id,
                        (
                            ReviewDecisionChange(
                                "observation",
                                observation_id,
                                observation.decision.value,
                                DecisionState.ACCEPTED.value,
                            ),
                        ),
                    )
                    self._apply_changes(item_id, operation.changes, undo=False)
                    self._undo_stack.append(operation)
                    self._redo_stack.clear()
                self.page.clear_manual_entry()
                self._log(f"Existing suggestion reused for manual tag: {name}")
                self.refresh_local_review()
                return
            observation_id = self.image_analysis.workflow.add_manual_tag(item_id, name)
            self._undo_stack.append(ManualAddOperation(item_id, observation_id))
            self._redo_stack.clear()
            self.page.clear_manual_entry()
            self._log(f"Manual tag added: {name}")
            self.refresh_local_review()
        except (FileNotFoundError, ValueError) as exc:
            self._log(f"Could not add manual tag: {exc}", level="ERROR")
            self.page.analysis_state.setText(self.catalog.text("tagging.review.manual_error", error=exc))
        finally:
            _emit_perf(self, "manual_tag_insertion", started)

    def _current_item_id(self) -> int | None:
        if not self.image_analysis:
            return None
        local_item_id = getattr(self, "_local_batch_item_id", None)
        if local_item_id is not None and self.current_post_id is None:
            return int(local_item_id)
        if self.current_post_id is None:
            return None
        item = self.image_analysis.repository.item_by_remote_source(
            _active_site(self), str(self.current_post_id)
        )
        return item.id if item is not None else None

    @staticmethod
    def _unique_targets(tokens: object) -> list[tuple[str, str | int]]:
        values = tokens if isinstance(tokens, list) else [tokens]
        targets: list[tuple[str, str | int]] = []
        for value in values:
            target = parse_review_row_token(value)
            if target not in targets:
                targets.append(target)
        return targets

    def _apply_changes(
        self, item_id: int, changes: tuple[ReviewDecisionChange, ...], *, undo: bool
    ) -> None:
        repository = self.image_analysis.repository
        for change in changes:
            decision = change.before if undo else change.after
            if change.kind == "existing":
                repository.set_existing_tag_decision(item_id, str(change.target), decision)
            else:
                self.image_analysis.workflow.decide(
                    int(change.target), DecisionState(decision), None
                )

    def _apply_operation(
        self, operation: ReviewDecisionOperation | ManualAddOperation, *, undo: bool
    ) -> None:
        if isinstance(operation, ManualAddOperation):
            self.image_analysis.workflow.decide(
                operation.observation_id,
                DecisionState.REJECTED if undo else DecisionState.ACCEPTED,
                None,
            )
        else:
            self._apply_changes(operation.item_id, operation.changes, undo=undo)

    def decide(self, observation_id: object, value: str) -> None:
        if not self.image_analysis:
            return
        item_id = self._current_item_id()
        if item_id is None:
            return
        try:
            repository = self.image_analysis.repository
            observations = dict(repository.observations(item_id))
            changes: list[ReviewDecisionChange] = []
            for kind, target in self._unique_targets(observation_id):
                if kind == "existing":
                    before = repository.existing_tag_decision(item_id, str(target))
                    after = "keep" if value == "accepted" else "remove"
                else:
                    before = observations[int(target)].decision.value
                    after = (
                        DecisionState.ACCEPTED.value
                        if value == "accepted"
                        else DecisionState.REJECTED.value
                    )
                if before != after:
                    changes.append(ReviewDecisionChange(kind, target, before, after))
            if not changes:
                return
            operation = ReviewDecisionOperation(item_id, tuple(changes))
            self._apply_changes(item_id, operation.changes, undo=False)
            self._undo_stack.append(operation)
            self._redo_stack.clear()
            self._log(f"Review entries {len(changes)} {value}")
            self.refresh_local_review()
        except Exception as exc:  # noqa: BLE001 - Qt action boundary
            self._log(f"Could not save decision {observation_id}: {exc}", level="ERROR")
            self.page.analysis_state.setText(self.catalog.text("tagging.review.decision_error", error=exc))

    def undo(self) -> None:
        if not self._undo_stack or not self.image_analysis:
            return
        operation = self._undo_stack.pop()
        try:
            if isinstance(operation, ManualAddOperation):
                self.image_analysis.workflow.decide(
                    operation.observation_id, DecisionState.REJECTED, None
                )
            else:
                self._apply_changes(operation.item_id, operation.changes, undo=True)
            self._redo_stack.append(operation)
            count = len(operation.changes) if isinstance(operation, ReviewDecisionOperation) else 1
            self._log(f"Undo review entries {count}")
            self.refresh_local_review()
        except Exception as exc:  # noqa: BLE001 - Qt action boundary
            self._undo_stack.append(operation)
            self._log(f"Could not undo review decision: {exc}", level="ERROR")

    def redo(self) -> None:
        if not self._redo_stack or not self.image_analysis:
            return
        operation = self._redo_stack.pop()
        try:
            if isinstance(operation, ManualAddOperation):
                self.image_analysis.workflow.decide(
                    operation.observation_id, DecisionState.ACCEPTED, None
                )
            else:
                self._apply_changes(operation.item_id, operation.changes, undo=False)
            self._undo_stack.append(operation)
            count = len(operation.changes) if isinstance(operation, ReviewDecisionOperation) else 1
            self._log(f"Redo review entries {count}")
            self.refresh_local_review()
        except Exception as exc:  # noqa: BLE001 - Qt action boundary
            self._redo_stack.append(operation)
            self._log(f"Could not redo review decision: {exc}", level="ERROR")

    def _local_names(self, names: list[str]) -> set[str]:
        result: set[str] = set()
        for name in names:
            try:
                eligible = self._eligible_exact_name(name)
            except FileNotFoundError:
                return set()
            if eligible:
                result.add(eligible)
        return result

    def _local_tag_rows(self, names: list[str]) -> dict[str, TagRow]:
        started = perf_counter()
        database = self._tag_database()
        if database is None:
            self._local_tag_lookup_available = False
            return {}
        self._local_tag_lookup_available = True
        try:
            rows = exact_tags(database, names)
        except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
            self._local_tag_lookup_available = False
            self._log(
                f"Local tag lookup unavailable: site={_active_site(self)} path={database} ({exc})",
                level="DEBUG",
            )
            return {}
        finally:
            _emit_perf(self, "local_tag_lookup", started)
        return {normalize_booru_tag(row.name): row for row in rows}

    def refresh_local_review(self) -> None:
        """Render one row per normalized existing, WD14, or manual tag."""
        started = perf_counter()
        if not self.image_analysis:
            return
        repository = self.image_analysis.repository
        local_item_id = getattr(self, "_local_batch_item_id", None)
        local_review = local_item_id is not None and self.current_post_id is None
        item = (
            repository.get_item(int(local_item_id))
            if local_review
            else repository.item_by_remote_source(_active_site(self), str(self.current_post_id))
            if self.current_post_id is not None
            else None
        )
        if item is None:
            if hasattr(self.page, "set_reanalyze_available"):
                self.page.set_reanalyze_available(False)
            return super().refresh_local_review()
        # `_local_tag_rows` sets this to false if the configured catalogue is
        # unavailable.  A missing catalogue must not masquerade as a missing
        # individual tag in the review table.
        self._local_tag_lookup_available = True
        from booruflow.infrastructure.gelbooru_tagging import post_tags

        entry = repository.batch_entry(item.id) if hasattr(repository, "batch_entry") else None
        current_tags = (
            set(entry["original_tags"])
            if local_review and entry is not None
            else set(post_tags(self.current_post))
        )
        persisted = {
            tag.name
            for tag in repository.source_tags(item.id)
            if tag.source.value == _active_site(self)
        }
        if persisted:
            current_tags = persisted
        summary = (
            repository.tag_review_summary(item.id, sorted(current_tags))
            if hasattr(repository, "tag_review_summary")
            else {"removals": [], "final_tags": sorted(current_tags)}
        )
        rows: dict[str, dict] = {}
        local_rows = getattr(self, "_local_tag_rows", lambda _names: {})(list(current_tags))
        api_categories = self.current_post.get("_tag_categories", {})
        persisted_categories = {
            normalize_booru_tag(tag.name): tag.category
            for tag in repository.source_tags(item.id)
            if tag.source.value == _active_site(self)
        }
        for tag in sorted(current_tags):
            key = normalize_booru_tag(tag)
            native_category = persisted_categories.get(
                key,
                api_categories.get(tag) if isinstance(api_categories, dict) else None,
            )
            if native_category is None and key in local_rows:
                native_category = local_rows[key].category
            category_label, category_id = _category_values(self, native_category)
            rows[key] = {
                "id": f"existing:{tag}",
                "tag": tag,
                "confidence": "",
                "decision": "remove" if tag in summary["removals"] else "keep",
                "match": self.catalog.text("tagging.match.existing"),
                "category": category_label,
                "category_id": category_id,
            }
        from booruflow.application.tagging import is_rating_observation

        observations = [
            row
            for row in repository.observations(item.id)
            if row[1].source in {ObservationSource.WD14, ObservationSource.MANUAL}
            and not is_rating_observation(row[1].name, row[1].category)
        ]
        mapped_names = []
        mappings: dict[int, str | None] = {}
        for observation_id, observation in observations:
            mapping = (
                repository.tag_mapping("wd14", observation.name, _active_site(self))
                if observation.source is ObservationSource.WD14
                else None
            )
            mappings[observation_id] = mapping
            mapped_names.extend(
                filter(None, (observation.reviewed_name or observation.name, mapping))
            )
        suggestion_rows = getattr(self, "_local_tag_rows", lambda _names: {})(mapped_names)
        local_lookup_available = getattr(self, "_local_tag_lookup_available", True)
        local = {
            row.name
            for row in suggestion_rows.values()
            if not is_deprecated(_active_site(self), row.category)
        }
        for observation_id, observation in observations:
            name = observation.reviewed_name or observation.name
            match = match_local_tag(name, local, current_tags, mappings[observation_id])
            display_name = match.target_tag or normalize_booru_tag(name)
            key = normalize_booru_tag(display_name)
            existing = rows.get(key)
            confidence = "" if observation.confidence is None else f"{observation.confidence:.3f}"
            if existing is not None:
                if confidence and (
                    not existing["confidence"] or float(confidence) > float(existing["confidence"])
                ):
                    existing["confidence"] = confidence
                origins = self.catalog.text("tagging.match.manual") if observation.source is ObservationSource.MANUAL else "WD14"
                if origins not in existing["match"]:
                    existing["match"] += self.catalog.text("tagging.match.also", origin=origins)
                continue
            tag_row = suggestion_rows.get(key)
            if tag_row is not None and is_deprecated(_active_site(self), tag_row.category):
                continue
            origin = (
                self.catalog.text("tagging.match.manual")
                if observation.source is ObservationSource.MANUAL
                else {
                    LocalMatchState.EXACT: "exact",
                    LocalMatchState.MAPPING: f"mapping → {match.target_tag}",
                    LocalMatchState.MISSING: (
                        self.catalog.text("tagging.match.missing")
                        if local_lookup_available
                        else self.catalog.text("tagging.match.lookup_unavailable")
                    ),
                    LocalMatchState.ALREADY_PRESENT: self.catalog.text("tagging.match.already_present"),
                }[match.state]
            )
            category_label, category_id = _category_values(
                self,
                tag_row.category if tag_row is not None else None
            )
            rows[key] = {
                "id": observation_id,
                "tag": display_name,
                "confidence": confidence,
                "decision": observation.decision.value,
                "match": origin,
                "category": category_label,
                "category_id": category_id,
            }
        labels = {
            "pending": self.catalog.text("tagging.analysis.pending"),
            "processing": self.catalog.text("tagging.analysis.processing"),
            "ready_for_review": self.catalog.text("tagging.analysis.ready"),
            "reviewed": self.catalog.text("tagging.analysis.reviewed"),
            "failed": self.catalog.text("tagging.analysis.failed", error=item.last_error or self.catalog.text("tagging.unknown")),
            "skipped": self.catalog.text("tagging.analysis.skipped"),
        }
        if item.state.value == "pending":
            labels["pending"] = self._worker_pending_label() or labels["pending"]
        if entry is not None:
            labels[item.state.value] += self.catalog.text("tagging.analysis.validated")
        self.page.show_local_review(
            labels[item.state.value],
            item.cached_path,
            sorted(current_tags),
            list(rows.values()),
            [],
            summary["final_tags"],
        )
        if hasattr(self.page, "set_reanalyze_available"):
            self.page.set_reanalyze_available(
                True,
                busy=item.state.value in {"pending", "processing"},
            )
        if local_review:
            self.page._batch_local_item_id = item.id
            self.page.review_title.setText(self.catalog.text("tagging.review.local_item", item_id=item.id))
            self.page.copy_open_button.setEnabled(True)
        _emit_perf(self, "review_refresh_total", started)
