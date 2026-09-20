"""Scan, plan and safely apply automatic organization operations."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path

from booruflow.domain.auto_organize import (
    FilePlan,
    OrganizeMode,
    PlanStatus,
    PostMetadata,
    RuleEngine,
    RuleNode,
    canonical_filename,
    status_for,
)
from booruflow.domain.image_analysis import collection_site_from_path, parse_booru_filename
from booruflow.infrastructure.post_metadata_client import MetadataFetchError, PostNotFoundError

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})
DEFAULT_METADATA_CACHE_DAYS = 30
SCAN_PROGRESS_INTERVAL = 100
SYSTEMIC_API_ERROR_THRESHOLD = 10
SUPPORTED_SITES = ("gelbooru", "e621")
_MODEL_SITE_ROOT = re.compile(
    r"^(?P<family>.+?)\s+\((?P<site>gelbooru|e621)\)(?P<young>\s+y)?$",
    re.IGNORECASE,
)


def _normalized_model_key(value: str | Path) -> str:
    return "/".join(
        part.casefold().strip()
        for part in Path(str(value).replace("\\", "/")).parts
        if part not in {"", "."}
    )


def _model_node_id(relative: Path) -> str:
    value = relative.as_posix().casefold()
    normalized = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    digest = hashlib.sha1(value.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]
    return f"model_{normalized or 'root'}_{digest}"


def scan_classification_model(
    model_root: Path,
    base_rules: Iterable[RuleNode],
    saved_rules: Iterable[RuleNode] = (),
) -> tuple[RuleNode, ...]:
    """Build effective rules from an existing directory tree without changing it.

    The filesystem owns the shape and relative destinations. Existing rules own
    semantics (rule/router/dynamic), activation, and priority. Unknown directories
    remain organizational branches and are never guessed to be tags.
    """
    root = model_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Le dossier modèle n'est pas un dossier : {root}")
    canonical = tuple(base_rules)
    tags = next((node for node in canonical if node.node_id == "tags"), None)

    exact: dict[str, list[RuleNode]] = {}
    terminal: dict[str, list[RuleNode]] = {}
    rank: dict[str, int] = {}
    counter = 0

    def register(node: RuleNode, labels: tuple[str, ...]) -> None:
        nonlocal counter
        rank.setdefault(node.node_id, counter)
        counter += 1
        if node.node_id not in {"tags", "dedicated"}:
            label_path = _normalized_model_key(Path(*labels)) if labels else ""
            if label_path:
                exact.setdefault(label_path, []).append(node)
            if node.destination:
                destination = node.destination.replace("{value}", "").rstrip("/\\")
                key = _normalized_model_key(destination)
                if key:
                    exact.setdefault(key, []).append(node)
                    parts = key.split("/")
                    if parts and parts[0] == "tags" and len(parts) > 1:
                        exact.setdefault("/".join(parts[1:]), []).append(node)
            if node.kind == "rule":
                for name in (node.label, *node.tags):
                    terminal.setdefault(name.casefold(), []).append(node)
        for child in node.children:
            register(child, labels + (child.label,))

    if tags is not None:
        rank[tags.node_id] = counter
        counter += 1
        for child in tags.children:
            register(child, (child.label,))
    fallback_nodes = tuple(node for node in canonical if node.kind == "dynamic")
    for node in fallback_nodes:
        register(node, (node.label,))

    def unique(values: Iterable[RuleNode]) -> RuleNode | None:
        by_id = {node.node_id: node for node in values}
        return next(iter(by_id.values())) if len(by_id) == 1 else None

    def semantic(relative: Path) -> RuleNode | None:
        match = unique(exact.get(_normalized_model_key(relative), ()))
        if match is not None:
            return match
        return unique(terminal.get(relative.name.casefold(), ()))

    def scan(
        parent: Path,
        relative_parent: Path = Path(),
        semantic_parent: Path = Path(),
        *,
        allow_semantics: bool = True,
    ) -> tuple[RuleNode, ...]:
        discovered: list[tuple[int, str, RuleNode]] = []
        directories = sorted(
            (
                path
                for path in parent.iterdir()
                if path.is_dir()
                and not path.is_symlink()
                and not getattr(path, "is_junction", lambda: False)()
            ),
            key=lambda path: path.name.casefold(),
        )
        for path in directories:
            relative = relative_parent / path.name
            semantic_relative = semantic_parent / path.name
            original = semantic(semantic_relative) if allow_semantics else None
            children = scan(
                path,
                relative,
                semantic_relative,
                allow_semantics=allow_semantics,
            )
            node_id = _model_node_id(relative)
            if original is None:
                node = RuleNode(
                    node_id,
                    path.name,
                    "branch",
                    children=children,
                )
                priority = 1_000_000
            else:
                destination = ""
                if original.kind == "rule":
                    destination = relative.as_posix()
                elif original.kind == "dynamic":
                    destination = relative.as_posix()
                    if original.special != "copyright_character":
                        destination += "/{value}"
                node = RuleNode(
                    node_id,
                    path.name,
                    original.kind,
                    destination,
                    original.tags,
                    original.sites,
                    original.active,
                    original.source,
                    original.special,
                    original.ordered,
                    children,
                    original.node_id,
                )
                priority = rank.get(original.node_id, 1_000_000)
            discovered.append((priority, path.name.casefold(), node))
        return tuple(node for _priority, _name, node in sorted(discovered, key=lambda item: item[:2]))

    root_paths = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir()
            and not path.is_symlink()
            and not getattr(path, "is_junction", lambda: False)()
        ),
        key=lambda path: path.name.casefold(),
    )
    root_names = {path.name.casefold() for path in root_paths}
    dynamic_sources = {
        "artist": "artists",
        "artists": "artists",
        "copyright": "copyrights",
        "species": "species",
        "espèces": "species",
        "especes": "species",
    }
    discovered: list[tuple[int, str, RuleNode]] = []
    for path in root_paths:
        relative = Path(path.name)
        root_match = _MODEL_SITE_ROOT.fullmatch(path.name)
        family = root_match.group("family").strip() if root_match else ""
        site = root_match.group("site").casefold() if root_match else ""
        young_variant = bool(root_match and root_match.group("young"))
        source = dynamic_sources.get(family.casefold())
        if source:
            children = scan(path, relative, allow_semantics=False)
            special = "copyright_character" if source == "copyrights" else ""
            destination = relative.as_posix()
            if not special:
                destination += "/{value}"
            semantic_id = next(
                (node.node_id for node in fallback_nodes if node.source == source),
                source,
            )
            node = RuleNode(
                _model_node_id(relative),
                path.name,
                "dynamic",
                destination,
                sites=(site,) if site else SUPPORTED_SITES,
                source=source,
                special=special,
                children=children,
                semantic_id=semantic_id,
            )
            priority = rank.get(semantic_id, 1_000_000)
        elif root_match:
            normal_name = f"{family} ({root_match.group('site')})".casefold()
            paired = f"{normal_name} y" in root_names
            special = "young_root" if young_variant else "normal_root" if paired else "site_root"
            children = scan(path, relative, allow_semantics=True)
            semantic_id = "tags" if family.casefold() == "tags" else f"root:{family.casefold()}:{site}"
            node = RuleNode(
                _model_node_id(relative),
                path.name,
                "route",
                relative.as_posix(),
                sites=(site,),
                special=special,
                children=children,
                semantic_id=semantic_id,
            )
            priority = rank.get(semantic_id, 1_000_000)
        else:
            children = scan(path, relative, relative, allow_semantics=True)
            original = semantic(relative)
            if original is None:
                node = RuleNode(
                    _model_node_id(relative),
                    path.name,
                    "branch",
                    children=children,
                )
                priority = 1_000_000
            else:
                destination = relative.as_posix() if original.kind == "rule" else ""
                if original.kind == "dynamic":
                    destination = relative.as_posix()
                    if original.special != "copyright_character":
                        destination += "/{value}"
                node = RuleNode(
                    _model_node_id(relative),
                    path.name,
                    original.kind,
                    destination,
                    original.tags,
                    original.sites,
                    original.active,
                    original.source,
                    original.special,
                    original.ordered,
                    children,
                    original.node_id,
                )
                priority = rank.get(original.node_id, 1_000_000)
        discovered.append((priority, path.name.casefold(), node))

    scanned = tuple(
        node for _priority, _label, node in sorted(discovered, key=lambda item: item[:2])
    )
    saved_exact: dict[str, tuple[int, RuleNode]] = {}
    saved_semantic: dict[str, tuple[int, RuleNode]] = {}

    def index_saved(nodes: Iterable[RuleNode], counter: list[int]) -> None:
        for node in nodes:
            position = counter[0]
            counter[0] += 1
            saved_exact.setdefault(node.node_id, (position, node))
            semantic_id = node.semantic_id or (
                node.node_id if not node.node_id.startswith("model_") else ""
            )
            if semantic_id:
                saved_semantic.setdefault(semantic_id, (position, node))
            index_saved(node.children, counter)

    index_saved(tuple(saved_rules), [0])

    def apply_preferences(nodes: tuple[RuleNode, ...]) -> tuple[RuleNode, ...]:
        preferred: list[tuple[int, int, RuleNode]] = []
        for default_position, node in enumerate(nodes):
            match = saved_exact.get(node.node_id)
            if match is None and node.semantic_id:
                match = saved_semantic.get(node.semantic_id)
            children = apply_preferences(node.children)
            active = match[1].active if match is not None else node.active
            updated = replace(node, active=active, children=children)
            preferred.append(
                (
                    match[0] if match is not None else 1_000_000 + default_position,
                    default_position,
                    updated,
                )
            )
        return tuple(node for _rank, _default, node in sorted(preferred, key=lambda item: item[:2]))

    return apply_preferences(scanned)


class AnalysisCancelled(RuntimeError):
    """Carry the plans completed before a cooperative cancellation."""

    def __init__(self, plans: list[FilePlan]) -> None:
        super().__init__("Analyse annulée")
        self.plans = plans


class SystemicApiError(RuntimeError):
    """Stop a batch after repeated identical infrastructure failures."""

    def __init__(self, plans: list[FilePlan], signature: str) -> None:
        super().__init__("Analyse interrompue : erreur API systématique")
        self.plans, self.signature = plans, signature


def rule_node_from_dict(value: dict) -> RuleNode:
    """Build a rule node while preserving the stable JSON field names."""
    return RuleNode(
        str(value["id"]),
        str(value.get("label", value["id"])),
        str(value.get("kind", "branch")),
        str(value.get("destination", "")),
        tuple(value.get("tags", ())),
        tuple(value.get("sites", ("gelbooru", "e621"))),
        bool(value.get("active", True)),
        str(value.get("source", "")),
        str(value.get("special", "")),
        bool(value.get("ordered", True)),
        tuple(rule_node_from_dict(child) for child in value.get("children", ())),
        str(value.get("semantic_id", "")),
    )


def rule_node_to_dict(node: RuleNode) -> dict[str, object]:
    """Serialize a rule node using the compatibility-sensitive JSON schema."""
    result: dict[str, object] = {"id": node.node_id, "label": node.label, "kind": node.kind}
    if node.destination:
        result["destination"] = node.destination
    if node.tags:
        result["tags"] = list(node.tags)
    if node.sites != ("gelbooru", "e621"):
        result["sites"] = list(node.sites)
    if not node.active:
        result["active"] = False
    if node.source:
        result["source"] = node.source
    if node.special:
        result["special"] = node.special
    if not node.ordered:
        result["ordered"] = False
    if node.semantic_id:
        result["semantic_id"] = node.semantic_id
    if node.children or node.kind == "branch":
        result["children"] = [rule_node_to_dict(child) for child in node.children]
    return result


def load_rules(default_path: Path, override_path: Path | None = None) -> tuple[RuleNode, ...]:
    """Load canonical rules and merge the limited fields editable by users."""
    data = json.loads(default_path.read_text(encoding="utf-8"))
    if override_path and override_path.is_file():
        try:
            override = json.loads(override_path.read_text(encoding="utf-8-sig"))
            if isinstance(override, dict) and isinstance(override.get("roots"), list):

                def merge_nodes(defaults: list[dict], changes: list[dict]) -> list[dict]:
                    remaining = {str(node.get("id")): node for node in defaults}
                    merged = []
                    for change in changes:
                        node_id = str(change.get("id", ""))
                        canonical_id = (
                            "general"
                            if node_id == "other_tags" and "general" in remaining
                            else node_id
                        )
                        original = remaining.pop(canonical_id, None)
                        if original is None:
                            if node_id != "demon_girl_direct":
                                merged.append(change)
                            continue
                        # The editor only changes sibling order and the active flag.  Keeping
                        # canonical structure here lets old overrides inherit new leaves and
                        # migrations such as rule -> route without silently reverting them.
                        value = {**original}
                        if "active" in change:
                            value["active"] = bool(change["active"])
                        value["children"] = merge_nodes(
                            list(original.get("children", ())), list(change.get("children", ()))
                        )
                        merged.append(value)
                    merged.extend(remaining.values())
                    return merged

                data = {
                    **data,
                    "roots": merge_nodes(list(data.get("roots", ())), list(override["roots"])),
                }
        except (OSError, ValueError, TypeError):
            pass
    return tuple(rule_node_from_dict(value) for value in data.get("roots", ()))


def iter_images(roots: Iterable[Path], recursive: bool) -> Iterable[Path]:
    for root in roots:
        iterator = root.rglob("*") if recursive else root.iterdir()
        yield from (
            path for path in iterator if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
        )


class AutoOrganizer:
    """Plan safe file operations from cached or freshly fetched post metadata."""

    def __init__(
        self,
        cache,
        fetcher: Callable[[str, str], PostMetadata],
        rules: RuleEngine,
        destination_root: Path,
        error_reporter: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.cache, self.fetcher, self.rules, self.destination_root = (
            cache,
            fetcher,
            rules,
            destination_root,
        )
        self.error_reporter = error_reporter

    def _report_error(
        self,
        plan: FilePlan,
        stage: str,
        exception_type: str,
        message: str,
        *,
        status=None,
        endpoint="",
        attempt=1,
        signature="",
        infrastructure=False,
    ) -> None:
        plan.error_signature = signature or f"{stage}|{exception_type}|{message}"
        plan.infrastructure_error = infrastructure
        if self.error_reporter:
            self.error_reporter(
                {
                    "file": str(plan.source),
                    "site": plan.site,
                    "post_id": plan.post_id,
                    "stage": stage,
                    "exception_type": exception_type,
                    "message": message,
                    "status": status,
                    "endpoint": endpoint,
                    "attempt": attempt,
                    "signature": plan.error_signature,
                }
            )

    def plan_file(
        self,
        source: Path,
        mode: OrganizeMode,
        *,
        use_cache: bool = True,
        force_refresh: bool = False,
        cache_days: int = DEFAULT_METADATA_CACHE_DAYS,
        cancel_check: Callable[[], bool] | None = None,
    ) -> FilePlan:
        def cancelled() -> None:
            if cancel_check and cancel_check():
                raise AnalysisCancelled([])

        cancelled()
        plan = FilePlan(source=source, mode=mode)
        parsed = parse_booru_filename(source)
        if parsed is None:
            plan.status, plan.message = PlanStatus.UNRECOGNIZED, "Nom standard non reconnu"
            self._report_error(plan, "parse", "UnrecognizedFilename", plan.message)
            return plan
        plan.post_id, plan.current_artist = parsed.post_id, parsed.artist
        plan.site = collection_site_from_path(source) or ""
        stage = "cache_read"
        try:
            cancelled()
            metadata = None
            if not plan.site and use_cache and not force_refresh:
                finder = getattr(self.cache, "find_candidates", None)
                candidates = (
                    tuple(finder(plan.post_id, parsed.source_md5, cache_days))
                    if finder is not None
                    else ()
                )
                if len(candidates) > 1:
                    plan.status = PlanStatus.AMBIGUOUS
                    plan.message = "Plusieurs sites correspondent dans le cache"
                    self._report_error(
                        plan, "site_identification", "AmbiguousSite", plan.message
                    )
                    return plan
                if candidates:
                    metadata = candidates[0]
                    plan.site = metadata.site
                    plan.fetch_state = "cache"
                    plan.cache_hit = True
            elif plan.site and use_cache and not force_refresh:
                metadata = self.cache.get(plan.site, plan.post_id, cache_days)
            cancelled()
            if metadata is None:
                sites = (plan.site,) if plan.site else SUPPORTED_SITES
                remote_matches: list[PostMetadata] = []
                failures: list[MetadataFetchError] = []
                for site in sites:
                    stage = f"{site}_api"
                    plan.api_calls += 1
                    cancelled()
                    try:
                        candidate = self.fetcher(site, plan.post_id)
                    except PostNotFoundError:
                        continue
                    except MetadataFetchError as exc:
                        failures.append(exc)
                        continue
                    cancelled()
                    stage = "cache_write"
                    self.cache.put(candidate)
                    if plan.site or (
                        candidate.md5
                        and candidate.md5.casefold() == parsed.source_md5.casefold()
                    ):
                        remote_matches.append(candidate)
                if len(remote_matches) > 1:
                    plan.status, plan.fetch_state, plan.message = (
                        PlanStatus.AMBIGUOUS,
                        "api",
                        "Plusieurs sites distants correspondent au fichier",
                    )
                    self._report_error(
                        plan, "site_identification", "AmbiguousSite", plan.message
                    )
                    return plan
                if not remote_matches:
                    if failures:
                        raise failures[0]
                    if plan.site:
                        plan.status, plan.fetch_state, plan.message = (
                            PlanStatus.NOT_FOUND,
                            "not_found",
                            "Post introuvable",
                        )
                    else:
                        plan.status, plan.fetch_state, plan.message = (
                            PlanStatus.UNRESOLVED,
                            "not_found",
                            "Site/post non résolu par le cache ou les API",
                        )
                    self._report_error(
                        plan, "site_identification", "UnresolvedSite", plan.message
                    )
                    return plan
                metadata = remote_matches[0]
                plan.site = metadata.site
                plan.fetch_state = "api"
            elif not plan.fetch_state:
                plan.fetch_state = "cache"
                plan.cache_hit = True
        except AnalysisCancelled:
            raise
        except PostNotFoundError as exc:
            plan.status, plan.fetch_state, plan.message = (
                PlanStatus.NOT_FOUND,
                "not_found",
                "Post introuvable",
            )
            self._report_error(
                plan,
                "remote_fetch",
                "PostNotFoundError",
                plan.message,
                status=404,
                endpoint=getattr(exc, "endpoint", ""),
                attempt=1,
            )
            return plan
        except MetadataFetchError as exc:
            failure = exc.failure
            plan.status, plan.fetch_state, plan.message = PlanStatus.ERROR, "error", failure.message
            self._report_error(
                plan,
                failure.stage,
                failure.exception_type,
                failure.message,
                status=failure.status,
                endpoint=failure.endpoint,
                attempt=failure.attempt,
                signature=failure.signature,
                infrastructure=True,
            )
            return plan
        except Exception as exc:  # noqa: BLE001 - per-file isolation boundary
            plan.status, plan.fetch_state, plan.message = PlanStatus.ERROR, "error", str(exc)
            self._report_error(plan, stage, type(exc).__name__, str(exc), infrastructure=True)
            return plan
        plan.remote_artist = " & ".join(metadata.artists) or parsed.artist
        cancelled()
        plan.future_name = canonical_filename(parsed, metadata, source.suffix)
        if metadata.md5 and metadata.md5.casefold() != parsed.source_md5:
            plan.status, plan.message = (
                PlanStatus.AMBIGUOUS,
                "MD5 distant différent; aucune opération autorisée",
            )
            return plan
        decision = self.rules.decide(metadata)
        cancelled()
        plan.winner, plan.candidates, plan.winner_path = (
            decision.winner,
            decision.candidates,
            decision.winner_path,
        )
        plan.route, plan.fallback = decision.route, decision.fallback
        plan.classification, plan.has_tag_match = decision.classification, decision.has_tag_match
        if decision.ambiguous:
            plan.status, plan.message = PlanStatus.AMBIGUOUS, decision.reason
            return plan
        if mode is OrganizeMode.ORGANIZE and not decision.destination:
            plan.status, plan.message = PlanStatus.UNRESOLVED, decision.reason
            return plan
        destination_dir = (
            source.parent
            if mode is OrganizeMode.REFRESH_ONLY
            else (
                self.destination_root / decision.destination
                if decision.destination
                else source.parent
            )
        )
        plan.destination = destination_dir / plan.future_name
        plan.destination_relative = (
            Path(decision.destination) / plan.future_name
            if mode is OrganizeMode.ORGANIZE and decision.destination
            else Path(plan.future_name)
        )
        plan.status = status_for(source, plan.destination, mode)
        plan.message = decision.reason
        stat = source.stat()
        plan.source_size, plan.source_mtime_ns = stat.st_size, stat.st_mtime_ns
        return plan

    def plan(
        self,
        roots: Iterable[Path],
        mode: OrganizeMode,
        recursive: bool,
        *,
        cancel_check: Callable[[], bool] | None = None,
        progress: Callable[[dict[str, int | str]], None] | None = None,
        **options,
    ) -> list[FilePlan]:
        paths: list[Path] = []
        for root in roots:
            iterator = root.rglob("*") if recursive else root.iterdir()
            for path in iterator:
                if cancel_check and cancel_check():
                    raise AnalysisCancelled([])
                if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES:
                    paths.append(path)
                if progress and (len(paths) == 1 or len(paths) % SCAN_PROGRESS_INTERVAL == 0):
                    progress(
                        {
                            "phase": "scan",
                            "scanned": len(paths),
                            "total": 0,
                            "processed": 0,
                            "cache_hits": 0,
                            "api_calls": 0,
                            "ambiguities": 0,
                            "errors": 0,
                            "tag_matches": 0,
                            "classified_tags": 0,
                            "classified_species": 0,
                            "classified_copyright": 0,
                            "classified_artist": 0,
                            "routed_cl": 0,
                            "routed_yl": 0,
                        }
                    )
        totals = {
            "phase": "analyze",
            "scanned": len(paths),
            "total": len(paths),
            "processed": 0,
            "cache_hits": 0,
            "api_calls": 0,
            "ambiguities": 0,
            "errors": 0,
            "tag_matches": 0,
            "classified_tags": 0,
            "classified_species": 0,
            "classified_copyright": 0,
            "classified_artist": 0,
            "routed_cl": 0,
            "routed_yl": 0,
        }
        if progress:
            progress(dict(totals))
        plans: list[FilePlan] = []
        consecutive_signature = ""
        consecutive_count = 0
        for index, path in enumerate(paths, start=1):
            if cancel_check and cancel_check():
                raise AnalysisCancelled(plans)
            try:
                plan = self.plan_file(path, mode, cancel_check=cancel_check, **options)
            except AnalysisCancelled as exc:
                raise AnalysisCancelled(plans + exc.plans) from exc
            plans.append(plan)
            totals["processed"] = index
            totals["cache_hits"] += int(plan.cache_hit)
            totals["api_calls"] += plan.api_calls
            totals["ambiguities"] += int(plan.status is PlanStatus.AMBIGUOUS)
            totals["errors"] += int(
                plan.status in {PlanStatus.ERROR, PlanStatus.NOT_FOUND, PlanStatus.UNRECOGNIZED}
            )
            totals["tag_matches"] += int(plan.has_tag_match)
            classification_key = f"classified_{plan.classification}"
            if classification_key in totals:
                totals[classification_key] += 1
            route_key = {"Tags C&L": "routed_cl", "Tags Y&L": "routed_yl"}.get(plan.route)
            if route_key:
                totals[route_key] += 1
            if plan.infrastructure_error:
                consecutive_count = (
                    consecutive_count + 1 if plan.error_signature == consecutive_signature else 1
                )
                consecutive_signature = plan.error_signature
            else:
                consecutive_signature = ""
                consecutive_count = 0
            if plan.status in {PlanStatus.ERROR, PlanStatus.NOT_FOUND, PlanStatus.UNRECOGNIZED}:
                totals["last_error"] = (
                    f"{plan.site or '?'} post {plan.post_id or '?'} — {plan.message}"
                )
            if progress:
                progress(dict(totals))
            if consecutive_count >= SYSTEMIC_API_ERROR_THRESHOLD:
                raise SystemicApiError(plans, consecutive_signature)
        return plans


def validate_batch(plans: list[FilePlan]) -> None:
    targets: dict[Path, list[FilePlan]] = {}
    for plan in plans:
        if plan.destination and plan.status in {
            PlanStatus.RENAME,
            PlanStatus.MOVE,
            PlanStatus.RENAME_MOVE,
        }:
            targets.setdefault(plan.destination, []).append(plan)
    for target, entries in targets.items():
        if len(entries) > 1:
            for plan in entries:
                plan.status, plan.message = (
                    PlanStatus.DESTINATION_CONFLICT,
                    f"Collision de lot: {target}",
                )
        elif target.exists() and target != entries[0].source:
            entries[0].status, entries[0].message = (
                PlanStatus.DESTINATION_CONFLICT,
                f"Cible existante: {target}",
            )


def apply_plans(
    plans: Iterable[FilePlan],
    operation_reporter: Callable[[dict[str, str]], None] | None = None,
) -> dict[str, int]:
    """Apply independent validated operations; never overwrite and continue after failures."""
    result = {"applied": 0, "unchanged": 0, "failed": 0, "skipped": 0}
    for plan in plans:
        if plan.status is PlanStatus.UNCHANGED:
            result["unchanged"] += 1
            if operation_reporter:
                operation_reporter(
                    {"state": "unchanged", "source": str(plan.source), "destination": str(plan.destination or "")}
                )
            continue
        if (
            plan.status not in {PlanStatus.RENAME, PlanStatus.MOVE, PlanStatus.RENAME_MOVE}
            or not plan.destination
        ):
            result["skipped"] += 1
            if operation_reporter:
                operation_reporter(
                    {"state": "skipped", "source": str(plan.source), "destination": str(plan.destination or "")}
                )
            continue
        try:
            if (
                plan.mode is OrganizeMode.REFRESH_ONLY
                and plan.destination.parent != plan.source.parent
            ):
                raise RuntimeError("Le mode Actualiser uniquement interdit tout déplacement")
            stat = plan.source.stat()
            if stat.st_size != plan.source_size or stat.st_mtime_ns != plan.source_mtime_ns:
                raise RuntimeError("Le fichier a changé depuis l'analyse")
            if plan.destination.exists():
                raise FileExistsError(plan.destination)
            plan.destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(plan.source), str(plan.destination))
            result["applied"] += 1
            if operation_reporter:
                operation_reporter(
                    {"state": "applied", "source": str(plan.source), "destination": str(plan.destination)}
                )
        except Exception as exc:  # noqa: BLE001 - batch must continue
            plan.status, plan.message = PlanStatus.ERROR, str(exc)
            result["failed"] += 1
            if operation_reporter:
                operation_reporter(
                    {"state": "failed", "source": str(plan.source), "destination": str(plan.destination or ""), "message": str(exc)}
                )
    return result


def validation_summary(plans: Iterable[FilePlan]) -> dict[str, int]:
    plans = list(plans)
    exact = sum(p.status is PlanStatus.UNCHANGED for p in plans)
    ambiguous = sum(p.status is PlanStatus.AMBIGUOUS for p in plans)
    unresolved = sum(p.status is PlanStatus.UNRESOLVED for p in plans)
    conflicts = sum(p.status is PlanStatus.DESTINATION_CONFLICT for p in plans)
    errors = sum(
        p.status in {PlanStatus.ERROR, PlanStatus.NOT_FOUND, PlanStatus.UNRECOGNIZED} for p in plans
    )
    summary = {
        "analyzed": len(plans),
        "exact": exact,
        "divergences": len(plans) - exact - ambiguous - unresolved - conflicts - errors,
        "ambiguous": ambiguous,
        "unresolved": unresolved,
        "conflicts": conflicts,
        "errors": errors,
        "tag_matches": sum(p.has_tag_match for p in plans),
        "by_tags": sum(p.classification == "tags" for p in plans),
        "by_species": sum(p.classification == "species" for p in plans),
        "by_copyright": sum(p.classification == "copyright" for p in plans),
        "by_artist": sum(p.classification == "artist" for p in plans),
        "routed_cl": sum(p.route == "Tags C&L" for p in plans),
        "routed_yl": sum(p.route == "Tags Y&L" for p in plans),
    }
    return summary


def rule_inventory(rules: Iterable[RuleNode]) -> dict[str, object]:
    """Return terminal counts for either canonical or filesystem-backed trees."""
    roots = tuple(rules)
    tags_root = next((node for node in roots if node.node_id == "tags"), None)

    def terminals(node: RuleNode) -> list[RuleNode]:
        result = [node] if node.kind in {"rule", "dynamic"} else []
        for child in node.children:
            result.extend(terminals(child))
        return result

    visible_roots = tags_root.children if tags_root else roots
    branches = {child.label: len(terminals(child)) for child in visible_roots}
    tag_leaves = [
        leaf
        for child in visible_roots
        for leaf in terminals(child)
        if leaf.kind == "rule"
    ]
    return {
        "branches": branches,
        "tags_total": len(tag_leaves),
        "gelbooru": sum("gelbooru" in leaf.sites for leaf in tag_leaves),
        "e621": sum("e621" in leaf.sites for leaf in tag_leaves),
        "shared": sum(set(leaf.sites) == {"gelbooru", "e621"} for leaf in tag_leaves),
    }
