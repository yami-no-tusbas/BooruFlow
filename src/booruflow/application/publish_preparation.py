"""Read-only preparation of a future Gelbooru tag publication."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from booruflow.application.tagging import normalize_booru_tag
from booruflow.domain.image_analysis import PublishState
from booruflow.infrastructure.image_sources import ImageSourceError, PostNotFoundError


class BatchEntryReader(Protocol):
    def batch_entry(self, item_id: int) -> dict[str, object] | None: ...


class FreshPostReader(Protocol):
    def fetch_post(self, post_id: str): ...


class PublishPreparationError(RuntimeError):
    """A batch entry cannot safely produce a remote publication payload."""


class NonPublishableBatchEntryError(PublishPreparationError):
    pass


class FreshPostUnavailableError(PublishPreparationError):
    pass


class PublishVerificationError(PublishPreparationError):
    """The remote post does not reflect the requested deltas after submit."""

    def __init__(
        self,
        additions_missing: tuple[str, ...],
        removals_still_present: tuple[str, ...],
    ) -> None:
        self.additions_missing = additions_missing
        self.removals_still_present = removals_still_present
        super().__init__(
            "publish_verification_failed: "
            f"additions_missing={len(additions_missing)} "
            f"removals_still_present={len(removals_still_present)}"
        )


@dataclass(frozen=True, slots=True)
class PublishPreparation:
    item_id: int
    site: str
    post_id: str
    original_tags: tuple[str, ...]
    reviewed_final_tags: tuple[str, ...]
    additions: tuple[str, ...]
    removals: tuple[str, ...]
    fresh_tags: tuple[str, ...]
    external_additions: tuple[str, ...]
    external_removals: tuple[str, ...]
    publish_tags: tuple[str, ...]
    prepared_at: str

    @property
    def external_changes_detected(self) -> bool:
        return bool(self.external_additions or self.external_removals)


def stable_tags(values: object) -> tuple[str, ...]:
    """Normalize, deduplicate, and deterministically order Booru tag names."""
    if not isinstance(values, (list, tuple, set, frozenset)):
        return ()
    return tuple(sorted({normalize_booru_tag(str(value)) for value in values if str(value).strip()}))


class PublishPreparationService:
    """Prepare one current Gelbooru payload using a fresh read, never a POST."""

    def __init__(
        self,
        repository: BatchEntryReader,
        gelbooru_provider: FreshPostReader,
        *,
        now: Callable[[], str] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.repository = repository
        self.gelbooru_provider = gelbooru_provider
        self.now = now or (lambda: datetime.now(UTC).isoformat(timespec="seconds"))
        self.log = log or (lambda _message: None)

    def _fetch_fresh_tags(self, post_id: str) -> tuple[str, ...]:
        try:
            post = self.gelbooru_provider.fetch_post(str(post_id))
        except PostNotFoundError as exc:
            raise FreshPostUnavailableError(f"Gelbooru post {post_id} was not found") from exc
        except ImageSourceError as exc:
            raise FreshPostUnavailableError(f"could not fetch Gelbooru post {post_id}: {exc}") from exc
        except (OSError, ValueError) as exc:
            raise FreshPostUnavailableError(f"invalid fresh Gelbooru response for post {post_id}: {exc}") from exc

        try:
            fresh_tags = stable_tags([tag.name for tag in post.tags])
        except (AttributeError, TypeError) as exc:
            raise FreshPostUnavailableError(
                f"invalid fresh Gelbooru response for post {post_id}"
            ) from exc
        if not fresh_tags:
            raise FreshPostUnavailableError(f"Gelbooru post {post_id} returned no usable tags")
        return fresh_tags

    def prepare(self, item_id: int) -> PublishPreparation:
        entry = self.repository.batch_entry(item_id)
        if entry is None:
            raise NonPublishableBatchEntryError(f"no batch entry for item {item_id}")
        site, post_id = entry["site"], entry["post_id"]
        if site != "gelbooru" or not post_id:
            raise NonPublishableBatchEntryError("only a remote Gelbooru batch entry is publishable")
        publish_state = entry["publish_state"]
        state_value = (
            publish_state.value if isinstance(publish_state, PublishState) else str(publish_state)
        )
        if state_value not in {PublishState.PENDING_PUBLISH.value, PublishState.PUBLISHING.value}:
            raise NonPublishableBatchEntryError(
                f"batch entry is not pending publication: {state_value}"
            )
        fresh_tags = self._fetch_fresh_tags(str(post_id))
        original_tags = stable_tags(entry["original_tags"])
        additions = stable_tags(entry["additions"])
        removals = stable_tags(entry["removals"])
        reviewed_final_tags = stable_tags(entry["reviewed_final_tags"])
        original, fresh = set(original_tags), set(fresh_tags)
        external_additions = tuple(sorted(fresh - original))
        external_removals = tuple(sorted(original - fresh))
        publish_tags = tuple(sorted((fresh - set(removals)) | set(additions)))
        result = PublishPreparation(
            item_id=item_id, site="gelbooru", post_id=str(post_id),
            original_tags=original_tags, reviewed_final_tags=reviewed_final_tags,
            additions=additions, removals=removals, fresh_tags=fresh_tags,
            external_additions=external_additions, external_removals=external_removals,
            publish_tags=publish_tags, prepared_at=self.now(),
        )
        self.log(
            f"Prepare publish Gelbooru #{post_id}: original={len(original_tags)} "
            f"fresh={len(fresh_tags)} our_add={len(additions)} our_remove={len(removals)} "
            f"external_add={len(external_additions)} external_remove={len(external_removals)} "
            f"publish={len(publish_tags)}"
        )
        return result

    def verify_remote(self, prepared: PublishPreparation) -> tuple[str, ...]:
        """Fetch once after submit and require every requested delta to be visible."""
        server_tags = self._fetch_fresh_tags(prepared.post_id)
        server = set(server_tags)
        additions_missing = tuple(
            tag for tag in prepared.additions if tag not in server
        )
        removals_still_present = tuple(
            tag for tag in prepared.removals if tag in server
        )
        self.log(
            f"Publish verification Gelbooru #{prepared.post_id}: "
            f"additions_missing={len(additions_missing)} "
            f"removals_still_present={len(removals_still_present)}"
        )
        if additions_missing or removals_still_present:
            raise PublishVerificationError(additions_missing, removals_still_present)
        return server_tags
