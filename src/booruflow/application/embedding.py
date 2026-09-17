"""Stable application port for optional visual embedding providers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from booruflow.domain.similar_artists import EmbeddingSpace


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    space: EmbeddingSpace
    vector: tuple[float, ...]


class EmbeddingBackend(Protocol):
    space: EmbeddingSpace

    def prepare(self) -> None: ...
    def encode(self, image: Path) -> EmbeddingResult: ...
    def close(self) -> None: ...


class EmbeddingIndexService:
    """Controlled, cache-aware indexing on canonical AnalysisItems."""

    def __init__(self, repository, logger=None) -> None:
        self.repository = repository
        self.log = logger or (lambda _message: None)

    def eligible_item_ids(self, artist=None) -> list[int]:
        if artist is None:
            return self.repository.embeddable_item_ids()
        return [int(row["item_id"]) for row in self.repository.artist_profile_inputs(
            artist.site, artist.tag
        )]

    def missing_item_ids(self, backend: EmbeddingBackend, item_ids) -> list[int]:
        result = []
        for item_id in dict.fromkeys(int(value) for value in item_ids):
            row = self.repository.embedding_for_identity(
                item_id, backend.space.backend, backend.space.model_name,
                backend.space.model_version, backend.space.configuration_hash,
            )
            if row is None:
                result.append(item_id)
        return result

    def encode_missing(self, backend: EmbeddingBackend, item_ids, progress_callback=None) -> dict[str, float | int]:
        import time
        from array import array

        requested = list(dict.fromkeys(int(value) for value in item_ids))
        missing = self.missing_item_ids(backend, requested)
        computed = skipped = failed = 0; started = time.perf_counter()
        try:
            if missing: backend.prepare()
            for item_id in missing:
                item = self.repository.get_item(item_id)
                if item is None or item.cached_path is None or not item.cached_path.is_file():
                    skipped += 1
                    if progress_callback:progress_callback(computed+skipped+failed,len(missing))
                    continue
                run_id = self.repository.begin_model_run(
                    item_id, backend.space.backend, backend.space.model_name,
                    backend.space.model_version, backend.space.configuration_hash,
                    backend.space.runtime, backend.space.device,
                )
                if run_id is None:
                    skipped += 1; self.log(f"embedding cache hit: item {item_id}"); continue
                try:
                    result = backend.encode(item.cached_path)
                    values = array("f", result.vector)
                    self.repository.save_embedding(
                        item_id, run_id, values.tobytes(), len(values), "float32", True
                    )
                    computed += 1; self.log(f"embedding computed: item {item_id}")
                except Exception as exc:  # noqa: BLE001 - isolate one corrupt item
                    failed += 1; self.repository.fail_model_run(run_id, str(exc))
                    self.log(f"embedding failed: item {item_id}: {exc}")
                if progress_callback:progress_callback(computed+skipped+failed,len(missing))
        finally:
            backend.close()
        elapsed = time.perf_counter() - started
        return {"images_eligible": len(requested), "embeddings_missing": len(missing),
                "embeddings_computed": computed, "images_skipped": skipped,
                "images_failed": failed, "seconds": elapsed,
                "seconds_per_image": elapsed / computed if computed else 0.0}
