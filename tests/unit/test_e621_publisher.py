from __future__ import annotations

import io
import urllib.error
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

from booruflow.application.batch_publisher import (
    BatchPublisher,
    BatchPublishSummary,
    MixedSiteBatchPublisher,
)
from booruflow.application.publish_preparation import PublishPreparationService
from booruflow.domain.image_analysis import (
    AnalysisItem,
    InputKind,
    PublishState,
    SourceReference,
)
from booruflow.infrastructure.e621_client import E621ApiError, E621Client
from booruflow.infrastructure.e621_publish_transport import E621PublishTransport
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository


class Response:
    def __init__(self, payload: bytes = b"{}") -> None:
        self.payload = payload
        self.headers = SimpleNamespace(get_content_charset=lambda: "utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.payload


class Provider:
    def __init__(self, responses: list[list[str]]) -> None:
        self.responses = responses
        self.calls = 0

    def fetch_post(self, _post_id: str):
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return SimpleNamespace(
            tags=[SimpleNamespace(name=name) for name in self.responses[index]]
        )


def reviewed_repository(tmp_path: Path) -> tuple[ImageAnalysisRepository, int]:
    repository = ImageAnalysisRepository(tmp_path / "analysis.sqlite")
    item_id = repository.add_item(
        AnalysisItem(SourceReference(InputKind.E621_POST, site="e621", post_id="42"))
    )
    state = repository.save_review_batch_entry(
        item_id,
        original_tags=["wolf", "old", "external_removed"],
        additions=["solo"],
        removals=["old"],
        reviewed_final_tags=["wolf", "solo", "external_removed"],
    )
    assert state is PublishState.PENDING_PUBLISH
    return repository, item_id


def test_e621_patch_uses_basic_auth_and_only_tag_string_diff() -> None:
    requests = []

    def opener(request, _timeout):
        requests.append(request)
        return Response(b'{"post":{"id":42}}')

    E621Client("alice", "secret", opener=opener, request_interval=0).update_post_tags(
        42, ("solo", "blue_hair"), ("old",)
    )
    request = requests[0]
    form = urllib.parse.parse_qs(request.data.decode("utf-8"))
    assert request.method == "PATCH"
    assert request.full_url == "https://e621.net/posts/42.json"
    assert form == {"post[tag_string_diff]": ["solo blue_hair -old"]}
    assert request.headers["Authorization"].startswith("Basic ")
    assert "secret" not in request.full_url and b"secret" not in request.data
    assert "BooruFlow" in request.headers["User-agent"]


@pytest.mark.parametrize(
    ("status", "reason"),
    [(401, "invalid_credentials"), (403, "access_denied"), (422, "validation_error"),
     (429, "rate_limited"), (503, "rate_limited")],
)
def test_e621_patch_classifies_http_errors_without_secrets(status: int, reason: str) -> None:
    def opener(request, _timeout):
        raise urllib.error.HTTPError(request.full_url, status, "failure", {}, io.BytesIO())

    client = E621Client("alice", "secret", opener=opener, request_interval=0)
    with pytest.raises(E621ApiError) as captured:
        client.update_post_tags(42, ("solo",), ())
    assert (captured.value.status, captured.value.reason) == (status, reason)
    assert "secret" not in str(captured.value)


def test_e621_patch_classifies_timeout() -> None:
    client = E621Client(
        "alice", "secret", opener=lambda *_args: (_ for _ in ()).throw(TimeoutError()),
        request_interval=0,
    )
    with pytest.raises(E621ApiError, match="reason=timeout"):
        client.update_post_tags(42, ("solo",), ())


def test_e621_preparation_preserves_external_changes_and_never_uses_aliases(
    tmp_path: Path, monkeypatch
) -> None:
    repository, item_id = reviewed_repository(tmp_path)
    provider = Provider([["wolf", "external_added"]])
    monkeypatch.setattr(
        "booruflow.infrastructure.gelbooru_aliases.inspect_alias_catalog",
        lambda *_args: pytest.fail("Gelbooru aliases must not be used for e621"),
    )
    service = PublishPreparationService(repository, provider, site="e621")
    prepared = service.prepare(item_id)
    assert prepared.external_additions == ("external_added",)
    assert prepared.external_removals == ("external_removed", "old")
    assert prepared.publish_tags == ("external_added", "solo", "wolf")
    repository.close()


def test_e621_publish_transitions_and_verifies_remote_delta(tmp_path: Path) -> None:
    repository, item_id = reviewed_repository(tmp_path)
    provider = Provider([
        ["wolf", "old", "external_added"],
        ["wolf", "solo", "external_added"],
    ])
    submitted = []
    client = SimpleNamespace(
        update_post_tags=lambda post_id, additions, removals: submitted.append(
            (post_id, additions, removals)
        )
    )
    publisher = BatchPublisher(
        repository,
        PublishPreparationService(repository, provider, site="e621"),
        E621PublishTransport(client),
        object(),
        site="e621",
        delay_seconds=0,
    )
    result = publisher.publish_pending()
    assert (result.published, result.failed) == (1, 0)
    assert submitted == [("42", ("solo",), ("old",))]
    assert repository.batch_entry(item_id)["publish_state"] is PublishState.PUBLISHED
    repository.close()


def test_e621_verification_failure_is_durable_and_retryable(tmp_path: Path) -> None:
    repository, item_id = reviewed_repository(tmp_path)
    provider = Provider([["wolf", "old"], ["wolf", "old"]])
    transport = SimpleNamespace(submit=lambda *_args: None)
    publisher = BatchPublisher(
        repository,
        PublishPreparationService(repository, provider, site="e621"),
        transport,
        object(),
        site="e621",
        delay_seconds=0,
        verification_attempts=1,
    )
    result = publisher.publish_pending()
    entry = repository.batch_entry(item_id)
    assert result.failed == 1 and entry["publish_state"] is PublishState.FAILED
    assert "additions_missing=1" in entry["last_error"]
    assert "removals_still_present=1" in entry["last_error"]

    provider.responses = [["wolf", "solo"]]
    provider.calls = 0
    retried = publisher.retry_failed([item_id])
    assert retried.published == 1
    assert repository.batch_entry(item_id)["publish_state"] is PublishState.PUBLISHED
    repository.close()


def test_e621_http_failure_marks_only_that_item_failed(tmp_path: Path) -> None:
    repository, item_id = reviewed_repository(tmp_path)
    client = SimpleNamespace(
        update_post_tags=lambda *_args: (_ for _ in ()).throw(
            E621ApiError(429, "rate_limited")
        )
    )
    publisher = BatchPublisher(
        repository,
        PublishPreparationService(repository, Provider([["wolf", "old"]]), site="e621"),
        E621PublishTransport(client),
        object(),
        site="e621",
        delay_seconds=0,
    )
    assert publisher.publish_pending().failed == 1
    entry = repository.batch_entry(item_id)
    assert entry["publish_state"] is PublishState.FAILED
    assert entry["last_error"] == "e621_api_error status=429 reason=rate_limited"
    repository.close()


def test_mixed_batch_summary_and_progress_distinguish_sites() -> None:
    entries = [
        {"item_id": 1, "site": "gelbooru", "post_id": "10"},
        {"item_id": 2, "site": "e621", "post_id": "20"},
    ]
    repository = SimpleNamespace(
        list_batch_entries=lambda _state: entries,
    )

    class Publisher:
        cancel_check = None

        def __init__(self, site: str) -> None:
            self.site = site

        def publish_pending(self, progress):
            entry = next(value for value in entries if value["site"] == self.site)
            progress(1, 1, entry["post_id"])
            return BatchPublishSummary(total=1, published=1)

    progress = []
    mixed = MixedSiteBatchPublisher(
        repository,
        {"gelbooru": Publisher("gelbooru"), "e621": Publisher("e621")},
    )
    result = mixed.publish_pending(lambda current, total, target: progress.append(
        (current, total, target)
    ))
    assert result.sites == ("e621", "gelbooru")
    assert (result.total, result.published) == (2, 2)
    assert progress == [(1, 2, "gelbooru:10"), (2, 2, "e621:20")]
