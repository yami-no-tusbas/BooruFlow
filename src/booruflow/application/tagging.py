"""Use-case models for manual under-tagged-post review."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaggingRequest:
    query: str
    pages_per_block: int
    start_page: int
    minimum_tags: int
    maximum_tags: int
    critical_maximum: int
    high_maximum: int

    def __post_init__(self) -> None:
        if self.pages_per_block < 1 or self.start_page < 1:
            raise ValueError("page values must be positive")
        if self.minimum_tags < 0 or self.maximum_tags < self.minimum_tags:
            raise ValueError("invalid tag-count range")
        if not self.minimum_tags <= self.critical_maximum <= self.high_maximum <= self.maximum_tags:
            raise ValueError("thresholds must satisfy minimum <= critical <= high <= maximum")


def tagging_priority(count: int, critical_maximum: int, high_maximum: int) -> str:
    if count <= critical_maximum:
        return "critical"
    if count <= high_maximum:
        return "high"
    return "low"
