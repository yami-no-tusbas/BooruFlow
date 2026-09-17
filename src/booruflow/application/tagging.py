"""Use-case models for manual under-tagged-post review."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class TaggingRequest:
    query: str
    pages_per_block: int
    start_page: int
    minimum_tags: int
    maximum_tags: int
    critical_maximum: int
    high_maximum: int
    site: str = "gelbooru"

    def __post_init__(self) -> None:
        if self.pages_per_block < 1 or self.start_page < 1:
            raise ValueError("page values must be positive")
        if self.minimum_tags < 0 or self.maximum_tags < self.minimum_tags:
            raise ValueError("invalid tag-count range")
        if not self.minimum_tags <= self.critical_maximum <= self.high_maximum <= self.maximum_tags:
            raise ValueError("thresholds must satisfy minimum <= critical <= high <= maximum")
        if self.site not in {"gelbooru", "e621"}:
            raise ValueError("unsupported tagging site")


def tagging_priority(count: int, critical_maximum: int, high_maximum: int) -> str:
    if count <= critical_maximum:
        return "critical"
    if count <= high_maximum:
        return "high"
    return "low"


class LocalMatchState(StrEnum):
    EXACT = "exact"
    MAPPING = "mapping"
    MISSING = "missing"
    ALREADY_PRESENT = "already_present"


RATING_NAMES = frozenset({"safe", "sensitive", "questionable", "explicit", "general"})


def is_rating_observation(name: str, category: str | None) -> bool:
    """Prefer persisted WD14 category, with a legacy-name safety net."""
    if normalize_booru_tag(category or "") == "rating":
        return True
    return normalize_booru_tag(name) in RATING_NAMES


@dataclass(frozen=True, slots=True)
class LocalTagMatch:
    source_tag: str
    target_tag: str | None
    state: LocalMatchState


def normalize_booru_tag(value: str) -> str:
    """Conservative comparison form; it never invents a semantic alias."""
    return "_".join(value.strip().casefold().split())


def match_local_tag(
    suggestion: str,
    local_names: set[str],
    source_tags: set[str],
    mapped_target: str | None = None,
) -> LocalTagMatch:
    source = normalize_booru_tag(suggestion)
    present = {normalize_booru_tag(value) for value in source_tags}
    if source in present:
        return LocalTagMatch(suggestion, source, LocalMatchState.ALREADY_PRESENT)
    local = {normalize_booru_tag(value) for value in local_names}
    if source in local:
        return LocalTagMatch(suggestion, source, LocalMatchState.EXACT)
    if mapped_target:
        target = normalize_booru_tag(mapped_target)
        if target in present:
            return LocalTagMatch(suggestion, target, LocalMatchState.ALREADY_PRESENT)
        if target in local:
            return LocalTagMatch(suggestion, target, LocalMatchState.MAPPING)
    return LocalTagMatch(suggestion, None, LocalMatchState.MISSING)


def tags_to_add(matches: list[LocalTagMatch]) -> list[str]:
    return list(dict.fromkeys(
        match.target_tag for match in matches
        if match.target_tag and match.state in {LocalMatchState.EXACT, LocalMatchState.MAPPING}
    ))


def build_clipboard_tags(names: list[str]) -> str:
    """Build the deterministic append-ready value shared by every copy action."""
    normalized = list(dict.fromkeys(normalize_booru_tag(name) for name in names if name.strip()))
    return f" {' '.join(normalized)}" if normalized else ""


def build_final_tags_clipboard(names: list[str]) -> str:
    """Build the replace-ready complete tag list used by Tagging review."""
    return " ".join(dict.fromkeys(
        normalize_booru_tag(name) for name in names if name.strip()
    ))


def parse_review_row_token(value: object) -> tuple[str, str | int]:
    """Return the persistent target addressed by one unified-review row.

    Existing source tags are deliberately opaque string tokens.  Only WD14
    observation tokens are numeric, so keeping this distinction here prevents
    a bulk UI path from accidentally coercing ``existing:<tag>`` with ``int``.
    """
    token = str(value)
    if token.startswith("existing:"):
        return "existing", token.removeprefix("existing:")
    return "observation", int(token)


def analysis_resume_action(state: str) -> str:
    return {
        "ready_for_review": "reuse",
        "reviewed": "reuse",
        "skipped": "restore_review",
        "failed": "retry",
        "pending": "restore_pending",
        "processing": "follow",
    }.get(state, "follow")
