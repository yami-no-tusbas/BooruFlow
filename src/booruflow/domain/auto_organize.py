"""Pure models and rules for safe booru file organization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from booruflow.domain.image_analysis import ParsedBooruFilename


class OrganizeMode(StrEnum):
    ORGANIZE = "organize"
    REFRESH_ONLY = "refresh_only"


class PlanStatus(StrEnum):
    UNCHANGED = "unchanged"
    RENAME = "rename"
    MOVE = "move"
    RENAME_MOVE = "rename_move"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    UNRECOGNIZED = "unrecognized"
    ERROR = "error"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class PostMetadata:
    site: str
    post_id: str
    tags: tuple[str, ...]
    categories: dict[str, str] = field(default_factory=dict)
    artists: tuple[str, ...] = ()
    copyrights: tuple[str, ...] = ()
    characters: tuple[str, ...] = ()
    species: tuple[str, ...] = ()
    rating: str = ""
    source: str = ""
    md5: str = ""
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OrganizeRule:
    rule_id: str
    group: str
    priority: int
    sites: tuple[str, ...]
    tags: tuple[str, ...]
    destination: str
    active: bool = True


@dataclass(frozen=True, slots=True)
class RuleNode:
    node_id: str
    label: str
    kind: str = "branch"
    destination: str = ""
    tags: tuple[str, ...] = ()
    sites: tuple[str, ...] = ("gelbooru", "e621")
    active: bool = True
    source: str = ""
    special: str = ""
    ordered: bool = True
    children: tuple[RuleNode, ...] = ()


@dataclass(frozen=True, slots=True)
class RuleDecision:
    destination: str | None
    winner: str = ""
    candidates: tuple[str, ...] = ()
    ambiguous: bool = False
    reason: str = ""
    winner_path: tuple[str, ...] = ()
    matched_paths: tuple[str, ...] = ()
    route: str = "normal"
    fallback: str = ""
    classification: str = ""
    has_tag_match: bool = False


@dataclass(slots=True)
class FilePlan:
    source: Path
    mode: OrganizeMode = OrganizeMode.ORGANIZE
    site: str = ""
    post_id: str = ""
    fetch_state: str = ""
    cache_hit: bool = False
    api_calls: int = 0
    error_signature: str = ""
    infrastructure_error: bool = False
    current_artist: str = ""
    remote_artist: str = ""
    future_name: str = ""
    destination: Path | None = None
    winner: str = ""
    candidates: tuple[str, ...] = ()
    winner_path: tuple[str, ...] = ()
    route: str = "normal"
    fallback: str = ""
    classification: str = ""
    has_tag_match: bool = False
    status: PlanStatus = PlanStatus.ERROR
    message: str = ""
    source_size: int | None = None
    source_mtime_ns: int | None = None


_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename_part(value: str) -> str:
    return _INVALID_FILENAME.sub("_", value).strip(" .") or "anonymous"


def canonical_filename(parsed: ParsedBooruFilename, metadata: PostMetadata, suffix: str) -> str:
    artist = " & ".join(metadata.artists) if metadata.artists else parsed.artist
    rating = metadata.rating or parsed.rating
    # The local hash is an identity field: a remote mismatch is reported elsewhere, never adopted.
    return f"{safe_filename_part(artist)} - {parsed.post_id} - {rating} - {parsed.source_md5}{suffix}"


class RuleEngine:
    """Hierarchical engine: sibling order determines priority at every level."""

    def __init__(self, rules: tuple[RuleNode, ...] | tuple[OrganizeRule, ...]) -> None:
        if rules and isinstance(rules[0], OrganizeRule):
            grouped: dict[int, list[OrganizeRule]] = {}
            for rule in rules: grouped.setdefault(rule.priority, []).append(rule)
            self.rules = tuple(RuleNode(f"legacy_{priority}", "Tags", "branch", ordered=False,
                children=tuple(RuleNode(r.rule_id, r.rule_id, "rule", r.destination,
                    r.tags, r.sites, r.active) for r in grouped[priority]))
                for priority in sorted(grouped))
        else:
            self.rules = rules

    def decide(self, metadata: PostMetadata) -> RuleDecision:
        matches: list[tuple[tuple[int, ...], tuple[str, ...], RuleNode, str]] = []
        unordered_conflict = False
        route_node: RuleNode | None = None

        def matches_tag_rule(node: RuleNode) -> bool:
            return (
                node.active
                and metadata.site in node.sites
                and bool(set(metadata.tags).intersection(node.tags))
            )

        def find_route(nodes: tuple[RuleNode, ...]) -> RuleNode | None:
            for node in nodes:
                if node.kind == "route" and matches_tag_rule(node):
                    return node
                found = find_route(node.children)
                if found is not None:
                    return found
            return None

        route_node = find_route(self.rules)
        if route_node is not None and route_node.special == "ambiguous":
            labels = (route_node.label,)
            return RuleDecision(
                None,
                route_node.node_id,
                (route_node.label,),
                True,
                "Branche Garçons détectée; sous-dossier à vérifier",
                labels,
                (route_node.label,),
                route_node.label,
            )

        def walk(nodes: tuple[RuleNode, ...], indices: tuple[int, ...], labels: tuple[str, ...], ordered: bool) -> int:
            nonlocal unordered_conflict
            matched_children = 0
            for index, node in enumerate(nodes):
                if not node.active:
                    continue
                if node.kind == "route" or node.node_id == "dedicated":
                    continue
                before = len(matches); node_indices = indices + (index,); node_labels = labels + (node.label,)
                if node.kind == "dynamic":
                    values = tuple(getattr(metadata, node.source, ()))
                    if values:
                        if node.special == "copyright_character":
                            copyrights = tuple(dict.fromkeys(values))
                            characters = tuple(dict.fromkeys(metadata.characters))
                            destination_parts = [safe_filename_part(" ".join(copyrights))]
                            if characters:
                                destination_parts.append(safe_filename_part(" ".join(characters)))
                            destination = "/".join(destination_parts)
                            labels_for_value = node_labels + copyrights + characters
                            matches.append((node_indices, labels_for_value, node, destination))
                        else:
                            matches.append((node_indices, node_labels + values, node, "\0".join(values)))
                elif node.kind == "rule" and metadata.site in node.sites and set(metadata.tags).intersection(node.tags):
                    matches.append((node_indices, node_labels, node, ""))
                if node.children: walk(node.children, node_indices, node_labels, node.ordered)
                if len(matches) > before: matched_children += 1
            if not ordered and matched_children > 1: unordered_conflict = True
            return matched_children

        walk(self.rules, (), (), True)
        if matches:
            paths = tuple(" / ".join(labels) for _, labels, _, _ in sorted(matches))
            has_tag_match = any(labels and labels[0] == "Tags" for _, labels, _, _ in matches)
            if unordered_conflict:
                return RuleDecision(None, candidates=paths, ambiguous=True,
                    reason="Plusieurs règles correspondent dans un niveau non ordonné", matched_paths=paths,
                    route=route_node.label if route_node else "normal", has_tag_match=has_tag_match)
            matches.sort(key=lambda item: item[0]); _, labels, node, value = matches[0]
            if node.special == "ambiguous":
                return RuleDecision(
                    None, node.node_id, paths, True,
                    "Branche Garçons détectée; sous-dossier à vérifier", labels, paths,
                    route=route_node.label if route_node else "normal",
                    has_tag_match=has_tag_match,
                )
            if "\0" in value:
                return RuleDecision(
                    None, node.node_id, paths, True,
                    f"Plusieurs valeurs {node.label} sans ordre explicite", labels, paths,
                    route=route_node.label if route_node else "normal",
                    has_tag_match=has_tag_match,
                )
            destination = (
                f"{node.destination.rstrip('/')}/{value}"
                if node.special == "copyright_character"
                else node.destination.replace("{value}", safe_filename_part(value))
            )
            if route_node is not None:
                relative = destination.removeprefix("Tags/")
                destination = f"{route_node.destination.rstrip('/')}/{relative}"
            loser = paths[1] if len(paths) > 1 else "aucune autre règle"
            classification = labels[0].casefold() if labels else ""
            fallback = " / ".join(labels) if classification != "tags" else ""
            route_text = route_node.label if route_node else "normal"
            return RuleDecision(
                destination, node.node_id, paths, False,
                f"Route {route_text}; {' / '.join(labels)} est placé avant {loser}.", labels, paths,
                route=route_text, fallback=fallback, classification=classification,
                has_tag_match=has_tag_match,
            )
        return RuleDecision(None, reason="Aucune règle applicable",
            route=route_node.label if route_node else "normal")


def status_for(source: Path, target: Path, mode: OrganizeMode) -> PlanStatus:
    renamed = source.name != target.name
    moved = source.parent != target.parent and mode is OrganizeMode.ORGANIZE
    if renamed and moved:
        return PlanStatus.RENAME_MOVE
    if renamed:
        return PlanStatus.RENAME
    if moved:
        return PlanStatus.MOVE
    return PlanStatus.UNCHANGED
