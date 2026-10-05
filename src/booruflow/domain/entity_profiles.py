"""Statistical evidence from explicitly selected Gelbooru entity datasets."""
import re
from dataclasses import dataclass

from booruflow.domain.booru_sites import category_name

RELATION_CATEGORIES = frozenset({"character", "copyright", "artist", "species", "lore"})
DEFAULT_EXCLUSIONS = frozenset({
    "smile", "open_mouth", "looking_at_viewer", "blush", "standing", "sitting",
    "outdoors", "indoors", "sky", "cloud", "tree", "grass", "simple_background",
})
STATUSES = frozenset({"keep", "review", "ignore"})
COMPOSITION_TAGS = frozenset({
    "solo", "solo_focus", "multiple_girls", "multiple_boys", "multiple_others",
    "multiple_futa", "multiple_futas", "multiple_girl", "multiple_boy", "multiple_other",
})
COMPOSITION_COUNT = re.compile(r"[1-9][0-9]*\+?(?:girls?|boys?|others?|futas?)\Z")


def automatic_exclusion(tag: str, category: str | int | None) -> bool:
    """Composition and technical metadata are evidence, not entity attributes."""
    return (tag in COMPOSITION_TAGS or bool(COMPOSITION_COUNT.fullmatch(tag))
            or category_name("gelbooru", category) == "meta")


def contextual_relation_visible(source_category, tag: str, category) -> bool:
    """Directional policy shared by review and suggestions; never deletes evidence."""
    return (tag != "anonymous" and not (
        category_name("gelbooru", source_category) in {"character", "copyright"}
        and category_name("gelbooru", category) == "artist"))


def tag_kind(category: str | int | None) -> str:
    return "relation" if category_name("gelbooru", category) in RELATION_CATEGORIES else "attribute"


@dataclass(frozen=True, slots=True)
class EntitySuggestion:
    profile_id: int
    entity_tag: str
    tag: str
    category: str
    frequency: float


def activation_tags(observed, confirmed, derived=()) -> set[str]:
    """Derived evidence never becomes a seed, even when it has a high score."""
    return set(observed) | set(confirmed)
