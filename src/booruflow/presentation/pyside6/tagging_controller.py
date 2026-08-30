"""Primary Tagging workflow controller.

New orchestration is added here; the fallback controller is frozen in
:mod:`tagging_legacy_controller`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QMessageBox

from booruflow.application.batch_publisher import BatchPublishSummary
from booruflow.application.tag_lookup import exact_tag, lookup_tags
from booruflow.application.tag_policy import is_deprecated
from booruflow.application.tagging import (
    LocalMatchState,
    match_local_tag,
    normalize_booru_tag,
    parse_review_row_token,
)
from booruflow.domain.image_analysis import AnalysisState, DecisionState, ObservationSource
from booruflow.infrastructure.gelbooru_edit_transport import (
    GelbooruSessionExpiredError,
    GelbooruSessionUnknownError,
)
from booruflow.infrastructure.tag_browser import TagRow, TagSearch, search_tags
from booruflow.presentation.pyside6.tagging_legacy_controller import TaggingLegacyController


class BatchPublishWorker(QThread):
    progress = Signal(int, int, str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, publisher, retry_ids: list[int] | None = None) -> None:
        super().__init__(); self.publisher = publisher; self.retry_ids = retry_ids

    def run(self) -> None:
        try:
            if callable(self.publisher):
                self.publisher = self.publisher()
            if hasattr(self.publisher, "cancel_check"):
                self.publisher.cancel_check = self.isInterruptionRequested
            callback = lambda current, total, post_id: self.progress.emit(current, total, post_id)
            result = self.publisher.retry_failed(self.retry_ids, callback) if self.retry_ids is not None else self.publisher.publish_pending(callback)
            self.completed.emit(result)
        except Exception as exc:  # noqa: BLE001 - Qt boundary
            self.failed.emit(str(exc))
        finally:
            repository = getattr(self.publisher, "repository", None)
            if repository is not None and hasattr(repository, "close"):
                repository.close()


class SessionTestWorker(QThread):
    completed = Signal(str)

    def __init__(self, factory) -> None:
        super().__init__(); self.factory = factory

    def run(self) -> None:
        try:
            self.factory.validate()
            self.completed.emit("Session Gelbooru valide.")
        except GelbooruSessionExpiredError:
            self.completed.emit("Session Gelbooru non connectée.")
        except GelbooruSessionUnknownError:
            self.completed.emit(
                "État de session Gelbooru indéterminé. Ouvrez la session Gelbooru et "
                "vérifiez que la page est chargée et connectée."
            )
        except Exception as exc:  # noqa: BLE001 - session boundary
            self.completed.emit(f"Session Gelbooru non disponible : {exc}")


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
        self._publication_backend_provider = kwargs.pop("publication_backend_provider", None)
        self._diagnostic_mode_provider = kwargs.pop("diagnostic_mode_provider", None)
        self._http_diagnostic_mode_provider = kwargs.pop(
            "http_diagnostic_mode_provider", None
        )
        super().__init__(*args, **kwargs)
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
        self.page.batch_session_test_requested.connect(self.test_gelbooru_session)
        self.page.batch_cancel_requested.connect(self.cancel_batch_publish)
        self.publish_worker: BatchPublishWorker | None = None
        self.session_test_worker: SessionTestWorker | None = None

    def select_post(self, post_id: int, post: dict) -> None:
        self._local_batch_item_id = None
        super().select_post(post_id, post)

    def refresh_batch(self) -> None:
        if self.image_analysis:
            repository = self.image_analysis.repository
            self.page.show_batch_entries(repository.list_batch_entries())
            self.page.set_reviewed_post_ids(repository.reviewed_remote_post_ids())

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
        if entry["site"] == "gelbooru" and entry["post_id"]:
            tags = [
                tag.name for tag in repository.source_tags(item_id)
                if tag.source is ObservationSource.GELBOORU
            ]
            self.page._open_result_post({
                "id": int(entry["post_id"]), "tags": " ".join(tags),
            })
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
        if entry is None or entry["site"] != "gelbooru" or not entry["post_id"]:
            return
        self.page._open_post(int(entry["post_id"]))

    def publish_batch(self) -> None:
        self._start_batch_publish(None)

    def retry_failed_batch(self, item_ids: list[int]) -> None:
        self._start_batch_publish(item_ids)

    def cancel_batch_publish(self) -> None:
        if self.publish_worker is not None and self.publish_worker.isRunning():
            self.publish_worker.requestInterruption()
            self.page.show_batch_publish_summary(
                "Annulation demandée — le post courant se termine, aucun suivant ne sera lancé."
            )

    def test_gelbooru_session(self) -> None:
        factory = (
            self._session_factory_provider()
            if self._session_factory_provider is not None
            else self._session_factory
        )
        backend = (
            self._publication_backend_provider()
            if self._publication_backend_provider is not None
            else "configured"
        )
        if backend == "cdp":
            backend = "browser-cdp"
        self.log(f"Gelbooru session test: backend={backend}")
        if factory is None:
            self.page.show_batch_publish_summary("Publication Gelbooru désactivée.")
            return
        if self.session_test_worker is not None and self.session_test_worker.isRunning():
            return
        self.page.show_batch_publish_summary("Test de la session Gelbooru…")
        self.session_test_worker = SessionTestWorker(factory)
        self.session_test_worker.completed.connect(self.page.show_batch_publish_summary)
        self.session_test_worker.start()

    def _start_batch_publish(self, retry_ids: list[int] | None) -> None:
        if self.publish_worker is not None and self.publish_worker.isRunning():
            return
        if not self.image_analysis:
            return
        pending = [entry for entry in self.image_analysis.repository.list_batch_entries()
                   if entry["site"] == "gelbooru" and entry["post_id"]
                   and str(entry["publish_state"].value) == ("failed" if retry_ids is not None else "pending_publish")
                   and (retry_ids is None or int(entry["item_id"]) in set(retry_ids))]
        if not pending:
            self.page.show_batch_publish_summary("Aucune publication Gelbooru éligible.")
            return
        if self._publisher_factory is None:
            self.page.show_batch_publish_summary("Session Gelbooru authentifiée non configurée : aucune publication envoyée.")
            return
        additions = sum(len(entry["additions"]) for entry in pending)
        removals = sum(len(entry["removals"]) for entry in pending)
        diagnostic_only = bool(
            self._diagnostic_mode_provider()
            if self._diagnostic_mode_provider is not None else False
        )
        http_diagnostic = bool(
            self._http_diagnostic_mode_provider()
            if self._http_diagnostic_mode_provider is not None else False
        )
        if http_diagnostic and len(pending) != 1:
            self.page.show_batch_publish_summary(
                "Le diagnostic HTTP réel exige exactement une entrée Gelbooru "
                "en attente. Aucun envoi effectué."
            )
            return
        if diagnostic_only:
            title = "Confirmer le diagnostic"
            message = (
                f"Inspecter le formulaire Gelbooru de {len(pending)} entrée(s) ?\n\n"
                "Les tags seront appliqués uniquement au DOM local pour capturer FormData. "
                "Le diagnostic s'arrêtera avant tout submit et toute requête d'édition."
            )
        elif http_diagnostic:
            title = "Confirmer la publication instrumentée"
            message = (
                f"Envoyer réellement {len(pending)} modification(s) sur Gelbooru ?\n\n"
                f"{additions} ajout(s), {removals} retrait(s).\n\n"
                "La trace HTTP est passive et se désarme après le premier POST. "
                "Elle ne déclenche aucun envoi par elle-même."
            )
        else:
            title = "Confirmer la publication"
            message = (
                f"Publier {len(pending)} modification(s) sur Gelbooru ?\n\n"
                f"{additions} ajout(s), {removals} retrait(s)."
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
        suffix = " · session expirée, lot mis en pause" if summary.session_expired else ""
        if summary.session_unknown:
            suffix += " · état de session indéterminé, lot mis en pause"
        if summary.cancelled:
            suffix += " · annulé avant le post suivant"
        if summary.deferred:
            suffix += " · diagnostic terminé, aucun envoi"
        self.page.show_batch_publish_summary(
            f"{summary.total} entrées · {summary.published} publiées · {summary.no_op} no-op · {summary.failed} échecs{suffix}"
        )
        self.refresh_batch()

    def _batch_publish_failed(self, error: str) -> None:
        self.page.set_batch_publish_running(False)
        self.page.show_batch_publish_summary(f"Publication interrompue : {error}")
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
                    tag.name for tag in repository.source_tags(item_id)
                    if tag.source is ObservationSource.GELBOORU
                ] or list(self.current_post.get("tags", "").split())
            )
            summary = repository.tag_review_summary(item_id, original_tags)
            state = repository.save_review_batch_entry(
                item_id,
                original_tags=summary["original_tags"], additions=summary["additions"],
                removals=summary["removals"], reviewed_final_tags=summary["final_tags"],
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
        scope = str(self.page.pool_scope.currentData()) if hasattr(self.page, "pool_scope") else "all"
        next_item = repository.next_tagging_pool_item(item_id, scope)
        if next_item is None:
            self.page.show_review_completion("Revue enregistrée — pool terminé.")
            return
        site, post_id = next_item["source_site"], next_item["source_post_id"]
        if site == "gelbooru" and post_id:
            tags = [
                tag.name for tag in repository.source_tags(int(next_item["id"]))
                if tag.source is ObservationSource.GELBOORU
            ]
            self.page._open_result_post({"id": int(post_id), "tags": " ".join(tags)})
            return
        self.page.show_review_completion(
            f"Revue enregistrée — prochain item local #{int(next_item['id'])}."
        )

    def _tag_database(self) -> Path | None:
        if not self.image_analysis:
            return None
        value = str(self.image_analysis.settings.get("gelbooru_database", ""))
        return Path(value) if value else None

    def lookup_manual_tags(self, text: str) -> None:
        database = self._tag_database()
        if database is None or not text.strip():
            self.page.set_manual_suggestions([])
            return
        try:
            rows = lookup_tags("gelbooru", database, text.strip(), limit=20)
            self.page.set_manual_suggestions([row.name for row in rows])
        except (FileNotFoundError, ValueError) as exc:
            self.page.set_manual_suggestions([])
            self._log(f"Manual tag lookup unavailable: {exc}", level="WARNING")

    def _eligible_exact_name(self, value: str) -> str | None:
        database = self._tag_database()
        if database is None:
            return None
        row = exact_tag("gelbooru", database, normalize_booru_tag(value))
        return row.name if row is not None else None

    def add_manual_tag(self, value: str) -> None:
        if not self.image_analysis:
            return
        item_id = self._current_item_id()
        if item_id is None:
            return
        try:
            name = self._eligible_exact_name(value)
            if name is None:
                self._log(
                    f"Manual tag rejected; absent or deprecated: {value.strip()}",
                    level="WARNING",
                )
                self.page.analysis_state.setText("Tag absent ou deprecated dans la base Gelbooru.")
                return
            normalized = normalize_booru_tag(name)
            repository = self.image_analysis.repository
            source_names = {
                normalize_booru_tag(tag.name): tag.name
                for tag in repository.source_tags(item_id)
            }
            from booruflow.infrastructure.gelbooru_tagging import post_tags
            source_names.update({
                normalize_booru_tag(tag): tag for tag in post_tags(self.current_post)
            })
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
                        (ReviewDecisionChange(
                            "existing", existing_name, "remove", "keep"
                        ),),
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
                        (ReviewDecisionChange(
                            "observation", observation_id,
                            observation.decision.value, DecisionState.ACCEPTED.value,
                        ),),
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
            self.page.analysis_state.setText(f"Ajout manuel impossible : {exc}")

    def _current_item_id(self) -> int | None:
        if not self.image_analysis:
            return None
        local_item_id = getattr(self, "_local_batch_item_id", None)
        if local_item_id is not None and self.current_post_id is None:
            return int(local_item_id)
        if self.current_post_id is None:
            return None
        item = self.image_analysis.repository.item_by_remote_source(
            "gelbooru", str(self.current_post_id)
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

    def _apply_changes(self, item_id: int, changes: tuple[ReviewDecisionChange, ...], *, undo: bool) -> None:
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
                        if value == "accepted" else DecisionState.REJECTED.value
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
            self.page.analysis_state.setText(f"Erreur de décision : {exc}")

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
        database = self._tag_database()
        if database is None:
            return {}
        result: dict[str, TagRow] = {}
        for name in dict.fromkeys(names):
            try:
                rows = search_tags(database, TagSearch(text=name, mode="exact", limit=1))
            except FileNotFoundError:
                return {}
            if rows:
                result[normalize_booru_tag(rows[0].name)] = rows[0]
        return result

    def refresh_local_review(self) -> None:
        """Render one row per normalized existing, WD14, or manual tag."""
        if not self.image_analysis:
            return
        repository = self.image_analysis.repository
        local_item_id = getattr(self, "_local_batch_item_id", None)
        local_review = local_item_id is not None and self.current_post_id is None
        item = (
            repository.get_item(int(local_item_id)) if local_review
            else repository.item_by_remote_source("gelbooru", str(self.current_post_id))
            if self.current_post_id is not None else None
        )
        if item is None:
            return super().refresh_local_review()
        from booruflow.infrastructure.gelbooru_tagging import post_tags

        entry = repository.batch_entry(item.id) if hasattr(repository, "batch_entry") else None
        current_tags = (
            set(entry["original_tags"]) if local_review and entry is not None
            else set(post_tags(self.current_post))
        )
        persisted = {
            tag.name for tag in repository.source_tags(item.id)
            if tag.source is ObservationSource.GELBOORU
        }
        if persisted:
            current_tags = persisted
        summary = (
            repository.tag_review_summary(item.id, sorted(current_tags))
            if hasattr(repository, "tag_review_summary")
            else {"removals": [], "final_tags": sorted(current_tags)}
        )
        rows: dict[str, dict] = {}
        local_rows = getattr(self, "_local_tag_rows", lambda _names: {})(
            list(current_tags)
        )
        for tag in sorted(current_tags):
            key = normalize_booru_tag(tag)
            rows[key] = {
                "id": f"existing:{tag}", "tag": tag, "confidence": "",
                "decision": "remove" if tag in summary["removals"] else "keep",
                "match": "Existant",
                "category": str(local_rows[key].category) if key in local_rows else "",
            }
        from booruflow.application.tagging import is_rating_observation

        observations = [
            row for row in repository.observations(item.id)
            if row[1].source in {ObservationSource.WD14, ObservationSource.MANUAL}
            and not is_rating_observation(row[1].name, row[1].category)
        ]
        mapped_names = []
        mappings: dict[int, str | None] = {}
        for observation_id, observation in observations:
            mapping = (
                repository.tag_mapping("wd14", observation.name, "gelbooru")
                if observation.source is ObservationSource.WD14 else None
            )
            mappings[observation_id] = mapping
            mapped_names.extend(filter(None, (observation.reviewed_name or observation.name, mapping)))
        suggestion_rows = getattr(self, "_local_tag_rows", lambda _names: {})(
            mapped_names
        )
        local = {
            row.name for row in suggestion_rows.values()
            if not is_deprecated("gelbooru", row.category)
        }
        for observation_id, observation in observations:
            name = observation.reviewed_name or observation.name
            match = match_local_tag(name, local, current_tags, mappings[observation_id])
            display_name = match.target_tag or normalize_booru_tag(name)
            key = normalize_booru_tag(display_name)
            existing = rows.get(key)
            confidence = "" if observation.confidence is None else f"{observation.confidence:.3f}"
            if existing is not None:
                if confidence and (not existing["confidence"] or float(confidence) > float(existing["confidence"])):
                    existing["confidence"] = confidence
                origins = "manuel" if observation.source is ObservationSource.MANUAL else "WD14"
                if origins not in existing["match"]:
                    existing["match"] += f" · également {origins}"
                continue
            tag_row = suggestion_rows.get(key)
            if tag_row is not None and is_deprecated("gelbooru", tag_row.category):
                continue
            origin = "manuel" if observation.source is ObservationSource.MANUAL else {
                LocalMatchState.EXACT: "exact",
                LocalMatchState.MAPPING: f"mapping → {match.target_tag}",
                LocalMatchState.MISSING: "introuvable localement",
                LocalMatchState.ALREADY_PRESENT: "déjà présent",
            }[match.state]
            rows[key] = {
                "id": observation_id, "tag": display_name, "confidence": confidence,
                "decision": observation.decision.value, "match": origin,
                "category": str(tag_row.category) if tag_row is not None else "",
            }
        labels = {
            "pending": "Analyse en attente", "processing": "Analyse en cours",
            "ready_for_review": "Analyse disponible", "reviewed": "Déjà analysée",
            "failed": f"Erreur d’analyse : {item.last_error or 'inconnue'}",
            "skipped": "Analyse ignorée",
        }
        if entry is not None:
            labels[item.state.value] += " · déjà validée"
        self.page.show_local_review(
            labels[item.state.value], item.cached_path, sorted(current_tags),
            list(rows.values()), [], summary["final_tags"],
        )
        if local_review:
            self.page._batch_local_item_id = item.id
            self.page.review_title.setText(f"Fichier local #{item.id}")
            self.page.copy_open_button.setEnabled(True)
