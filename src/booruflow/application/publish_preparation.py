"""Read-only preparation of a future Gelbooru tag publication."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
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
    """Prepare one current site payload using a fresh read, never a write."""

    def __init__(
        self,
        repository: BatchEntryReader,
        gelbooru_provider: FreshPostReader,
        *,
        site: str = "gelbooru",
        alias_database: Path | None = None,
        now: Callable[[], str] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.repository = repository
        self.gelbooru_provider = gelbooru_provider
        self.site = site
        self.now = now or (lambda: datetime.now(UTC).isoformat(timespec="seconds"))
        self.log = log or (lambda _message: None)
        self.alias_database = alias_database
        self._alias_unavailable_logged = False
        self._alias_empty_logged = False

    def _log_alias_unavailable(self, post_id: str, tag: str, reason: str) -> None:
        """Keep literal fallback diagnosable without logging every batch tag."""
        if self._alias_unavailable_logged:
            return
        self._alias_unavailable_logged = True
        self.log(
            f"Publish verification Gelbooru #{post_id}: alias resolution unavailable "
            f"for {tag}: {reason}; using literal fallback"
        )

    def _resolved_alias(self, tag: str, post_id: str) -> str:
        from booruflow.infrastructure.gelbooru_aliases import inspect_alias_catalog

        status = inspect_alias_catalog(self.alias_database)
        if not status.available:
            configured_path = status.path if status.path is not None else "<none>"
            self._log_alias_unavailable(
                post_id, tag, f"configured_path={configured_path}; reason={status.reason}"
            )
            return tag
        if status.active_aliases == 0 and not self._alias_empty_logged:
            self._alias_empty_logged = True
            self.log(
                f"Publish verification Gelbooru #{post_id}: alias catalogue has no active aliases "
                f"at {status.path}; {tag} is being checked literally"
            )
        try:
            from booruflow.infrastructure.gelbooru_aliases import resolve_gelbooru_alias
            return resolve_gelbooru_alias(tag, self.alias_database)
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            self._log_alias_unavailable(post_id, tag, str(exc))
            return tag

    def _fetch_fresh_tags(self, post_id: str) -> tuple[str, ...]:
        try:
            post = self.gelbooru_provider.fetch_post(str(post_id))
        except PostNotFoundError as exc:
            raise FreshPostUnavailableError(f"{self.site} post {post_id} was not found") from exc
        except ImageSourceError as exc:
            raise FreshPostUnavailableError(f"could not fetch {self.site} post {post_id}: {exc}") from exc
        except (OSError, ValueError) as exc:
            raise FreshPostUnavailableError(f"invalid fresh {self.site} response for post {post_id}: {exc}") from exc

        try:
            fresh_tags = stable_tags([tag.name for tag in post.tags])
        except (AttributeError, TypeError) as exc:
            raise FreshPostUnavailableError(
                f"invalid fresh {self.site} response for post {post_id}"
            ) from exc
        if not fresh_tags:
            raise FreshPostUnavailableError(f"{self.site} post {post_id} returned no usable tags")
        return fresh_tags

    def prepare(self, item_id: int) -> PublishPreparation:
        entry = self.repository.batch_entry(item_id)
        if entry is None:
            raise NonPublishableBatchEntryError(f"no batch entry for item {item_id}")
        site, post_id = entry["site"], entry["post_id"]
        if site != self.site or not post_id:
            label = "Gelbooru" if self.site == "gelbooru" else self.site
            raise NonPublishableBatchEntryError(
                f"only a remote {label} batch entry is publishable"
            )
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
            item_id=item_id, site=self.site, post_id=str(post_id),
            original_tags=original_tags, reviewed_final_tags=reviewed_final_tags,
            additions=additions, removals=removals, fresh_tags=fresh_tags,
            external_additions=external_additions, external_removals=external_removals,
            publish_tags=publish_tags, prepared_at=self.now(),
        )
        self.log(
            f"Prepare publish {self.site} #{post_id}: original={len(original_tags)} "
            f"fresh={len(fresh_tags)} our_add={len(additions)} our_remove={len(removals)} "
            f"external_add={len(external_additions)} external_remove={len(external_removals)} "
            f"publish={len(publish_tags)}"
        )
        return result

    def verify_remote(self, prepared: PublishPreparation) -> tuple[str, ...]:
        """Fetch once after submit and require every requested delta to be visible."""
        server_tags = self._fetch_fresh_tags(prepared.post_id)
        server = set(server_tags)
        additions_missing = []
        for tag in prepared.additions:
            resolved = (
                self._resolved_alias(tag, prepared.post_id)
                if self.site == "gelbooru"
                else tag
            )
            if tag not in server and resolved not in server:
                additions_missing.append(tag)
            if resolved != tag:
                self.log(
                    f"Publish verification Gelbooru #{prepared.post_id}: "
                    f"addition {tag} resolved to {resolved}: "
                    f"{'present' if resolved in server else 'missing'}"
                )
        # A removal of an active alias is verified against both spellings: Gelbooru
        # may expose the canonical target even if the submitted source was accepted.
        removals_still_present = tuple(
            tag
            for tag in prepared.removals
            if tag in server
            or (
                self.site == "gelbooru"
                and self._resolved_alias(tag, prepared.post_id) in server
            )
        )
        additions_missing = tuple(additions_missing)
        self.log(
            f"Publish verification {self.site} #{prepared.post_id}: "
            f"additions_missing={len(additions_missing)} "
            f"removals_still_present={len(removals_still_present)}"
        )
        if additions_missing or removals_still_present:
            raise PublishVerificationError(additions_missing, removals_still_present)
        return server_tags
