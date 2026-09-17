"""Persistent artist profiles and independent-space similarity diagnostics."""

from __future__ import annotations

import hashlib
import json
import math
from array import array
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from booruflow.domain.image_analysis import detect_local_source
from booruflow.domain.similar_artists import (
    ArtistIdentity,
    ArtistProfile,
    ArtistRanking,
    DispersionMetrics,
    EmbeddingProfile,
    EmbeddingSpace,
    PaletteMetric,
    SimilarityQueryProfile,
)

PROFILE_VERSION = "artist-profile-v1"


def normalize(values) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    norm = math.sqrt(sum(value * value for value in vector))
    if not vector or not math.isfinite(norm) or norm <= 0:
        raise ValueError("embedding is not L2-normalizable")
    result = tuple(value / norm for value in vector)
    if not all(math.isfinite(value) for value in result):
        raise ValueError("embedding contains a non-finite value")
    return result


def cosine(left, right) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions differ")
    return sum(a * b for a, b in zip(normalize(left), normalize(right), strict=True))


def centroid(vectors) -> tuple[tuple[float, ...], DispersionMetrics]:
    normalized = [normalize(vector) for vector in vectors]
    if not normalized:
        raise ValueError("at least one vector is required")
    dimensions = len(normalized[0])
    if any(len(vector) != dimensions for vector in normalized):
        raise ValueError("embedding dimensions differ")
    center = normalize(
        sum(vector[index] for vector in normalized) / len(normalized)
        for index in range(dimensions)
    )
    similarities = [sum(a * b for a, b in zip(vector, center, strict=True))
                    for vector in normalized]
    distances = [1.0 - value for value in similarities]
    mean_distance = sum(distances) / len(distances)
    variance = sum((value - mean_distance) ** 2 for value in distances) / len(distances)
    return center, DispersionMetrics(
        sum(similarities) / len(similarities), variance,
        min(similarities), max(similarities),
    )


def _decode(row) -> tuple[float, ...]:
    dtype = str(row["dtype"])
    typecode = {"float32": "f", "float64": "d"}.get(dtype)
    if typecode is None:
        raise ValueError(f"unsupported embedding dtype: {dtype}")
    values = array(typecode); values.frombytes(bytes(row["vector"]))
    if len(values) != int(row["dimensions"]):
        raise ValueError("corrupt embedding dimensions")
    return normalize(values)


