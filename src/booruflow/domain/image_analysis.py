"""Pure models and state rules for local image analysis."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path


class InputKind(StrEnum):
    LOCAL_FILE = "local_file"
    GELBOORU_POST = "gelbooru_post"
    E621_POST = "e621_post"


class AnalysisState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY_FOR_REVIEW = "ready_for_review"
    REVIEWED = "reviewed"
    FAILED = "failed"
    SKIPPED = "skipped"


class DecisionState(StrEnum):
    UNREVIEWED = "unreviewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class PublishState(StrEnum):
    """Lifecycle of a saved Tagging review, independent from analysis state."""

    REVIEWED = "reviewed"
    PENDING_PUBLISH = "pending_publish"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class ObservationSource(StrEnum):
    GELBOORU = "gelbooru"
    E621 = "e621"
    WD14 = "wd14"
    HYDRA = "hydra"
    YOLO = "yolo"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class DetectedLocalSource:
    site: str
    post_id: str
    confidence: str = "high"


@dataclass(frozen=True, slots=True)
class ParsedBooruFilename:
    artist: str
    post_id: str
    rating: str
    source_md5: str

    @property
    def artists(self)->tuple[str,...]:
        return tuple(value.strip() for value in self.artist.split(" & ") if value.strip())


_COLLECTION_MARKERS = {
    "gelbooru": re.compile(r"\(gelbooru\)", re.IGNORECASE),
    "e621": re.compile(r"\(e621\)", re.IGNORECASE),
}
_COLLECTION_FILENAME = re.compile(
    r"^.+?\s+-\s+(?P<post_id>[1-9]\d*)\s+-\s+"
    r"(?P<rating>safe|questionable|explicit|general|sensitive)\s+-\s+.+$",
    re.IGNORECASE,
)
_RATINGS=frozenset({"safe","general","sensitive","questionable","explicit"})
_SOURCE_MD5=re.compile(r"[0-9a-fA-F]{32}")


def collection_site_from_path(path:Path)->str|None:
    sites=[site for site,marker in _COLLECTION_MARKERS.items() if marker.search(str(path))]
    return sites[0] if len(sites)==1 else None


def parse_booru_filename(path:Path)->ParsedBooruFilename|None:
    """Parse ``artist - id - rating - md5`` from the three rightmost separators."""
    parts=path.stem.rsplit(" - ",3)
    if len(parts)!=4:return None
    artist,post_id,rating,source_md5=(value.strip() for value in parts);rating=rating.casefold()
    if not artist or not post_id.isdigit() or int(post_id)<=0:return None
    if rating not in _RATINGS or not _SOURCE_MD5.fullmatch(source_md5):return None
    return ParsedBooruFilename(artist,post_id,rating,source_md5.casefold())


def detect_local_source(path: Path) -> DetectedLocalSource | None:
    """Recognize the explicit ``(site)`` + ``artist - id - rating - hash`` convention."""
    site=collection_site_from_path(path);parsed=parse_booru_filename(path)
    return DetectedLocalSource(site,parsed.post_id) if site and parsed else None


VALID_TRANSITIONS = {
    AnalysisState.PENDING: frozenset(
        {AnalysisState.PROCESSING, AnalysisState.FAILED, AnalysisState.SKIPPED}
    ),
    AnalysisState.PROCESSING: frozenset(
        {AnalysisState.PENDING, AnalysisState.READY_FOR_REVIEW, AnalysisState.FAILED}
    ),
    AnalysisState.READY_FOR_REVIEW: frozenset(
        {AnalysisState.REVIEWED, AnalysisState.PROCESSING, AnalysisState.SKIPPED}
    ),
    AnalysisState.REVIEWED: frozenset(),
    AnalysisState.FAILED: frozenset({AnalysisState.PENDING, AnalysisState.SKIPPED}),
    AnalysisState.SKIPPED: frozenset({AnalysisState.PENDING, AnalysisState.READY_FOR_REVIEW}),
}


def validate_transition(current: AnalysisState, target: AnalysisState) -> None:
    if target not in VALID_TRANSITIONS[current]:
        raise ValueError(f"invalid analysis transition: {current} -> {target}")


@dataclass(frozen=True, slots=True)
class SourceReference:
    kind: InputKind
    original_path: Path | None = None
    site: str | None = None
    post_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind is InputKind.LOCAL_FILE:
            if self.original_path is None or self.site is not None or self.post_id is not None:
                raise ValueError("a local source requires only original_path")
        else:
            expected = "gelbooru" if self.kind is InputKind.GELBOORU_POST else "e621"
            if self.site != expected or not str(self.post_id or "").strip():
                raise ValueError(f"{self.kind} requires site={expected!r} and a post_id")


@dataclass(frozen=True, slots=True)
class AnalysisItem:
    source: SourceReference
    state: AnalysisState = AnalysisState.PENDING
    id: int | None = None
    cached_path: Path | None = None
    content_sha256: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        if bool(self.width) != bool(self.height):
            raise ValueError("width and height must be set together")
        if self.width is not None and (self.width < 1 or self.height is None or self.height < 1):
            raise ValueError("image dimensions must be positive")
        if self.content_sha256 is not None and (
            len(self.content_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.content_sha256)
        ):
            raise ValueError("content_sha256 must be a lowercase SHA-256 hex digest")

    def transition_to(self, state: AnalysisState) -> AnalysisItem:
        validate_transition(self.state, state)
        return replace(self, state=state)


@dataclass(frozen=True, slots=True)
class SourceTag:
    name: str
    source: ObservationSource
    category: str | None = None

    def __post_init__(self) -> None:
        if self.source not in {ObservationSource.GELBOORU, ObservationSource.E621}:
            raise ValueError("source tags must come from Gelbooru or e621")
        if not self.name.strip():
            raise ValueError("tag name must not be empty")


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    backend: str
    name: str
    version: str = ""
    configuration_hash: str = ""
    device: str = ""

    def __post_init__(self) -> None:
        if not self.backend.strip() or not self.name.strip():
            raise ValueError("backend and model name must not be empty")


@dataclass(frozen=True, slots=True)
class TagObservation:
    name: str
    source: ObservationSource
    confidence: float | None = None
    decision: DecisionState = DecisionState.UNREVIEWED
    reviewed_name: str | None = None
    model: ModelIdentity | None = None
    category: str | None = None
    raw_tag_name: str | None = None
    source_present: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("tag name must not be empty")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between zero and one")
        if self.source is ObservationSource.MANUAL and self.confidence is not None:
            raise ValueError("manual observations have no confidence score")


@dataclass(frozen=True, slots=True)
class HumanDecision:
    state: DecisionState
    reviewed_name: str | None = None

    def __post_init__(self) -> None:
        if self.reviewed_name is not None and not self.reviewed_name.strip():
            raise ValueError("reviewed_name must not be blank")


@dataclass(frozen=True, slots=True)
class ObjectDetection:
    label: str
    confidence: float
    bounding_box: tuple[float, float, float, float]
    model: ModelIdentity

    def __post_init__(self) -> None:
        x_min, y_min, x_max, y_max = self.bounding_box
        if not self.label.strip() or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("invalid object detection")
        if not (0.0 <= x_min < x_max <= 1.0 and 0.0 <= y_min < y_max <= 1.0):
            raise ValueError("bounding box must be normalized and ordered")


@dataclass(frozen=True, slots=True)
class ColorStatistics:
    dominant_colors: tuple[str, ...]
    mean_saturation: float
    mean_luminance: float
    luminance_stddev: float
    contrast: float
    pastel_score: float | None = None

    def __post_init__(self) -> None:
        values = (
            self.mean_saturation,
            self.mean_luminance,
            self.luminance_stddev,
            self.contrast,
        )
        if any(value < 0.0 for value in values):
            raise ValueError("color statistics must not be negative")
        if self.pastel_score is not None and not 0.0 <= self.pastel_score <= 1.0:
            raise ValueError("pastel score must be between zero and one")


@dataclass(frozen=True, slots=True)
class EmbeddingDescriptor:
    dimensions: int
    dtype: str
    normalized: bool
    model: ModelIdentity

    def __post_init__(self) -> None:
        if self.dimensions < 1 or not self.dtype.strip():
            raise ValueError("embedding dimensions and dtype must be valid")
