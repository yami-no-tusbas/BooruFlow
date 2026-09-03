from pathlib import Path
from types import SimpleNamespace

import pytest

from booruflow.application.publish_preparation import (
    FreshPostUnavailableError,
    NonPublishableBatchEntryError,
    PublishPreparationService,
    PublishVerificationError,
)
from booruflow.domain.image_analysis import PublishState
from booruflow.infrastructure.gelbooru_aliases import AliasRelation, GelbooruAliasRepository
from booruflow.infrastructure.image_sources import ImageSourceError, PostNotFoundError


class FakeRepository:
    def __init__(self, entry: dict[str, object] | None) -> None:
        self.entry = entry

    def batch_entry(self, item_id: int) -> dict[str, object] | None:
        return self.entry if self.entry and self.entry["item_id"] == item_id else None


class FakeGelbooru:
    def __init__(self, tags: list[str] | Exception) -> None:
        self.tags = tags
        self.fetches: list[str] = []
        self.post_calls = 0

    def fetch_post(self, post_id: str):
        self.fetches.append(post_id)
        if isinstance(self.tags, Exception):
            raise self.tags
        return SimpleNamespace(tags=[SimpleNamespace(name=name) for name in self.tags])

    def post_form(self, *_args) -> None:
        self.post_calls += 1
        raise AssertionError("Phase 3A must never POST")


def entry(
    *, original: list[str] | None = None, additions: list[str] | None = None,
    removals: list[str] | None = None, final: list[str] | None = None,
    site: str | None = "gelbooru", post_id: str | None = "123",
    state: PublishState = PublishState.PENDING_PUBLISH,
) -> dict[str, object]:
    return {
        "item_id": 1, "site": site, "post_id": post_id,
        "original_tags": list(original if original is not None else ["a", "b", "c"]),
        "additions": list(additions if additions is not None else []),
        "removals": list(removals if removals is not None else []),
        "reviewed_final_tags": list(
            final if final is not None else original if original is not None else ["a", "b", "c"]
        ),
        "reviewed_at": "2026-08-25T10:00:00+00:00", "publish_state": state,
    }


def prepare(current: list[str], batch: dict[str, object] | None = None):
    provider = FakeGelbooru(current)
    service = PublishPreparationService(
        FakeRepository(batch or entry()), provider, now=lambda: "2026-08-25T12:00:00+00:00"
    )
    return service.prepare(1), provider


def test_prepare_merges_no_external_changes() -> None:
    result, provider = prepare(["a", "b", "c"], entry(additions=["d"], removals=["b"], final=["a", "c", "d"]))
    assert result.external_additions == ()
    assert result.external_removals == ()
    assert result.publish_tags == ("a", "c", "d")
    assert provider.fetches == ["123"]
    assert provider.post_calls == 0


def test_prepare_preserves_external_additions_and_never_resurrects_external_removals() -> None:
    result, _ = prepare(["a", "b", "e"], entry(additions=["d"], removals=[]))
    assert result.external_additions == ("e",)
    assert result.external_removals == ("c",)
    assert result.publish_tags == ("a", "b", "d", "e")


def test_prepare_treats_same_external_addition_as_idempotent() -> None:
    result, _ = prepare(["a", "b", "c", "d"], entry(additions=["d"]))
    assert result.external_additions == ("d",)
    assert result.publish_tags == ("a", "b", "c", "d")


def test_prepare_keeps_our_explicit_removal_prioritary() -> None:
    result, _ = prepare(["e", "c", "b", "a"], entry(removals=["b"]))
    assert result.external_additions == ("e",)
    assert result.publish_tags == ("a", "c", "e")


def test_second_cycle_real_removals_survive_normalization_and_leave_publish_tags() -> None:
    fresh = [
        "absurdres", "arknights", "highres", "irene_(arknights)",
        "irene_(voyage_of_feathers)_(arknights)", "1girl", "tagme", "head_wings",
    ]
    result, _ = prepare(
        fresh,
        entry(
            original=fresh[:5],
            additions=["holding", "long hair", "long_sleeves", "solo", "wings"],
            removals=["HIGHRES", "irene_(arknights)"],
        ),
    )

    assert result.removals == ("highres", "irene_(arknights)")
    assert "highres" not in result.publish_tags
    assert "irene_(arknights)" not in result.publish_tags
    assert "irene_(voyage_of_feathers)_(arknights)" in result.publish_tags
    assert "long_hair" in result.publish_tags


def test_prepare_normalizes_order_and_duplicates_for_simultaneous_deltas() -> None:
    result, _ = prepare(
        ["c", "a", "e", "a", "b"],
        entry(original=["c", "b", "a", "a"], additions=["d", "D", "f"], removals=["b", "b"]),
    )
    assert result.original_tags == ("a", "b", "c")
    assert result.fresh_tags == ("a", "b", "c", "e")
    assert result.publish_tags == ("a", "c", "d", "e", "f")


@pytest.mark.parametrize(
    ("batch", "message"),
    [
        (entry(site=None, post_id=None, state=PublishState.REVIEWED), "only a remote Gelbooru"),
        (entry(site="e621", post_id="1"), "only a remote Gelbooru"),
        (entry(state=PublishState.PUBLISHED), "not pending publication"),
    ],
)
def test_prepare_rejects_non_publishable_entries(batch, message) -> None:
    with pytest.raises(NonPublishableBatchEntryError, match=message):
        prepare(["a"], batch)


