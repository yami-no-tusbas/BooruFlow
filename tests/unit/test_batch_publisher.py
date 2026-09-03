from types import SimpleNamespace

from booruflow.application.batch_publisher import BatchPublisher
from booruflow.application.publish_preparation import PublishPreparationService
from booruflow.domain.image_analysis import PublishState
from booruflow.infrastructure.gelbooru_edit_transport import (
    GelbooruPublishDeferredError,
    GelbooruSessionExpiredError,
    GelbooruSessionUnknownError,
)


class Repo:
    def __init__(self, entries): self.entries = {entry["item_id"]: entry for entry in entries}; self.recovered = 0
    def batch_entry(self, item_id): return self.entries.get(item_id)
    def list_batch_entries(self, state=None): return [e for e in self.entries.values() if state is None or e["publish_state"] == state]
    def begin_publish_attempt(self, item_id):
        e=self.entries[item_id]; assert e["publish_state"] == PublishState.PENDING_PUBLISH; e["publish_state"]=PublishState.PUBLISHING; e["publish_attempts"]+=1; return e["publish_attempts"]
    def publish_succeeded(self, item_id):
        entry = self.entries[item_id]
        entry["publish_state"] = PublishState.PUBLISHED
        entry["published_final_tags"] = list(entry["reviewed_final_tags"])
    def publish_failed(self, item_id, error): self.entries[item_id]["publish_state"]=PublishState.FAILED; self.entries[item_id]["last_error"]=error
    def publish_deferred(self, item_id, error): self.entries[item_id]["publish_state"]=PublishState.PENDING_PUBLISH; self.entries[item_id]["last_error"]=error
    def recover_interrupted_publishes(self):
        self.recovered += 1
        for e in self.entries.values():
            if e["publish_state"] == PublishState.PUBLISHING: e["publish_state"] = PublishState.PENDING_PUBLISH
        return 0
    def retry_failed_publishes(self, ids):
        count=0
        for item_id in ids:
            if self.entries[item_id]["publish_state"] == PublishState.FAILED: self.entries[item_id]["publish_state"]=PublishState.PENDING_PUBLISH; count+=1
        return count


class Provider:
    def __init__(self, tags): self.tags=tags; self.calls=[]
    def fetch_post(self, post_id): self.calls.append(post_id); return SimpleNamespace(tags=[SimpleNamespace(name=x) for x in self.tags[post_id]])


class SequencedProvider(Provider):
    def fetch_post(self, post_id):
        self.calls.append(post_id)
        responses = self.tags[post_id]
        tags = responses.pop(0) if len(responses) > 1 else responses[0]
        return SimpleNamespace(tags=[SimpleNamespace(name=x) for x in tags])


class Transport:
    def __init__(self, failures=None, *, update_remote=True):
        self.calls=[]; self.failures=failures or {}; self.update_remote=update_remote
        self.provider = None
    def submit(self, _session, post_id, tags):
        self.calls.append((post_id, tags)); failure=self.failures.get(post_id)
        if failure: raise failure
        if self.provider is not None and self.update_remote:
            self.provider.tags[post_id] = list(tags)


def entry(item_id, post_id, *, state=PublishState.PENDING_PUBLISH, additions=("d",), removals=("b",)):
    return {"item_id":item_id,"site":"gelbooru","post_id":post_id,"original_tags":["a","b","c"],"additions":list(additions),"removals":list(removals),"reviewed_final_tags":["a","c","d"],"publish_state":state,"publish_attempts":0,"last_error":None,"published_final_tags":None}


def publisher(entries, fresh, transport=None, sleeps=None, verification_sleeps=None, *, sequenced=False):
    repo=Repo(entries); provider=(SequencedProvider(fresh) if sequenced else Provider(fresh))
    preparation=PublishPreparationService(repo, provider)
    actual_transport = transport or Transport()
    actual_transport.provider = provider
    return repo, provider, BatchPublisher(
        repo, preparation, actual_transport, object(), delay_seconds=1,
        sleeper=(sleeps.append if sleeps is not None else lambda _x: None),
        verification_sleeper=(verification_sleeps.append
                              if verification_sleeps is not None else lambda _x: None),
    )


def test_sequential_merge_failure_continues_and_rate_limits():
    transport=Transport({"101": RuntimeError("bad post")}); sleeps=[]
    repo, _, service=publisher([entry(1,"100"),entry(2,"101"),entry(3,"102")], {"100":["a","b","c","e"],"101":["a","b","c"],"102":["a","b","c"]},transport,sleeps)
    result=service.publish_pending()
    assert (result.published,result.failed,result.no_op)==(2,1,0)
    assert transport.calls[0] == ("100", ("a","c","d","e"))
    assert repo.entries[2]["publish_state"] == PublishState.FAILED and sleeps == [1,1]


