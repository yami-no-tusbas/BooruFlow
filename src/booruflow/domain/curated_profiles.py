"""Immutable, locally validated semantic knowledge; no statistical persistence."""
from dataclasses import dataclass
from urllib.parse import quote, unquote


def refinement_token(trigger, tag):
    return f"curated:{quote(trigger, safe='')}:{quote(tag, safe='')}"


def parse_refinement_token(token):
    _prefix, trigger, tag = token.split(":", 2)
    return unquote(trigger), unquote(tag)


class CuratedProfileIndex(dict):
    """Canonical identity map resolved at load time, including active aliases."""

    def __init__(self, profiles=(), canonical_names=None):
        super().__init__(profiles)
        self.canonical_names = canonical_names or {}

    def canonical(self, name):
        return self.canonical_names.get(name, name)


@dataclass(frozen=True, slots=True)
class CuratedTag:
    name: str
    category: int
    post_count: int
    packs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CuratedGroup:
    id: str
    labels: dict[str, str]
    tags: tuple[CuratedTag, ...]

    def label(self, locale: str) -> str:
        locale = locale.replace("-", "_").split("_")[0]
        return self.labels.get(locale) or self.labels.get("en") or self.id


@dataclass(frozen=True, slots=True)
class CuratedProfile:
    trigger: str
    groups: tuple[CuratedGroup, ...]
    top_count: int
    min_post_count: int
    packs: tuple[str, ...]

    @property
    def tags(self) -> dict[str, CuratedTag]:
        return {tag.name: tag for group in self.groups for tag in group.tags}

    @property
    def top(self) -> tuple[CuratedTag, ...]:
        return tuple(sorted(self.tags.values(), key=lambda tag: (-tag.post_count, tag.name))
                     [:self.top_count])
