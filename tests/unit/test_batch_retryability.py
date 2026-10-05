"""Explicit batch retries use one predicate from the button to persisted state."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QMessageBox

from booruflow.application.batch_publisher import MAX_PUBLISH_ATTEMPTS, BatchPublisher
from booruflow.application.publish_preparation import PublishPreparationService
from booruflow.domain.image_analysis import AnalysisItem, InputKind, PublishState, SourceReference
from booruflow.domain.publication_retry import can_retry_publication
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from tests.unit.test_tagging_navigation_prefetch import navigation, wait_for  # noqa: F401


def failure(repo, post, reason="publish_error", retryable=True, attempts=3):
    item_id = repo.add_item(AnalysisItem(SourceReference(InputKind.GELBOORU_POST,
                                                       site="gelbooru", post_id=str(post))))
    repo.save_review_batch_entry(item_id, original_tags=["old"], additions=["new"],
                                 removals=[], reviewed_final_tags=["old", "new"])
    for attempt in range(attempts):
        repo.begin_publish_attempt(item_id)
        if attempt < attempts - 1:
            repo.publish_deferred(item_id, "temporary")
        else:
            repo.publish_failed(item_id, "test error", reason=reason, retryable=retryable)
    return item_id


@pytest.mark.parametrize("kind,enabled", [
    ("locked", False), ("error", True), ("mixed", True), ("validation", False),
])
def test_button_detects_retryable_failures_without_selection(navigation, kind, enabled):  # noqa: F811
    n = navigation
    repo = n.analysis.repository
    if kind in {"locked", "mixed"}:
        failure(repo, 1, "locked_image", False, 1)
    if kind in {"error", "mixed"}:
        # The previous publisher persisted False after exhausting automatic attempts.
        failure(repo, 2, retryable=False)
    if kind == "validation":
        failure(repo, 3, "validation_failed", False)
    n.controller.refresh_batch()
    assert n.page.batch_retry_button.isEnabled() is enabled
    assert n.page._selected_batch_ids() == []
    if enabled:
        sent = QSignalSpy(n.page.batch_retry_requested)
        with patch.object(n.controller, "_start_batch_publish") as start:
            n.page.batch_retry_button.click()
        assert sent.count() == 1
        ids = sent.at(0)[0]
        assert len(ids) == 1
        assert repo.batch_entry(ids[0])["failure_reason"] == "publish_error"
        start.assert_called_once_with(ids)
    n.page.set_batch_publish_running(True)
    assert not n.page.batch_retry_button.isEnabled()


@pytest.mark.parametrize("remove_lock", [False, True])
def test_retry_click_publishes_each_error_once_then_disables(navigation, remove_lock):  # noqa: F811
    n = navigation
    repo = n.analysis.repository
    locked = failure(repo, 10, "locked_image", False, 1)
    errors = [failure(repo, post, retryable=False) for post in (11, 12, 13)]
    if remove_lock:
        repo.remove_batch_entry(locked)
    remote = {str(post): ["old"] for post in (10, 11, 12, 13)}
    submissions = []

    class Provider:
        def fetch_post(self, post_id):
            return SimpleNamespace(tags=[SimpleNamespace(name=tag) for tag in remote[post_id]])

    class Transport:
        def submit(self, _session, post_id, tags):
            submissions.append(post_id)
            remote[post_id] = list(tags)

    def publisher():
        database = ImageAnalysisRepository(repo.path)
        return BatchPublisher(database, PublishPreparationService(database, Provider()),
                              Transport(), object(), delay_seconds=0, verification_retry_delay_seconds=0)

    n.controller._publisher_factory = publisher
    n.controller.refresh_batch()
    assert n.page.batch_retry_button.isEnabled()
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        n.page.batch_retry_button.click()
    assert n.controller.publish_worker is not None
    wait_for(n.app, lambda: not n.page._batch_publish_running)
    n.controller.publish_worker.wait(3000)
    assert sorted(submissions) == ["11", "12", "13"]
    assert all(repo.batch_entry(item)["publish_state"] == PublishState.PUBLISHED for item in errors)
    assert all(repo.batch_entry(item)["publish_attempts"] == 4 for item in errors)
    assert not n.page.batch_retry_button.isEnabled()
    if remove_lock:
        assert repo.batch_entry(locked) is None
    else:
        assert repo.batch_entry(locked)["publish_state"] == PublishState.FAILED
        assert not can_retry_publication(repo.batch_entry(locked))
    n.page.batch_retry_button.click()
    assert len(submissions) == 3


def test_manual_retry_is_deduplicated_and_keeps_automatic_budget_bounded(navigation):  # noqa: F811
    n = navigation
    repo = n.analysis.repository
    item = failure(repo, 21, retryable=False)
    calls = []

    class Provider:
        def fetch_post(self, _post_id):
            return SimpleNamespace(tags=[SimpleNamespace(name="old")])

    class Transport:
        def submit(self, _session, post_id, _tags):
            calls.append(post_id)
            raise OSError("temporary server failure")

    service = BatchPublisher(repo, PublishPreparationService(repo, Provider()), Transport(),
                             object(), delay_seconds=0, verification_retry_delay_seconds=0)
    result = service.retry_failed(iter([item, item, item]))
    assert result.total == 1 and result.failed == 1
    assert calls == ["21"] * MAX_PUBLISH_ATTEMPTS
    saved = repo.batch_entry(item)
    assert saved["failure_retryable"] is True
    assert saved["publish_attempts"] == 2 * MAX_PUBLISH_ATTEMPTS
    assert can_retry_publication(saved)
    n.controller.refresh_batch()
    assert n.page.batch_retry_button.isEnabled()
