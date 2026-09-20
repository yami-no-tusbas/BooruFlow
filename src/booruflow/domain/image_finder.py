"""Site-neutral models used by Image Finder discovery and future targets."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class SearchMode(StrEnum):
    PARTIAL = "partial"
    EXACT = "exact"
    TITLE_CAPTION = "title_caption"


class RemoteMediaType(StrEnum):
    IMAGE = "image"
    UGOIRA = "ugoira"


class DuplicateStatus(StrEnum):
    NOT_FOUND = "not_found"
    EXACT_SOURCE_MATCH = "exact_source_match"
    EXACT_FILE_MATCH = "exact_file_match"
    VISUAL_MATCH = "visual_match"
    POSSIBLE_VARIANT = "possible_variant"
    UNKNOWN = "unknown"
    ERROR = "error"


class EligibilityStatus(StrEnum):
    UNKNOWN = "unknown"
    ELIGIBLE = "eligible"
    REVIEW_REQUIRED = "review_required"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True, slots=True)
class SearchQuery:
    text: str
    mode: SearchMode = SearchMode.PARTIAL
    cursor: str | None = None
    artist_id: str | None = None


@dataclass(frozen=True, slots=True)
class RemoteArtist:
    source: str
    artist_id: str
    name: str
    profile_url: str | None = None


@dataclass(frozen=True, slots=True)
class RemoteImage:
    source: str
    artwork_id: str
    page_index: int
    source_url: str
    preview_url: str | None
    sample_url: str | None
    original_url: str | None
    width: int | None = None
    height: int | None = None
    media_type: RemoteMediaType = RemoteMediaType.IMAGE

    def __post_init__(self) -> None:
        if not self.source or not self.artwork_id or self.page_index < 0:
            raise ValueError("remote image provenance is incomplete")


@dataclass(frozen=True, slots=True)
class RemoteArtwork:
    source: str
    source_post_id: str
    source_url: str
    artist: RemoteArtist
    title: str
    description: str
    tags: tuple[str, ...]
    rating: str
    created_at: datetime | None
    images: tuple[RemoteImage, ...]

    def __post_init__(self) -> None:
        if not self.source or not self.source_post_id or not self.images:
            raise ValueError("remote artwork requires source identity and at least one image")
        if any(image.artwork_id != self.source_post_id for image in self.images):
            raise ValueError("remote image does not belong to its artwork")

    @property
    def page_count(self) -> int:
        return len(self.images)


@dataclass(frozen=True, slots=True)
class SearchResult:
    artworks: tuple[RemoteArtwork, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class TargetCapabilities:
    supports_upload: bool = False
    supports_duplicate_lookup: bool = False
    supports_source_lookup: bool = False
    supports_md5_lookup: bool = False
    supports_visual_duplicate_lookup: bool = False
    content_policy: str = "target-specific review required"


@dataclass(frozen=True, slots=True)
class Candidate:
    image: RemoteImage
    artwork: RemoteArtwork
    target: str
    duplicate_status: DuplicateStatus = DuplicateStatus.UNKNOWN
    eligibility_status: EligibilityStatus = EligibilityStatus.UNKNOWN
    local_file: Path | None = None
    prepared_tags: tuple[str, ...] = field(default_factory=tuple)


class ImageSource(Protocol):
    source_id: str

    def search(self, query: SearchQuery) -> SearchResult: ...

    def artwork(self, artwork_id: str) -> RemoteArtwork: ...


class ImageTarget(Protocol):
    target_id: str

    @property
    def capabilities(self) -> TargetCapabilities: ...
