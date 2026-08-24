"""Application orchestration for the persistent image-analysis workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from booruflow.domain.image_analysis import (
    AnalysisItem,
    AnalysisState,
    DecisionState,
    InputKind,
    TagObservation,
)


@dataclass(frozen=True, slots=True)
class QueuePolicy:
    download_prefetch: int = 10
    analysis_prefetch: int = 2

    def __post_init__(self) -> None:
        if self.download_prefetch < 1 or self.analysis_prefetch < 1:
            raise ValueError("prefetch depths must be positive")


@dataclass(frozen=True, slots=True)
class PipelineStatus:
    unresolved: int = 0
    resolved: int = 0
    pending: int = 0
    processing: int = 0
    ready_for_review: int = 0
    reviewed: int = 0
    failed: int = 0
    skipped: int = 0


class AnalysisWorkflowRepository(Protocol):
    def add_unresolved_remote(self, kind: InputKind, post_id: str, priority: int = 0) -> int: ...
    def queue_counts(self) -> dict[str, int]: ...
    def claim_sources_to_resolve(self, limit: int) -> list[int]: ...
    def activate_next_review(self) -> AnalysisItem | None: ...
    def finish_review(self, item_id: int, target: AnalysisState) -> None: ...
    def retry(self, item_id: int) -> None: ...
    def add_manual_observation(self, item_id: int, name: str) -> int: ...
    def observations(self, item_id: int) -> list[tuple[int, TagObservation]]: ...
    def decide_observation(
        self, observation_id: int, decision: DecisionState, reviewed_name: str | None = None
    ) -> None: ...


class LocalSourceAdder(Protocol):
    def add_local(self, path: Path) -> int: ...


class ImageAnalysisWorkflow:
    """UI-independent facade; SQLite remains the source of truth."""

    def __init__(
        self,
        repository: AnalysisWorkflowRepository,
        local_sources: LocalSourceAdder,
        policy: QueuePolicy | None = None,
    ) -> None:
        self.repository = repository
        self.local_sources = local_sources
        self.policy = policy or QueuePolicy()

    def add_local_files(self, paths: list[Path]) -> list[int]:
        return [self.local_sources.add_local(path) for path in paths]

    def add_remote_ids(
        self, kind: InputKind, post_ids: list[str], *, priority: int = 0
    ) -> list[int]:
        clean = [value.strip() for value in post_ids if value.strip()]
        return [self.repository.add_unresolved_remote(kind, value, priority) for value in clean]

    def sources_to_resolve(self) -> list[int]:
        return self.repository.claim_sources_to_resolve(self.policy.download_prefetch)

    def status(self) -> PipelineStatus:
        counts = self.repository.queue_counts()
        return PipelineStatus(**{
            field: counts.get(field, 0) for field in PipelineStatus.__dataclass_fields__
        })

    def next_for_review(self) -> AnalysisItem | None:
        return self.repository.activate_next_review()

    def complete_review(self, item_id: int) -> AnalysisItem | None:
        self.repository.finish_review(item_id, AnalysisState.REVIEWED)
        return self.next_for_review()

    def skip_review(self, item_id: int) -> AnalysisItem | None:
        self.repository.finish_review(item_id, AnalysisState.SKIPPED)
        return self.next_for_review()

    def retry(self, item_id: int) -> None:
        self.repository.retry(item_id)

    def add_manual_tag(self, item_id: int, name: str) -> int:
        if not name.strip():
            raise ValueError("manual tag must not be empty")
        return self.repository.add_manual_observation(item_id, name)

    def decide(
        self, observation_id: int, decision: DecisionState, reviewed_name: str | None = None
    ) -> None:
        self.repository.decide_observation(observation_id, decision, reviewed_name)
