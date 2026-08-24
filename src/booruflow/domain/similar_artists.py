"""Domain values for persistent artist signatures and independent vector spaces."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True, order=True)
class ArtistIdentity:
    site: str
    tag: str

    def __post_init__(self) -> None:
        if self.site not in {"local", "gelbooru", "e621"} or not self.tag.strip():
            raise ValueError("artist identity requires a supported site and a tag")


@dataclass(frozen=True, slots=True)
class EmbeddingSpace:
    backend: str
    model_name: str
    model_version: str
    configuration_hash: str
    dimensions: int
    dtype: str = "float32"
    normalized: bool = True
    runtime: str = ""
    device: str = ""

    @property
    def key(self) -> str:
        return f"{self.backend}|{self.model_name}|{self.model_version}|{self.configuration_hash}"


@dataclass(frozen=True, slots=True)
class DispersionMetrics:
    mean_similarity: float
    distance_variance: float
    minimum_similarity: float
    maximum_similarity: float


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    space: EmbeddingSpace
    centroid: tuple[float, ...]
    dispersion: DispersionMetrics


@dataclass(frozen=True, slots=True)
class PaletteMetric:
    mean: float
    variance: float


@dataclass(frozen=True, slots=True)
class ArtistProfile:
    artist: ArtistIdentity
    image_count: int
    embeddings: dict[str, EmbeddingProfile] = field(default_factory=dict)
    palette: dict[str, PaletteMetric] = field(default_factory=dict)
    source_tag_frequency: dict[str, int] = field(default_factory=dict)
    accepted_wd14_frequency: dict[str, int] = field(default_factory=dict)
    profile_version: str = "1"
    dependency_hash: str = ""
    built_at: str = ""

    @property
    def confidence_level(self) -> str:
        if self.image_count <= 1:
            return "very_low"
        if self.image_count <= 4:
            return "low"
        if self.image_count <= 9:
            return "medium"
        return "established"


@dataclass(frozen=True, slots=True)
class ArtistRanking:
    artist: ArtistIdentity
    centroid_similarity: float
    mean_top_k_similarity: float | None
    best_image_similarity: float | None
    image_count: int
    coherence: float


@dataclass(frozen=True, slots=True)
class SimilarityQueryProfile:
    item_ids: tuple[int, ...]
    embeddings: dict[str, EmbeddingProfile]
    item_similarity: dict[str, dict[int, float]] = field(default_factory=dict)
    palette: dict[str, PaletteMetric] = field(default_factory=dict)

    @property
    def image_count(self) -> int:
        return len(self.item_ids)

    @property
    def quality_level(self) -> str:
        if self.image_count <= 1: return "very_low"
        if self.image_count <= 4: return "low"
        if self.image_count <= 9: return "correct"
        return "solid"
