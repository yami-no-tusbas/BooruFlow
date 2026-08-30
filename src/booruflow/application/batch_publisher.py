"""Durable, sequential publication orchestration for reviewed Gelbooru batches."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from booruflow.application.publish_preparation import (
    PublishPreparation,
    PublishPreparationService,
    PublishVerificationError,
)
from booruflow.domain.image_analysis import PublishState
from booruflow.infrastructure.gelbooru_edit_transport import (
    GelbooruAuthenticatedSession,
    GelbooruEditTransport,
    GelbooruPublishDeferredError,
    GelbooruSessionExpiredError,
    GelbooruSessionUnknownError,
    GelbooruSessionValidationError,
)

PUBLISH_DELAY_SECONDS = 10.0
VERIFICATION_ATTEMPTS = 4
VERIFICATION_RETRY_DELAY_SECONDS = 2.0
TRANSPORT_WRAPPER_DEPTH = 2


class PublishRepository(Protocol):
    """Persistence operations required by the sequential batch publisher."""

    def list_batch_entries(
        self, publish_state: PublishState | None = None
    ) -> list[dict[str, object]]: ...
    def begin_publish_attempt(self, item_id: int) -> int: ...
    def publish_succeeded(self, item_id: int) -> None: ...
    def publish_failed(self, item_id: int, error: str) -> None: ...
    def publish_deferred(self, item_id: int, error: str) -> None: ...
    def recover_interrupted_publishes(self) -> int: ...
    def retry_failed_publishes(self, item_ids: Iterable[int]) -> int: ...


@dataclass(frozen=True, slots=True)
class BatchPublishSummary:
    """Immutable outcome counters for one publication run."""

    total: int
    published: int = 0
    no_op: int = 0
    failed: int = 0
    session_expired: bool = False
    session_unknown: bool = False
    cancelled: bool = False
    deferred: bool = False


class BatchPublisher:
    """Publish reviewed Gelbooru entries sequentially with durable state updates."""

    def __init__(
        self,
        repository: PublishRepository,
        preparation: PublishPreparationService,
        transport: GelbooruEditTransport,
        session: GelbooruAuthenticatedSession,
        *,
        delay_seconds: float = PUBLISH_DELAY_SECONDS,
        sleeper: Callable[[float], None] = time.sleep,
        verification_attempts: int = VERIFICATION_ATTEMPTS,
        verification_retry_delay_seconds: float = VERIFICATION_RETRY_DELAY_SECONDS,
        verification_sleeper: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        self.repository = repository
        self.preparation = preparation
        self.transport = transport
        self.session = session
        self.delay_seconds = max(0.0, delay_seconds)
        self.sleeper = sleeper
        self.verification_attempts = max(1, int(verification_attempts))
        self.verification_retry_delay_seconds = max(0.0, verification_retry_delay_seconds)
        self.verification_sleeper = verification_sleeper
        self.log = log or (lambda _message: None)
        self.cancel_check = cancel_check or (lambda: False)

    def publish_pending(
        self, progress: Callable[[int, int, str], None] | None = None
    ) -> BatchPublishSummary:
        self.repository.recover_interrupted_publishes()
        entries = [
            entry
            for entry in self.repository.list_batch_entries(PublishState.PENDING_PUBLISH)
            if entry["site"] == "gelbooru" and entry["post_id"]
        ]
        return self._publish(entries, progress)

    def retry_failed(
        self, item_ids: Iterable[int], progress: Callable[[int, int, str], None] | None = None
    ) -> BatchPublishSummary:
        self.repository.recover_interrupted_publishes()
        self.repository.retry_failed_publishes(item_ids)
        wanted = {int(item_id) for item_id in item_ids}
        entries = [
            entry
            for entry in self.repository.list_batch_entries(PublishState.PENDING_PUBLISH)
            if int(entry["item_id"]) in wanted and entry["site"] == "gelbooru" and entry["post_id"]
        ]
        return self._publish(entries, progress)

    def _submit_prepared(self, prepared: PublishPreparation) -> None:
        submit_prepared = getattr(self.transport, "submit_prepared", None)
        if callable(submit_prepared):
            submit_prepared(self.session, prepared)
        else:
            self.transport.submit(self.session, prepared.post_id, prepared.publish_tags)

    def _is_diagnostic_only_transport(self) -> bool:
        """Recognize the embedded no-submit transport through its lazy wrapper."""
        candidate = self.transport
        for _unused in range(TRANSPORT_WRAPPER_DEPTH):
            if getattr(candidate, "diagnostic_only", False) is True:
                return True
            candidate = getattr(candidate, "transport", None)
            if candidate is None:
                return False
        return False

    def _verify_with_retries(self, prepared: PublishPreparation) -> None:
        for attempt in range(1, self.verification_attempts + 1):
            try:
                self.preparation.verify_remote(prepared)
                return
            except PublishVerificationError:
                if attempt >= self.verification_attempts:
                    raise
                self.log(
                    f"Publish verification Gelbooru #{prepared.post_id}: "
                    f"stale result attempt={attempt}/{self.verification_attempts}; retrying"
                )
                if self.verification_retry_delay_seconds:
                    self.verification_sleeper(self.verification_retry_delay_seconds)

    def _publish(
        self, entries: list[dict[str, object]], progress: Callable[[int, int, str], None] | None
    ) -> BatchPublishSummary:
        published = no_op = failed = 0
        total = len(entries)
        for index, entry in enumerate(entries, start=1):
            if self.cancel_check():
                return BatchPublishSummary(
                    total=total,
                    published=published,
                    no_op=no_op,
                    failed=failed,
                    cancelled=True,
                )
            item_id, post_id = int(entry["item_id"]), str(entry["post_id"])
            if progress:
                progress(index, total, post_id)
            if self._is_diagnostic_only_transport():
                # A FormData capture is deliberately not a publish attempt: do not
                # enter PUBLISHING, increment attempts, verify remotely, or persist
                # any outcome. The transport always raises the explicit deferred
                # result after the local DOM snapshot.
                prepared = self.preparation.prepare(item_id)
                try:
                    self._submit_prepared(prepared)
                except GelbooruPublishDeferredError as exc:
                    self.log(f"Publish Gelbooru #{post_id}: diagnostic captured: {exc}")
                    return BatchPublishSummary(
                        total=total,
                        published=published,
                        no_op=no_op,
                        failed=failed,
                        deferred=True,
                    )
                raise RuntimeError("Diagnostic Embedded terminé sans résultat bloqué.")
            attempt = self.repository.begin_publish_attempt(item_id)
            try:
                prepared = self.preparation.prepare(item_id)
                self.log(
                    f"Publish Gelbooru #{post_id} attempt={attempt} original={len(prepared.original_tags)} fresh={len(prepared.fresh_tags)} add={len(prepared.additions)} remove={len(prepared.removals)} external_add={len(prepared.external_additions)} external_remove={len(prepared.external_removals)} final={len(prepared.publish_tags)}"
                )
                self.log(
                    f"Publish payload Gelbooru #{post_id}: "
                    f"fresh_count={len(prepared.fresh_tags)} "
                    f"add_count={len(prepared.additions)} "
                    f"remove_count={len(prepared.removals)} "
                    f"publish_count={len(prepared.publish_tags)}"
                )
                if prepared.publish_tags == prepared.fresh_tags:
                    self.repository.publish_succeeded(item_id)
                    published += 1
                    no_op += 1
                    self.log(f"Publish Gelbooru #{post_id}: no-op")
                else:
                    self._submit_prepared(prepared)
                    self._verify_with_retries(prepared)
                    self.repository.publish_succeeded(item_id)
                    published += 1
                    self.log(f"Publish Gelbooru #{post_id}: published")
            except GelbooruPublishDeferredError as exc:
                self.repository.publish_deferred(item_id, str(exc))
                self.log(f"Publish Gelbooru #{post_id}: deferred: {exc}")
                return BatchPublishSummary(
                    total=total,
                    published=published,
                    no_op=no_op,
                    failed=failed,
                    deferred=True,
                )
            except PublishVerificationError as exc:
                self.repository.publish_failed(item_id, str(exc))
                failed += 1
                self.log(f"Publish Gelbooru #{post_id}: verification failed: {exc}")
            except GelbooruSessionValidationError as exc:
                self.repository.publish_deferred(item_id, str(exc))
                expired = isinstance(exc, GelbooruSessionExpiredError)
                unknown = isinstance(exc, GelbooruSessionUnknownError)
                label = "not authenticated" if expired else "session unknown"
                self.log(f"Publish Gelbooru #{post_id}: {label}: {exc}")
                return BatchPublishSummary(
                    total=total,
                    published=published,
                    no_op=no_op,
                    failed=failed,
                    session_expired=expired,
                    session_unknown=unknown,
                )
            except Exception as exc:  # noqa: BLE001 - one post must not stop the batch
                self.repository.publish_failed(item_id, str(exc))
                failed += 1
                self.log(f"Publish Gelbooru #{post_id}: failed: {exc}")
            if index < total and self.delay_seconds:
                self.sleeper(self.delay_seconds)
        return BatchPublishSummary(total=total, published=published, no_op=no_op, failed=failed)
