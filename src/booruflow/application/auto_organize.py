"""Scan, plan and safely apply automatic organization operations."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterable
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
        if not plan.site:
            plan.status, plan.message = PlanStatus.AMBIGUOUS, "Site absent ou ambigu dans le chemin"
            self._report_error(plan, "site_identification", "AmbiguousSite", plan.message)
            return plan
        stage = "cache_read"
        try:
            cancelled()
            metadata = (
                None
                if force_refresh or not use_cache
                else self.cache.get(plan.site, plan.post_id, cache_days)
            )
            cancelled()
            if metadata is None:
                stage = f"{plan.site}_api"
                plan.api_calls = 1
                cancelled()
                metadata = self.fetcher(plan.site, plan.post_id)
                cancelled()
                stage = "cache_write"
                self.cache.put(metadata)
                cancelled()
                plan.fetch_state = "api"
            else:
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
                plan.status, plan.message = PlanStatus.AMBIGUOUS, f"Collision de lot: {target}"
        elif target.exists() and target != entries[0].source:
            entries[0].status, entries[0].message = (
                PlanStatus.AMBIGUOUS,
                f"Cible existante: {target}",
            )


def apply_plans(plans: Iterable[FilePlan]) -> dict[str, int]:
    """Apply independent validated operations; never overwrite and continue after failures."""
    result = {"applied": 0, "unchanged": 0, "failed": 0, "skipped": 0}
    for plan in plans:
        if plan.status is PlanStatus.UNCHANGED:
            result["unchanged"] += 1
            continue
        if (
            plan.status not in {PlanStatus.RENAME, PlanStatus.MOVE, PlanStatus.RENAME_MOVE}
            or not plan.destination
        ):
            result["skipped"] += 1
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
        except Exception as exc:  # noqa: BLE001 - batch must continue
            plan.status, plan.message = PlanStatus.ERROR, str(exc)
            result["failed"] += 1
    return result


def validation_summary(plans: Iterable[FilePlan]) -> dict[str, int]:
    plans = list(plans)
    exact = sum(p.status is PlanStatus.UNCHANGED for p in plans)
    ambiguous = sum(p.status is PlanStatus.AMBIGUOUS for p in plans)
    errors = sum(
        p.status in {PlanStatus.ERROR, PlanStatus.NOT_FOUND, PlanStatus.UNRECOGNIZED} for p in plans
    )
    summary = {
        "analyzed": len(plans),
        "exact": exact,
        "divergences": len(plans) - exact - ambiguous - errors,
        "ambiguous": ambiguous,
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
    """Return terminal counts by visible Tags branch and site."""
    roots = tuple(rules)
    tags_root = next((node for node in roots if node.node_id == "tags"), None)

    def terminals(node: RuleNode) -> list[RuleNode]:
        result = [node] if node.kind in {"rule", "dynamic"} else []
        for child in node.children:
            result.extend(terminals(child))
        return result

    branches = (
        {child.label: len(terminals(child)) for child in tags_root.children} if tags_root else {}
    )
    tag_leaves = (
        [leaf for child in tags_root.children for leaf in terminals(child)] if tags_root else []
    )
    return {
        "branches": branches,
        "tags_total": len(tag_leaves),
        "gelbooru": sum("gelbooru" in leaf.sites for leaf in tag_leaves),
        "e621": sum("e621" in leaf.sites for leaf in tag_leaves),
        "shared": sum(set(leaf.sites) == {"gelbooru", "e621"} for leaf in tag_leaves),
    }
