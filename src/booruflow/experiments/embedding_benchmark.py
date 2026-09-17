"""Experimental visual/style embedding dataset, cache, metrics and backends."""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GENERIC_CONTENT_TAGS = frozenset({
    "1girl", "1boy", "solo", "looking_at_viewer", "short_hair",
})
JUDGMENT_LABELS = frozenset({
    "strongly_similar", "style_only", "interesting_different", "false_positive",
})


@dataclass(frozen=True, slots=True)
class ExperimentIdentity:
    backend: str
    model: str
    version: str
    configuration: str

    @property
    def key(self) -> str:
        raw = json.dumps(self.__dict__ if hasattr(self, "__dict__") else {
            "backend": self.backend, "model": self.model, "version": self.version,
            "configuration": self.configuration,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class EmbeddingExperimentBackend(ABC):
    identity: ExperimentIdentity
    dimensions: int

    @abstractmethod
    def prepare(self) -> None: ...

    @abstractmethod
    def encode(self, image: Path): ...

    @abstractmethod
    def close(self) -> None: ...


class OpenClipBackend(EmbeddingExperimentBackend):
    """Optional PyTorch baseline; imports no production dependency at module load."""

    def __init__(
        self, model: str = "ViT-B-32", pretrained: str = "laion2b_s34b_b79k",
        device: str = "cuda",
    ) -> None:
        self.model_name = model; self.pretrained = pretrained; self.device = device
        self.identity = ExperimentIdentity(
            "openclip", model, pretrained,
            json.dumps({"preprocessing": "checkpoint-transform-rgb-exif-v1"}, sort_keys=True),
        )
        self.dimensions = 0; self.model = None; self.preprocess = None; self.torch = None

    def prepare(self) -> None:
        import importlib
        try:
            self.torch = importlib.import_module("torch")
            open_clip = importlib.import_module("open_clip")
        except ImportError as exc:
            raise RuntimeError(
                "OpenCLIP experiment requires optional packages torch and open_clip_torch"
            ) from exc
        if self.device == "cuda" and not self.torch.cuda.is_available():
            self.device = "cpu"
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            self.model_name, pretrained=self.pretrained, device=self.device
        )
        self.model.eval()

    def encode(self, image: Path):
        if self.model is None: self.prepare()
        from PIL import Image, ImageOps
        with Image.open(image) as source:
            rgb = ImageOps.exif_transpose(source).convert("RGB")
            tensor = self.preprocess(rgb).unsqueeze(0).to(self.device)
        with self.torch.inference_mode():
            vector = self.model.encode_image(tensor).float().cpu().numpy()[0]
        vector = normalize(vector); self.dimensions = int(vector.shape[0])
        return vector

    def close(self) -> None:
        self.model = None; self.preprocess = None
        if self.torch is not None and self.device == "cuda": self.torch.cuda.empty_cache()


class AuthorIdEmbeddingBackend(EmbeddingExperimentBackend):
    """Expose Author_ID's normalized 512D backbone vector, before fixed centroids/TopK."""

    OUTPUT_NAME = "/backbone/Div_output_0"

    def __init__(self, source_model: Path, derived_model: Path, device: str = "cuda") -> None:
        self.source_model = source_model; self.derived_model = derived_model
        self.device = device
        version_value = ExperimentalStore.file_sha256(source_model)
        self.identity = ExperimentIdentity(
            "author_id_embedding", "AugustLabs/Author_ID", version_value,
            json.dumps({"input": 384, "preprocessing": "imagenet-bilinear-rgb-exif-v2",
                        "output": self.OUTPUT_NAME}, sort_keys=True),
        )
        self.dimensions = 512; self.session = None; self.runtime_diagnostic = None

    def _create_derived_model(self) -> None:
        import importlib
        onnx = importlib.import_module("onnx")
        model = onnx.shape_inference.infer_shapes(onnx.load(self.source_model))
        value = next((item for item in model.graph.value_info if item.name == self.OUTPUT_NAME), None)
        if value is None: raise RuntimeError(f"Author_ID embedding node is missing: {self.OUTPUT_NAME}")
        del model.graph.output[:]
        model.graph.output.append(value)
        self.derived_model.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.derived_model.with_suffix(".part.onnx")
        onnx.save(model, temporary); temporary.replace(self.derived_model)

    def prepare(self) -> None:
        from booruflow.infrastructure.onnx_runtime import create_inference_session
        if not self.derived_model.is_file(): self._create_derived_model()
        self.session, self.runtime_diagnostic = create_inference_session(
            self.derived_model,
            (("CUDAExecutionProvider", "CPUExecutionProvider")
             if self.device == "cuda" else ("CPUExecutionProvider",)),
        )
        self.device = "cuda" if self.runtime_diagnostic.effective_provider == "CUDAExecutionProvider" else "cpu"
        shape = self.session.get_outputs()[0].shape
        if int(shape[-1]) != self.dimensions:
            raise RuntimeError(f"unexpected Author_ID embedding shape: {shape}")

    def encode(self, image: Path):
        if self.session is None: self.prepare()
        from PIL import Image, ImageOps
        numpy = _numpy()
        with Image.open(image) as source:
            rgba = ImageOps.exif_transpose(source).convert("RGBA")
            background = Image.new("RGBA", rgba.size, "white")
            background.alpha_composite(rgba)
            rgb = background.convert("RGB").resize((384, 384), Image.Resampling.BILINEAR)
        value = numpy.asarray(rgb, dtype=numpy.float32) / 255.0
        value = value.transpose(2, 0, 1)[None, ...]
        mean = numpy.asarray([0.485, 0.456, 0.406], dtype=numpy.float32)[None, :, None, None]
        std = numpy.asarray([0.229, 0.224, 0.225], dtype=numpy.float32)[None, :, None, None]
        return normalize(self.session.run([self.OUTPUT_NAME], {"image": (value - mean) / std})[0][0])

    def close(self) -> None: self.session = None


class ExperimentalStore:
    SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path; self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS images(
                id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, content_sha256 TEXT NOT NULL,
                artist TEXT NOT NULL DEFAULT '', tags_json TEXT NOT NULL DEFAULT '[]',
                groups_json TEXT NOT NULL DEFAULT '[]', source_item_id INTEGER);
            CREATE TABLE IF NOT EXISTS embeddings(
                image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
                backend_key TEXT NOT NULL, backend TEXT NOT NULL, model TEXT NOT NULL,
                version TEXT NOT NULL, configuration TEXT NOT NULL, dimensions INTEGER NOT NULL,
                dtype TEXT NOT NULL, vector BLOB NOT NULL,
                PRIMARY KEY(image_id,backend_key));
            CREATE TABLE IF NOT EXISTS judgments(
                query_id INTEGER NOT NULL REFERENCES images(id),
                candidate_id INTEGER NOT NULL REFERENCES images(id),
                label TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(query_id,candidate_id));
            PRAGMA user_version=1;
        """)

    def close(self) -> None: self.connection.close()
    def __enter__(self): return self
    def __exit__(self, *_args): self.close()

    @staticmethod
    def file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024): digest.update(chunk)
        return digest.hexdigest()

    def add_image(
        self, path: Path, artist: str = "", tags: tuple[str, ...] = (),
        groups: tuple[str, ...] = (), source_item_id: int | None = None,
    ) -> int:
        resolved = path.resolve(strict=True); digest = self.file_sha256(resolved)
        with self.connection:
            self.connection.execute("""INSERT INTO images(
                path,content_sha256,artist,tags_json,groups_json,source_item_id)
                VALUES(?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
                content_sha256=excluded.content_sha256,artist=excluded.artist,
                tags_json=excluded.tags_json,groups_json=excluded.groups_json,
                source_item_id=COALESCE(excluded.source_item_id,images.source_item_id)""",
                (str(resolved), digest, artist.strip(), json.dumps(sorted(set(tags))),
                 json.dumps(sorted(set(groups))), source_item_id))
        return int(self.connection.execute("SELECT id FROM images WHERE path=?", (str(resolved),)).fetchone()[0])

    def rows(self) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM images ORDER BY id"))

    def cached_vector(self, image_id: int, identity: ExperimentIdentity):
        row = self.connection.execute(
            "SELECT dimensions,dtype,vector FROM embeddings WHERE image_id=? AND backend_key=?",
            (image_id, identity.key),
        ).fetchone()
        if row is None: return None
        numpy = _numpy(); return numpy.frombuffer(row["vector"], dtype=row["dtype"]).copy()

    def save_vector(self, image_id: int, identity: ExperimentIdentity, vector) -> None:
        numpy = _numpy(); value = normalize(numpy.asarray(vector, dtype=numpy.float32).reshape(-1))
        with self.connection:
            self.connection.execute("""INSERT OR REPLACE INTO embeddings(
                image_id,backend_key,backend,model,version,configuration,dimensions,dtype,vector)
                VALUES(?,?,?,?,?,?,?,?,?)""", (image_id, identity.key, identity.backend,
                identity.model, identity.version, identity.configuration, value.size,
                str(value.dtype), value.tobytes()))

    def vectors(self, identity: ExperimentIdentity):
        numpy = _numpy(); records = []
        for row in self.connection.execute("""SELECT i.*,e.dimensions,e.dtype,e.vector
            FROM images i JOIN embeddings e ON e.image_id=i.id
            WHERE e.backend_key=? ORDER BY i.id""", (identity.key,)):
            records.append((row, numpy.frombuffer(row["vector"], dtype=row["dtype"]).copy()))
        return records

    def judge(self, query_id: int, candidate_id: int, label: str, note: str = "") -> None:
        if label not in JUDGMENT_LABELS: raise ValueError(f"unknown judgment: {label}")
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO judgments VALUES(?,?,?,?)",
                                    (query_id, candidate_id, label, note.strip()))


def _numpy():
    import importlib
    return importlib.import_module("numpy")


def normalize(vector):
    numpy = _numpy(); value = numpy.asarray(vector, dtype=numpy.float32).reshape(-1)
    norm = float(numpy.linalg.norm(value))
    if not numpy.isfinite(value).all() or norm <= 0: raise ValueError("embedding is not L2-normalizable")
    return value / norm


def balanced_sample(rows, maximum_per_artist: int | None, seed: int):
    if maximum_per_artist is None: return list(rows)
    grouped: dict[str, list[Any]] = {}
    for row in rows: grouped.setdefault(str(row["artist"]), []).append(row)
    rng = random.Random(seed); selected = []
    for artist in sorted(grouped):
        values = grouped[artist][:]; rng.shuffle(values); selected.extend(values[:maximum_per_artist])
    return sorted(selected, key=lambda row: int(row["id"]))


def content_tags(row) -> set[str]:
    return set(json.loads(row["tags_json"])) - GENERIC_CONTENT_TAGS


def tag_overlap(left, right) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def evaluate(records, *, k_values=(1, 5, 10), low_overlap=0.20, high_overlap=0.50):
    numpy = _numpy()
    if len(records) < 2: raise ValueError("at least two encoded images are required")
    rows = [record[0] for record in records]
    matrix = numpy.stack([normalize(record[1]) for record in records])
    similarities = matrix @ matrix.T; numpy.fill_diagonal(similarities, -numpy.inf)
    recalls = {k: [] for k in k_values}; reciprocal = []; cross = []; leakage = []
    for index, row in enumerate(rows):
        artist = str(row["artist"]); order = numpy.argsort(-similarities[index])
        same = [rank for rank, other in enumerate(order, 1) if str(rows[other]["artist"]) == artist and artist]
        if same:
            reciprocal.append(1 / same[0])
            for k in k_values: recalls[k].append(float(any(rank <= k for rank in same)))
        query_tags = content_tags(row)
        same_cross = [other for other in range(len(rows)) if other != index
                      and artist and str(rows[other]["artist"]) == artist
                      and tag_overlap(query_tags, content_tags(rows[other])) <= low_overlap]
        if same_cross:
            best = max(same_cross, key=lambda other: similarities[index, other])
            cross.append(1 / (int(numpy.where(order == best)[0][0]) + 1))
            decoys = [other for other in range(len(rows)) if str(rows[other]["artist"]) != artist
                      and tag_overlap(query_tags, content_tags(rows[other])) >= high_overlap]
            if decoys:
                leakage.append(float(max(similarities[index, other] for other in decoys)
                                     > similarities[index, best]))
    artists = {}
    for artist in sorted({str(row["artist"]) for row in rows if str(row["artist"])}):
        indices = [i for i, row in enumerate(rows) if str(row["artist"]) == artist]
        centroid = normalize(matrix[indices].mean(axis=0))
        values = numpy.clip(matrix[indices] @ centroid, -1.0, 1.0)
        others = []
        for other in sorted({str(row["artist"]) for row in rows if str(row["artist"]) and str(row["artist"]) != artist}):
            other_indices = [i for i, row in enumerate(rows) if str(row["artist"]) == other]
            others.append(float(centroid @ normalize(matrix[other_indices].mean(axis=0))))
        artists[artist] = {"images": len(indices), "coherence": float(values.mean()),
                           "dispersion": float((1 - values).mean()),
                           "distance_variance": float(numpy.var(1 - values)),
                           "distinctiveness": 1 - max(others) if others else None}
    return {"images": len(rows), "artists": len(artists),
            **{f"recall@{k}": sum(v) / len(v) if v else None for k, v in recalls.items()},
            "mrr": sum(reciprocal) / len(reciprocal) if reciprocal else None,
            "cross_subject_mrr": sum(cross) / len(cross) if cross else None,
            "content_leakage_rate": sum(leakage) / len(leakage) if leakage else None,
            "artist_statistics": artists}


def nearest(records, query_id: int, limit: int = 20):
    query = next((vector for row, vector in records if int(row["id"]) == query_id), None)
    if query is None: raise KeyError(query_id)
    result = []
    for row, vector in records:
        if int(row["id"]) != query_id:
            result.append((float(normalize(query) @ normalize(vector)), row))
    return sorted(result, key=lambda value: (-value[0], int(value[1]["id"])))[:limit]


def rank_artists(records, query_id: int, top_k: int = 3):
    numpy = _numpy(); query = normalize(next(vector for row, vector in records if int(row["id"]) == query_id))
    grouped: dict[str, list[float]] = {}; centroids = {}
    for row, vector in records:
        if int(row["id"]) == query_id or not str(row["artist"]): continue
        grouped.setdefault(str(row["artist"]), []).append(float(query @ normalize(vector)))
    for artist in grouped:
        vectors = [normalize(vector) for row, vector in records if str(row["artist"]) == artist]
        centroids[artist] = float(query @ normalize(numpy.stack(vectors).mean(axis=0)))
    return sorted(({"artist": artist, "best_image": max(values),
                    "top_k_mean": sum(sorted(values, reverse=True)[:top_k]) / min(top_k, len(values)),
                    "centroid": centroids[artist], "images": len(values)}
                   for artist, values in grouped.items()), key=lambda item: -item["centroid"])
