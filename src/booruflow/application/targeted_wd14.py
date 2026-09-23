"""Targeted WD14 confidence analysis for the Tagging results grid."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from booruflow.application.tag_canonicalization import canonicalize_new_gelbooru_tag
from booruflow.application.tagging import canonical_tag_value
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import (
    GelbooruPostProvider,
    ImageSourceService,
)
from booruflow.infrastructure.tag_category_lookup import LocalTagCategoryLookup
from booruflow.infrastructure.wd14 import (
    WD14Backend,
    WD14Config,
    load_selected_tags,
    wd14_config_identity,
)

CONFIDENCE_BUCKETS = (
    "90-100", "80-89", "70-79", "60-69", "50-59",
    "40-49", "30-39", "20-29", "10-19", "under-10",
    "not-analyzed", "failed",
)


def confidence_bucket(score: float | None, *, failed: bool = False) -> str:
    if failed:
        return "failed"
    if score is None:
        return "not-analyzed"
    value = min(1.0, max(0.0, float(score)))
    if value >= 0.9:
        return "90-100"
    if value < 0.1:
        return "under-10"
    lower = math.floor((value + 1e-12) * 10) * 10
    return f"{lower}-{lower + 9}"


def resolve_wd14_target(
    target: str, model_directory: Path, alias_database: Path | None = None
) -> tuple[str, ...]:
    normalized = canonical_tag_value(target).replace(" ", "_")
    canonical = canonicalize_new_gelbooru_tag(
        normalized, alias_database
    ).canonical_name
    vocabulary = {
        name.replace(" ", "_").casefold(): name
        for name, _category in load_selected_tags(model_directory / "selected_tags.csv")
    }
    matches = []
    for candidate in (normalized, canonical):
        raw = vocabulary.get(candidate.casefold())
        if raw is not None and raw not in matches:
            matches.append(raw)
    return tuple(matches)


@dataclass(frozen=True, slots=True)
class TargetedWD14Progress:
    total: int
    completed: int
    reused: int
    analyzed: int
    failed: int
    post_id: str = ""


@dataclass(frozen=True, slots=True)
class TargetedWD14Result:
    tag: str
    scores: dict[int, float]
    failed_post_ids: frozenset[int]
    progress: TargetedWD14Progress
    elapsed_seconds: float
    cancelled: bool = False


class TargetedWD14Analyzer:
    """Load WD14 once, reuse compatible vectors, and analyze only missing posts."""

    def __init__(
        self,
        database: Path,
        cache_directory: Path,
        model_directory: Path,
        model_id: str,
        alias_database: Path | None,
        credentials: dict[str, object],
        tag_database: Path | None,
        *,
        backend_factory=WD14Backend,
    ) -> None:
        self.database = database
        self.cache_directory = cache_directory
        self.model_directory = model_directory
        self.model_id = model_id
        self.alias_database = alias_database
        self.credentials = credentials
        self.tag_database = tag_database
        self.backend_factory = backend_factory

    def analyze(
        self,
        tag: str,
        raw_names: tuple[str, ...],
        posts: list[dict],
        progress_callback: Callable[[TargetedWD14Progress], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> TargetedWD14Result:
        started = perf_counter()
        total = len(posts)
        callback = progress_callback or (lambda _progress: None)
        is_cancelled = cancelled or (lambda: False)
        config = WD14Config(self.model_directory, self.model_id, 0.0)
        backend = None
        scores: dict[int, float] = {}
        failed: set[int] = set()
        analyzed = 0
        was_cancelled = False
        try:
            if self.backend_factory is WD14Backend:
                identity = wd14_config_identity(config)
            else:
                backend = self.backend_factory(config); backend.prepare(); identity = backend.identity
            post_ids = [str(post.get("id", "")) for post in posts]
            with ImageAnalysisRepository(self.database) as repository:
                cached = repository.wd14_scores_for_remote_posts(
                    "gelbooru", post_ids, identity.name, identity.version,
                    identity.configuration_hash, raw_names,
                )
                scores.update({int(post_id): score for post_id, score in cached.items()})
                reused = len(scores)
                callback(TargetedWD14Progress(total, reused, reused, 0, 0))
                if reused < total and backend is None:
                    backend = self.backend_factory(config)
                    backend.prepare()
                    identity = backend.identity
                gel = self.credentials.get("gelbooru", {})
                gel = gel if isinstance(gel, dict) else {}
                lookup = LocalTagCategoryLookup(self.tag_database) if self.tag_database else None
                provider = GelbooruPostProvider(
                    str(gel.get("user_id", "")), str(gel.get("api_key", "")),
                    category_lookup=lookup,
                )
                sources = ImageSourceService(repository, self.cache_directory)
                for post in posts:
                    post_id = str(post.get("id", ""))
                    if is_cancelled():
                        was_cancelled = True
                        break
                    if not post_id or int(post_id) in scores:
                        continue
                    run_id = None
                    try:
                        item = repository.item_by_remote_source("gelbooru", post_id)
                        if item is None:
                            item_id = sources.add_post(provider, post_id, request_analysis=False)
                            item = repository.get_item(item_id)
                        elif item.cached_path is None or not item.cached_path.is_file():
                            sources.resolve_post(item.id, provider, post_id)
                            item = repository.item_by_remote_source("gelbooru", post_id)
                        if item is None or item.cached_path is None:
                            raise RuntimeError("original image is unavailable")
                        repository.suppress_analysis_request(item.id)
                        run_id = repository.begin_model_run(
                            item.id, identity.backend, identity.name, identity.version,
                            identity.configuration_hash, backend.runtime, backend.device,
                        )
                        if run_id is None:
                            run_id = repository.model_run_id_for_identity(
                                item.id, identity.backend, identity.name, identity.version,
                                identity.configuration_hash,
                            )
                        if run_id is None:
                            raise RuntimeError("could not create the WD14 model run")
                        result = backend.analyze(item.cached_path)
                        vector = {prediction.raw_name: prediction.score for prediction in result.predictions}
                        repository.save_wd14_score_vector(run_id, vector)
                        repository.complete_model_run(run_id)
                        match = next(
                            (score for name, score in vector.items()
                             if name.casefold() in {value.casefold() for value in raw_names}),
                            None,
                        )
                        if match is None:
                            raise RuntimeError("target tag score is missing from WD14 output")
                        scores[int(post_id)] = float(match)
                        analyzed += 1
                    except Exception as exc:  # noqa: BLE001 - per-image boundary
                        failed.add(int(post_id))
                        if run_id is not None:
                            repository.fail_model_run(run_id, str(exc))
                    completed = reused + analyzed + len(failed)
                    callback(TargetedWD14Progress(
                        total, completed, reused, analyzed, len(failed), post_id,
                    ))
        finally:
            if backend is not None:
                backend.close()
        progress = TargetedWD14Progress(
            total, len(scores) + len(failed), len(scores) - analyzed,
            analyzed, len(failed),
        )
        return TargetedWD14Result(
            tag, scores, frozenset(failed), progress, perf_counter() - started,
            was_cancelled,
        )