@pytest.mark.parametrize("failure", [PostNotFoundError("gone"), ImageSourceError("offline")])
def test_prepare_translates_fresh_fetch_failures(failure) -> None:
    with pytest.raises(FreshPostUnavailableError):
        PublishPreparationService(FakeRepository(entry()), FakeGelbooru(failure)).prepare(1)


def test_prepare_rejects_empty_fresh_tag_response_and_does_not_mutate_snapshot() -> None:
    batch = entry(additions=["d"], removals=["b"], final=["a", "c", "d"])
    before = {key: list(value) if isinstance(value, list) else value for key, value in batch.items()}
    with pytest.raises(FreshPostUnavailableError, match="no usable tags"):
        prepare([], batch)
    assert batch == before


def test_prepare_rejects_invalid_fresh_response() -> None:
    provider = SimpleNamespace(fetch_post=lambda _post_id: SimpleNamespace(tags=None))
    with pytest.raises(FreshPostUnavailableError, match="invalid fresh"):
        PublishPreparationService(FakeRepository(entry()), provider).prepare(1)


@pytest.mark.parametrize(
    ("server_tags", "missing", "still_present"),
    [
        (["a", "b", "c", "d"], (), ("b",)),
        (["a", "c"], ("d",), ()),
    ],
)
def test_post_submit_verification_rejects_missing_addition_or_present_removal(
    server_tags, missing, still_present,
) -> None:
    batch = entry(additions=["d"], removals=["b"], final=["a", "c", "d"])
    provider = FakeGelbooru(["a", "b", "c"])
    service = PublishPreparationService(FakeRepository(batch), provider)
    prepared = service.prepare(1)
    provider.tags = server_tags

    with pytest.raises(PublishVerificationError) as failure:
        service.verify_remote(prepared)

    assert failure.value.additions_missing == missing
    assert failure.value.removals_still_present == still_present


def test_post_submit_verification_accepts_requested_deltas() -> None:
    batch = entry(additions=["d"], removals=["b"], final=["a", "c", "d"])
    provider = FakeGelbooru(["a", "b", "c"])
    service = PublishPreparationService(FakeRepository(batch), provider)
    prepared = service.prepare(1)
    provider.tags = ["a", "c", "d", "external_tag"]

    assert service.verify_remote(prepared) == ("a", "c", "d", "external_tag")


def test_verification_accepts_active_alias_addition_and_requires_alias_target_removal_absent(tmp_path):
    database = Path(tmp_path) / "tags.db"
    aliases = GelbooruAliasRepository(database)
    from booruflow.infrastructure.gelbooru_aliases import ensure_alias_schema
    ensure_alias_schema(database)
    aliases.upsert(AliasRelation("china_dress", "qipao", "active"))
    batch = entry(additions=["china_dress"], removals=["china_dress"])
    provider = FakeGelbooru(["a", "b", "c"])
    service = PublishPreparationService(FakeRepository(batch), provider, alias_database=database)
    prepared = service.prepare(1)
    provider.tags = ["a", "b", "c", "qipao"]
    with pytest.raises(PublishVerificationError) as failure:
        service.verify_remote(prepared)
    assert failure.value.additions_missing == ()
    assert failure.value.removals_still_present == ("china_dress",)


def test_verification_uses_literal_fallback_when_alias_catalogue_is_absent(tmp_path):
    missing_catalogue = Path(tmp_path) / "missing-aliases.db"
    logs: list[str] = []
    provider = FakeGelbooru(["a", "b", "c"])
    service = PublishPreparationService(
        FakeRepository(entry(additions=["china_dress"])),
        provider,
        alias_database=missing_catalogue,
        log=logs.append,
    )
    prepared = service.prepare(1)

    provider.tags = ["a", "b", "c", "china_dress"]
    assert service.verify_remote(prepared) == ("a", "b", "c", "china_dress")
    assert any("configured_path=" in line and "literal fallback" in line for line in logs)

    provider.tags = ["a", "b", "c", "qipao"]
    with pytest.raises(PublishVerificationError, match="additions_missing=1"):
        service.verify_remote(prepared)


def test_verification_logs_active_alias_resolution_when_canonical_tag_is_present(tmp_path):
    database = Path(tmp_path) / "aliases.db"
    from booruflow.infrastructure.gelbooru_aliases import ensure_alias_schema

    ensure_alias_schema(database)
    GelbooruAliasRepository(database).upsert(AliasRelation("china_dress", "qipao", "active"))
    logs: list[str] = []
    provider = FakeGelbooru(["a", "b", "c"])
    service = PublishPreparationService(
        FakeRepository(entry(additions=["china_dress"])),
        provider,
        alias_database=database,
        log=logs.append,
    )
    prepared = service.prepare(1)

    provider.tags = ["a", "b", "c", "qipao"]
    assert service.verify_remote(prepared) == ("a", "b", "c", "qipao")
    assert any("china_dress resolved to qipao: present" in line for line in logs)


def test_verification_logs_a_valid_but_empty_alias_catalogue_once(tmp_path):
    database = Path(tmp_path) / "aliases.db"
    from booruflow.infrastructure.gelbooru_aliases import ensure_alias_schema

    ensure_alias_schema(database)
    logs: list[str] = []
    provider = FakeGelbooru(["a", "b", "c"])
    service = PublishPreparationService(
        FakeRepository(entry(additions=["china_dress"])),
        provider,
        alias_database=database,
        log=logs.append,
    )
    prepared = service.prepare(1)
    provider.tags = ["a", "b", "c", "china_dress"]

    assert service.verify_remote(prepared) == ("a", "b", "c", "china_dress")
    assert sum("has no active aliases" in line for line in logs) == 1
