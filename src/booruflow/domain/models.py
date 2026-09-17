"""Small immutable models shared by BooruFlow use cases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Site(StrEnum):
    GELBOORU = "gelbooru"
    E621 = "e621"


class EntityType(StrEnum):
    ARTISTS = "artists"
    COPYRIGHTS = "copyrights"
    CHARACTERS = "characters"
    SPECIES = "species"


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    sites: tuple[Site, ...]
    entity_type: EntityType
    start_page: int = 1
    page_count: int = 10
    minimum_results: int = 0
    maximum_results: int = 0
    minimum_match_percent: int = 0

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must not be empty")
        if not self.sites:
            raise ValueError("at least one site is required")
        if self.start_page < 1 or self.page_count < 1:
            raise ValueError("page values must be positive")
        if not 0 <= self.minimum_match_percent <= 100:
            raise ValueError("minimum_match_percent must be between 0 and 100")
        if self.maximum_results and self.maximum_results < self.minimum_results:
            raise ValueError("maximum_results must be zero or greater than minimum_results")


@dataclass(frozen=True, slots=True)
class ToolAvailability:
    available: bool
    reason: str = ""