def test_inter_post_delay_is_never_applied_before_the_first_item():
    sleeps=[]; transport=Transport()
    _repo, _, service=publisher(
        [entry(1,"100")], {"100":["a","b","c"]}, transport, sleeps,
    )
    service.publish_pending()
    assert sleeps == [] and transport.calls == [("100", ("a", "c", "d"))]


def test_inter_post_delay_is_applied_only_before_later_items():
    events=[]
    transport=Transport()
    original_submit=transport.submit
    transport.submit=lambda *args: (events.append(f"submit:{args[1]}"), original_submit(*args))[1]
    _repo, _, service=publisher(
        [entry(1,"100"),entry(2,"101")],
        {"100":["a","b","c"],"101":["a","b","c"]}, transport,
    )
    service.sleeper=lambda seconds: events.append(f"sleep:{seconds}")
    service.publish_pending()
    assert events == ["submit:100", "sleep:1", "submit:101"]


def test_noop_skips_post_and_marks_published():
    transport=Transport(); repo, _, service=publisher([entry(1,"100")], {"100":["a","c","d"]},transport)
    result=service.publish_pending()
    assert result.no_op == 1 and not transport.calls and repo.entries[1]["publish_state"] == PublishState.PUBLISHED


def test_session_expiry_stops_remaining_items():
    transport=Transport({"100": GelbooruSessionExpiredError("expired")})
    repo, _, service=publisher([entry(1,"100"),entry(2,"101")], {"100":["a","b","c"],"101":["a","b","c"]},transport)
    result=service.publish_pending()
    assert result.session_expired and result.failed == 0
    assert repo.entries[1]["publish_state"] == PublishState.PENDING_PUBLISH
    assert repo.entries[2]["publish_state"] == PublishState.PENDING_PUBLISH
    assert transport.calls == [("100", ("a", "c", "d"))]


def test_unknown_session_stops_without_post_failure_and_is_directly_retryable():
    transport=Transport({"100": GelbooruSessionUnknownError("unknown")})
    repo, _, service=publisher(
        [entry(1,"100"),entry(2,"101")],
        {"100":["a","b","c"],"101":["a","b","c"]},transport,
    )
    result=service.publish_pending()
    assert result.session_unknown and not result.session_expired and result.failed == 0
    assert repo.entries[1]["publish_state"] == PublishState.PENDING_PUBLISH
    assert repo.entries[2]["publish_state"] == PublishState.PENDING_PUBLISH
    transport.failures.clear()
    retry=service.publish_pending()
    assert retry.published == 2


def test_diagnostic_preflight_defers_item_without_failure_or_next_post():
    transport = Transport({"100": GelbooruPublishDeferredError("diagnostic only")})
    repo, _, service = publisher(
        [entry(1, "100"), entry(2, "101")],
        {"100": ["a", "b", "c"], "101": ["a", "b", "c"]},
        transport,
    )

    result = service.publish_pending()

    assert result.deferred and result.failed == 0 and result.published == 0
    assert repo.entries[1]["publish_state"] == PublishState.PENDING_PUBLISH
    assert repo.entries[2]["publish_state"] == PublishState.PENDING_PUBLISH
    assert transport.calls == [("100", ("a", "c", "d"))]


def test_embedded_diagnostic_capture_never_enters_publish_state_or_mutates_entry():
    transport = Transport({"100": GelbooruPublishDeferredError("diagnostic captured")})
    transport.diagnostic_only = True
    pending = entry(1, "100")
    pending.update({
        "published_at": None, "published_verified_at": None,
        "published_final_tags": None, "retry_count": 0,
    })
    before = {key: value[:] if isinstance(value, list) else value for key, value in pending.items()}
    repo, _, service = publisher([pending], {"100": ["a", "b", "c"]}, transport)

    result = service.publish_pending()

    assert result.deferred and not result.published and not result.failed
    assert repo.entries[1] == before
    assert transport.calls == [("100", ("a", "c", "d"))]