def _profile_to_json(profile: ArtistProfile) -> str:
    return json.dumps(asdict(profile), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _profile_from_json(payload: str) -> ArtistProfile:
    value = json.loads(payload)
    embeddings = {}
    for key, entry in value["embeddings"].items():
        embeddings[key] = EmbeddingProfile(
            EmbeddingSpace(**entry["space"]), tuple(entry["centroid"]),
            DispersionMetrics(**entry["dispersion"]),
        )
    return ArtistProfile(
        ArtistIdentity(**value["artist"]), int(value["image_count"]), embeddings,
        {key: PaletteMetric(**metric) for key, metric in value["palette"].items()},
        dict(value["source_tag_frequency"]), dict(value["accepted_wd14_frequency"]),
        value["profile_version"], value["dependency_hash"], value["built_at"],
    )


class ArtistProfileService:
    """Application API; persistence remains behind repository methods."""

    def __init__(self, repository, *, profile_version: str = PROFILE_VERSION, logger=None) -> None:
        self.repository = repository
        self.profile_version = profile_version
        self.log = logger or (lambda _message: None)

    def _inputs(self, artist: ArtistIdentity):
        items = self.repository.artist_profile_inputs(artist.site, artist.tag)
        embeddings = self.repository.embeddings_for_artist(artist.site, artist.tag)
        source, accepted = self.repository.artist_tag_frequencies(artist.site, artist.tag)
        serializable = {
            "items": [dict(row) for row in items],
            "embeddings": [
                {key: value for key, value in dict(row).items() if key != "vector"}
                for row in embeddings
            ],
            "source": source, "accepted": accepted,
            "profile_version": self.profile_version,
        }
        digest = hashlib.sha256(json.dumps(
            serializable, sort_keys=True, default=str, separators=(",", ":")
        ).encode()).hexdigest()
        return items, embeddings, source, accepted, digest

    def build_profile(self, artist: ArtistIdentity, *, force: bool = False) -> ArtistProfile:
        items, rows, source, accepted, dependency_hash = self._inputs(artist)
        cached = self.repository.artist_profile_row(
            artist.site, artist.tag, self.profile_version
        )
        if (not force and cached is not None and not bool(cached["dirty"])
                and cached["dependency_hash"] == dependency_hash):
            self.log(f"profile reused: {artist.site}:{artist.tag}")
            return _profile_from_json(str(cached["profile_json"]))

        by_space: dict[str, list] = {}
        spaces: dict[str, EmbeddingSpace] = {}
        for row in rows:
            key = "|".join(str(row[name]) for name in (
                "backend", "model_name", "model_version", "configuration_hash",
            ))
            by_space.setdefault(key, []).append(_decode(row))
            spaces[key] = EmbeddingSpace(
                str(row["backend"]), str(row["model_name"]), str(row["model_version"]),
                str(row["configuration_hash"]), int(row["dimensions"]), str(row["dtype"]),
                bool(row["normalized"]), str(row["runtime"]), str(row["device"]),
            )
        embedding_profiles = {}
        for key, vectors in by_space.items():
            center, dispersion = centroid(vectors)
            embedding_profiles[key] = EmbeddingProfile(spaces[key], center, dispersion)

        palette = {}
        for name in ("mean_saturation", "mean_luminance", "contrast", "pastel_score"):
            values = [float(row[name]) for row in items if row[name] is not None]
            if values:
                mean = sum(values) / len(values)
                palette[name] = PaletteMetric(
                    mean, sum((value - mean) ** 2 for value in values) / len(values)
                )
        built_at = datetime.now(UTC).isoformat(timespec="seconds")
        profile = ArtistProfile(
            artist, len(items), embedding_profiles, palette, source, accepted,
            self.profile_version, dependency_hash, built_at,
        )
        versions = {key: profile.space.key for key, profile in embedding_profiles.items()}
        self.repository.save_artist_profile(
            artist.site, artist.tag, self.profile_version, dependency_hash,
            versions, len(items), _profile_to_json(profile),
        )
        self.log(f"profile built: {artist.site}:{artist.tag}")
        return profile

    def rebuild_profile(self, artist: ArtistIdentity) -> ArtistProfile:
        return self.build_profile(artist, force=True)

    def get_profile(self, artist: ArtistIdentity) -> ArtistProfile | None:
        row = self.repository.artist_profile_row(artist.site, artist.tag, self.profile_version)
        if row is None or bool(row["dirty"]):
            return None
        return _profile_from_json(str(row["profile_json"]))

    def list_profiled_artists(self) -> list[ArtistProfile]:
        return [_profile_from_json(str(row["profile_json"]))
                for row in self.repository.list_profile_rows(self.profile_version)
                if not bool(row["dirty"])]

    def list_artist_options(self) -> list[dict]:
        profiles = {(value.artist.site, value.artist.tag.casefold()): value
                    for value in self.list_profiled_artists()}
        result = []
        for site, tag in self.repository.list_artist_identities():
            profile = profiles.get((site, tag.casefold()))
            count = len(self.repository.artist_profile_inputs(site, tag))
            result.append({"artist": ArtistIdentity(site, tag), "image_count": count,
                           "profiled": profile is not None,
                           "confidence": profile.confidence_level if profile else "unbuilt"})
        return result

    def corpus_status(self) -> dict:
        return {**self.repository.similar_corpus_summary(),
                "profiles": len(self.list_profiled_artists()),
                "embedding_counts": self.repository.embedding_counts()}

    def unassigned_artist_report(self) -> dict:
        rows=self.repository.unassigned_artist_diagnostics();counts={
            "local_only":0,"gelbooru":0,"e621":0,"filename_parsable":0,
            "metadata_available":0,"metadata_missing":0,"multiple_provenances":0,
        }
        for row in rows:
            sites={value.get("site") for value in row["provenances"] if value.get("site")}
            counts["gelbooru"]+=int("gelbooru" in sites);counts["e621"]+=int("e621" in sites)
            counts["local_only"]+=int(not sites)
            counts["metadata_available"]+=int(row["metadata_available"])
            counts["metadata_missing"]+=int(bool(sites) and not row["metadata_available"])
            counts["multiple_provenances"]+=int(len(row["provenances"])>1)
            path=Path(row["cached_path"]);counts["filename_parsable"]+=int(detect_local_source(path) is not None)
        status=self.corpus_status()
        return {"total":status["images_eligible"]+status["images_skipped"],"with_artist":status["images_eligible"],"without_artist":len(rows),"counts":counts,"items":rows}

    def assign_items_to_artist(self,item_ids,artist:ArtistIdentity)->dict:
        changed=self.repository.assign_artist(list(item_ids),artist.site,artist.tag,"manual")
        profile=self.build_profile(artist,force=True)
        return {"associated":changed,"image_count":profile.image_count}

    def build_query_profile(self, item_ids) -> SimilarityQueryProfile:
        unique=tuple(dict.fromkeys(int(value) for value in item_ids));by_space={};spaces={}
        for item_id in unique:
            for backend in ("author_id_embedding","openclip"):
                row=self.repository.embedding(item_id,backend)
                if row is None:continue
                key="|".join(str(row[name]) for name in ("backend","model_name","model_version","configuration_hash"))
                by_space.setdefault(key,[]).append((item_id,_decode(row)))
                spaces[key]=EmbeddingSpace(str(row["backend"]),str(row["model_name"]),str(row["model_version"]),str(row["configuration_hash"]),int(row["dimensions"]),str(row["dtype"]),bool(row["normalized"]),str(row["runtime"]),str(row["device"]))
        profiles={};similarities={}
        for key,values in by_space.items():
            center,dispersion=centroid([vector for _item,vector in values]);profiles[key]=EmbeddingProfile(spaces[key],center,dispersion);similarities[key]={item_id:cosine(vector,center) for item_id,vector in values}
        palette={}
        stats=[self.repository.statistics(item_id) for item_id in unique]
        for name in ("mean_saturation","mean_luminance","contrast","pastel_score"):
            values=[float(getattr(value,name)) for value in stats if value is not None and getattr(value,name) is not None]
            if values:
                mean=sum(values)/len(values);palette[name]=PaletteMetric(mean,sum((value-mean)**2 for value in values)/len(values))
        return SimilarityQueryProfile(unique,profiles,similarities,palette)

    def rank_artists_for_query(self,query:SimilarityQueryProfile,backend:str,*,limit:int=20)->list[ArtistRanking]:
        query_space=self._space_query(query,backend)
        if query_space is None:raise KeyError(f"missing {backend} query embedding")
        result=[]
        for profile in self.list_profiled_artists():
            space=self._space(profile,backend,query_space.space.key)
            if space is None:continue
            result.append(ArtistRanking(profile.artist,cosine(query_space.centroid,space.centroid),None,None,profile.image_count,space.dispersion.mean_similarity))
        return sorted(result,key=lambda value:(-value.centroid_similarity,value.artist))[:limit]

    @staticmethod
    def _space_query(query:SimilarityQueryProfile,backend:str)->EmbeddingProfile|None:
        return max((value for value in query.embeddings.values() if value.space.backend==backend),key=lambda value:value.space.key,default=None)

    def suggest_artist_for_query(self,query:SimilarityQueryProfile)->dict:
        rows=self.rank_artists_for_query(query,"author_id_embedding",limit=2);first=rows[0] if rows else None;second=rows[1] if len(rows)>1 else None
        return {"top1":first,"top2":second,"margin":first.centroid_similarity-second.centroid_similarity if first and second else None}

    def item_provenances(self,item_id:int)->list[dict]:
        return [dict(row) for row in self.repository.provenances(item_id)]

    def compare_query_to_artist(self,query:SimilarityQueryProfile,candidate:ArtistProfile)->dict:
        scores={}
        for backend in ("author_id_embedding","openclip"):
            left=self._space_query(query,backend);right=self._space(candidate,backend,left.space.key if left else None)
            if left and right:scores[backend]={"centroid_similarity":cosine(left.centroid,right.centroid),"query_coherence":left.dispersion.mean_similarity,"candidate_coherence":right.dispersion.mean_similarity}
        common=set(query.palette)&set(candidate.palette)
        palette=math.sqrt(sum((query.palette[key].mean-candidate.palette[key].mean)**2 for key in common)) if common else None
        return {"embeddings":scores,"palette_distance":palette}

    def build_all(self) -> dict[str, int]:
        import time
        started = time.perf_counter()
        artists = [ArtistIdentity(*value) for value in self.repository.list_artist_identities()]
        rebuilt = reused = failed = 0
        for artist in artists:
            before = self.get_profile(artist)
            try:
                self.build_profile(artist)
                reused += int(before is not None); rebuilt += int(before is None)
            except (ValueError, OSError) as exc:
                failed += 1; self.log(f"profile failed: {artist.site}:{artist.tag}: {exc}")
        return {**self.repository.similar_corpus_summary(), "artists_found": len(artists),
                "profiles_rebuilt": rebuilt, "profiles_reused": reused,
                "profiles_failed": failed, "seconds": time.perf_counter() - started}

    @staticmethod
    def _space(
        profile: ArtistProfile, backend: str, identity_key: str | None = None,
    ) -> EmbeddingProfile | None:
        values = [value for value in profile.embeddings.values()
                  if value.space.backend == backend
                  and (identity_key is None or value.space.key == identity_key)]
        return max(values, key=lambda value: value.space.key, default=None)

    def rank_artists_for_image(
        self, item_id: int, backend: str, *, limit: int = 20, top_k: int = 3,
    ) -> list[ArtistRanking]:
        import time
        started = time.perf_counter(); self.log(f"ranking started: image {item_id} {backend}")
        query_row = self.repository.embedding(item_id, backend)
        if query_row is None:
            raise KeyError(f"missing {backend} embedding for item {item_id}")
        query = _decode(query_row)
        query_key = "|".join(str(query_row[name]) for name in (
            "backend", "model_name", "model_version", "configuration_hash",
        ))
        result = []
        for profile in self.list_profiled_artists():
            space = self._space(profile, backend, query_key)
            if space is None: continue
            rows = self.repository.embeddings_for_artist(profile.artist.site, profile.artist.tag)
            image_scores = [cosine(query, _decode(row)) for row in rows
                            if "|".join(str(row[name]) for name in (
                                "backend", "model_name", "model_version", "configuration_hash",
                            )) == query_key]
            ordered = sorted(image_scores, reverse=True)
            result.append(ArtistRanking(
                profile.artist, cosine(query, space.centroid),
                sum(ordered[:top_k]) / min(top_k, len(ordered)) if ordered else None,
                ordered[0] if ordered else None, profile.image_count,
                space.dispersion.mean_similarity,
            ))
        result = sorted(result, key=lambda row: (-row.centroid_similarity, row.artist))[:limit]
        self.log(f"ranking finished: {len(result)} artists in {time.perf_counter()-started:.6f}s")
        return result

    def rank_artists_for_artist(
        self, artist: ArtistIdentity, backend: str, *, limit: int = 20,
    ) -> list[ArtistRanking]:
        import time
        started = time.perf_counter()
        self.log(f"ranking started: artist {artist.site}:{artist.tag} {backend}")
        query_profile = self.get_profile(artist) or self.build_profile(artist)
        query_space = self._space(query_profile, backend)
        if query_space is None:
            raise KeyError(f"missing {backend} profile for {artist.site}:{artist.tag}")
        query_vectors = [
            _decode(row) for row in self.repository.embeddings_for_artist(artist.site, artist.tag)
            if "|".join(str(row[name]) for name in (
                "backend", "model_name", "model_version", "configuration_hash",
            )) == query_space.space.key
        ]
        rows = []
        for candidate in self.list_profiled_artists():
            if candidate.artist == artist: continue
            space = self._space(candidate, backend, query_space.space.key)
            if space is None: continue
            score = cosine(query_space.centroid, space.centroid)
            candidate_vectors = [
                _decode(row) for row in self.repository.embeddings_for_artist(
                    candidate.artist.site, candidate.artist.tag
                ) if "|".join(str(row[name]) for name in (
                    "backend", "model_name", "model_version", "configuration_hash",
                )) == query_space.space.key
            ]
            image_scores = sorted((cosine(left, right) for left in query_vectors
                                   for right in candidate_vectors), reverse=True)
            count = min(3, len(image_scores))
            rows.append(ArtistRanking(
                candidate.artist, score,
                sum(image_scores[:count]) / count if count else None,
                image_scores[0] if image_scores else None, candidate.image_count,
                space.dispersion.mean_similarity,
            ))
        rows = sorted(rows, key=lambda row: (-row.centroid_similarity, row.artist))[:limit]
        self.log(f"ranking finished: {len(rows)} artists in {time.perf_counter()-started:.6f}s")
        return rows

    def compare_artists(self, left: ArtistIdentity, right: ArtistIdentity) -> dict:
        left_profile = self.get_profile(left) or self.build_profile(left)
        right_profile = self.get_profile(right) or self.build_profile(right)
        embedding_scores = {}
        for key, left_space in left_profile.embeddings.items():
            right_space = right_profile.embeddings.get(key)
            if right_space is not None:
                embedding_scores[left_space.space.backend] = {
                    "centroid_similarity": cosine(left_space.centroid, right_space.centroid),
                    "left_coherence": left_space.dispersion.mean_similarity,
                    "right_coherence": right_space.dispersion.mean_similarity,
                }
        common = set(left_profile.palette) & set(right_profile.palette)
        palette_distance = math.sqrt(sum(
            (left_profile.palette[key].mean - right_profile.palette[key].mean) ** 2
            for key in common
        )) if common else None
        return {"left": left, "right": right,
                "left_image_count": left_profile.image_count,
                "right_image_count": right_profile.image_count,
                "embeddings": embedding_scores, "palette_distance": palette_distance}

    def closest_candidate_images(
        self, candidate: ArtistIdentity, backend: str, *, item_id: int | None = None,
        query_artist: ArtistIdentity | None = None, query_profile: SimilarityQueryProfile | None = None,
        exclude_item_ids=(), limit: int = 12,
    ) -> list[dict]:
        if item_id is not None:
            query_row = self.repository.embedding(item_id, backend)
            if query_row is None: return []
            query = _decode(query_row)
            identity_key = "|".join(str(query_row[name]) for name in (
                "backend", "model_name", "model_version", "configuration_hash",
            ))
        elif query_profile is not None:
            space=self._space_query(query_profile,backend)
            if space is None:return []
            query=space.centroid;identity_key=space.space.key
        elif query_artist is not None:
            profile = self.get_profile(query_artist) or self.build_profile(query_artist)
            space = self._space(profile, backend)
            if space is None: return []
            query = space.centroid; identity_key = space.space.key
        else:
            raise ValueError("an image or artist query is required")
        excluded={int(value) for value in exclude_item_ids}
        paths = {int(row["id"]): str(row["cached_path"])
                 for row in self.repository.artist_image_rows(candidate.site, candidate.tag)}
        values = []
        for row in self.repository.embeddings_for_artist(candidate.site, candidate.tag):
            key = "|".join(str(row[name]) for name in (
                "backend", "model_name", "model_version", "configuration_hash",
            ))
            if key == identity_key and int(row["item_id"]) in paths and int(row["item_id"]) not in excluded:
                provenances=self.item_provenances(int(row["item_id"]))
                values.append({"item_id": int(row["item_id"]),
                               "path": paths[int(row["item_id"])],
                               "score": cosine(query, _decode(row)),
                               "provenances":provenances})
        return sorted(values, key=lambda value: (-value["score"], value["item_id"]))[:limit]

    def suggest_artist_for_image(self, item_id: int, *, limit: int = 2) -> dict:
        rows = self.rank_artists_for_image(item_id, "author_id_embedding", limit=limit)
        first = rows[0] if rows else None; second = rows[1] if len(rows) > 1 else None
        return {"top1": first, "top2": second,
                "margin": (first.centroid_similarity - second.centroid_similarity)
                if first and second else None}
    def query_tag_frequencies(self, item_ids: list[int] | tuple[int, ...]) -> tuple[dict[str, int], dict[str, int]]:
        """Aggregate persisted reference metadata; this never schedules inference."""
        source: dict[str, int] = {}
        wd14: dict[str, int] = {}
        for item_id in item_ids:
            for tag in self.repository.source_tags(item_id):
                if tag.category not in {"artist", "rating"}:
                    source[tag.name] = source.get(tag.name, 0) + 1
            for _observation_id, observation in self.repository.observations(item_id):
                if observation.source.value == "wd14" and observation.decision.value == "accepted":
                    name = observation.reviewed_name or observation.name
                    wd14[name] = wd14.get(name, 0) + 1
        return source, wd14