def test_retry_refetches_after_failure_and_crash_state_is_recovered():
    failed=entry(1,"100",state=PublishState.FAILED); interrupted=entry(2,"101",state=PublishState.PUBLISHING)
    repo, provider, service=publisher([failed,interrupted], {"100":["a","b","c","e"],"101":["a","c","d"]})
    result=service.retry_failed([1])
    assert result.published == 1 and provider.calls == ["100", "100"]
    assert repo.entries[2]["publish_state"] == PublishState.PENDING_PUBLISH and repo.entries[1]["publish_attempts"] == 1


def test_cancellation_does_not_start_the_next_post():
    transport=Transport(); checks=iter((False, True))
    repo, _, service=publisher(
        [entry(1,"100"),entry(2,"101")],
        {"100":["a","b","c"],"101":["a","b","c"]}, transport,
    )
    service.cancel_check=lambda: next(checks)
    result=service.publish_pending()
    assert result.cancelled and transport.calls == [("100", ("a","c","d"))]
    assert repo.entries[2]["publish_state"] == PublishState.PENDING_PUBLISH


def test_post_submit_verification_failure_marks_failed_only_after_bounded_retries():
    transport = Transport(update_remote=False)
    pending = entry(1, "100")
    pending["published_final_tags"] = ["previous", "snapshot"]
    verification_sleeps = []
    repo, provider, service = publisher(
        [pending, entry(2, "101")],
        {
            "100": [["a", "b", "c"]],
            "101": [["a", "b", "c"], ["a", "c", "d"]],
        },
        transport, verification_sleeps=verification_sleeps, sequenced=True,
    )

    result = service.publish_pending()

    assert result.failed == 1 and result.published == 1
    assert transport.calls == [
        ("100", ("a", "c", "d")), ("101", ("a", "c", "d")),
    ]
    assert provider.calls == ["100"] * 5 + ["101", "101"]
    assert verification_sleeps == [2.0, 2.0, 2.0]
    assert repo.entries[1]["publish_state"] == PublishState.FAILED
    assert repo.entries[2]["publish_state"] == PublishState.PUBLISHED
    assert repo.entries[1]["published_final_tags"] == ["previous", "snapshot"]
    assert "publish_verification_failed" in repo.entries[1]["last_error"]
    assert "additions_missing=1" in repo.entries[1]["last_error"]
    assert "removals_still_present=1" in repo.entries[1]["last_error"]


def test_post_submit_verification_success_is_the_only_path_to_published_snapshot():
    transport = Transport()
    repo, provider, service = publisher(
        [entry(1, "100")], {"100": ["a", "b", "c"]}, transport,
    )

    result = service.publish_pending()

    assert result.published == 1 and result.failed == 0
    assert provider.calls == ["100", "100"]
    assert repo.entries[1]["publish_state"] == PublishState.PUBLISHED
    assert repo.entries[1]["published_final_tags"] == ["a", "c", "d"]


def test_first_stale_verification_then_correct_is_published_without_resubmit():
    transport = Transport(update_remote=False); verification_sleeps = []
    _repo, provider, service = publisher(
        [entry(1, "100")],
        {"100": [["a", "b", "c"], ["a", "b", "c"], ["a", "c", "d"]]},
        transport, verification_sleeps=verification_sleeps, sequenced=True,
    )

    result = service.publish_pending()

    assert result.published == 1 and result.failed == 0
    assert transport.calls == [("100", ("a", "c", "d"))]
    assert provider.calls == ["100", "100", "100"]
    assert verification_sleeps == [2.0]


def test_multiple_stale_verifications_then_correct_are_published_without_resubmit():
    transport = Transport(update_remote=False); verification_sleeps = []
    _repo, provider, service = publisher(
        [entry(1, "100")],
        {"100": [
            ["a", "b", "c"], ["a", "b", "c"],
            ["a", "b", "c"], ["a", "c", "d"],
        ]},
        transport, verification_sleeps=verification_sleeps, sequenced=True,
    )

    result = service.publish_pending()

    assert result.published == 1 and result.failed == 0
    assert len(transport.calls) == 1
    assert provider.calls == ["100"] * 4
    assert verification_sleeps == [2.0, 2.0]


def test_retry_of_already_effective_failed_publish_is_noop_without_submit():
    failed = entry(1, "14347081", state=PublishState.FAILED)
    transport = Transport(update_remote=False)
    repo, provider, service = publisher(
        [failed], {"14347081": ["a", "c", "d"]}, transport,
    )

    result = service.retry_failed([1])

    assert result.published == 1 and result.no_op == 1 and result.failed == 0
    assert transport.calls == []
    assert provider.calls == ["14347081"]
    assert repo.entries[1]["publish_state"] == PublishState.PUBLISHED
