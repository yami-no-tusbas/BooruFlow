"""Command-line entry point for the isolated visual embedding experiment."""

from __future__ import annotations

import argparse
import csv
import html
import json
import random
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from booruflow.experiments.embedding_benchmark import (
    AuthorIdEmbeddingBackend,
    ExperimentalStore,
    OpenClipBackend,
    balanced_sample,
    evaluate,
    nearest,
    rank_artists,
)

CANDIDATES = (
    {"backend": "openclip", "model": "ViT-B-32/laion2b_s34b_b79k", "dimensions": 512,
     "license": "MIT code; checkpoint/data terms must be checked", "status": "implemented optional"},
    {"backend": "wd_intermediate", "model": "SmilingWolf/wd-vit-tagger-v3", "dimensions": None,
     "license": "Apache-2.0", "status": "research: no validated intermediate ONNX output yet"},
    {"backend": "anime_style", "model": "Fgdfgfthgr/Anime_Images_Style_Embedder v4",
     "dimensions": "6/7", "license": "MIT", "status": "gated DINOv3 dependency/token"},
    {"backend": "author_id", "model": "AugustLabs/Author_ID", "dimensions": 512,
     "license": "Apache-2.0", "status": "implemented: normalized pre-centroid ONNX output"},
    {"backend": "jina_clip_v2", "model": "jinaai/jina-clip-v2", "dimensions": "64-1024",
     "license": "CC-BY-NC-4.0", "status": "deferred: 0.9B parameters and non-commercial"},
)


def import_manifest(store: ExperimentalStore, manifest: Path) -> int:
    count = 0
    with manifest.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            path = Path(row["path"])
            if not path.is_absolute(): path = manifest.parent / path
            tags = tuple(value for value in row.get("tags", "").split() if value)
            groups = tuple(value for value in row.get("groups", "").split() if value)
            store.add_image(path, row.get("artist", ""), tags, groups); count += 1
    return count


def import_analysis_database(store: ExperimentalStore, database: Path) -> int:
    source = sqlite3.connect(f"file:{database}?mode=ro", uri=True); source.row_factory = sqlite3.Row
    count = 0
    try:
        for row in source.execute("""SELECT id,cached_path FROM analysis_items
            WHERE source_state='resolved' AND cached_path IS NOT NULL"""):
            tags = tuple(value[0] for value in source.execute(
                "SELECT tag_name FROM source_tags WHERE item_id=?", (row["id"],)))
            artist_rows = list(source.execute("""SELECT tag_name FROM source_tags
                WHERE item_id=? AND category='artist' ORDER BY tag_name""", (row["id"],)))
            artist = str(artist_rows[0][0]) if len(artist_rows) == 1 else ""
            path = Path(str(row["cached_path"]))
            if path.is_file():
                store.add_image(path, artist, tags, source_item_id=int(row["id"])); count += 1
    finally: source.close()
    return count


def import_filename_artists(
    store: ExperimentalStore, root: Path, artists: list[str], maximum: int, seed: int
) -> dict[str, int]:
    extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
    rng = random.Random(seed); seen_hashes: set[str] = set(); counts = {}
    folded_artists = {artist.casefold(): artist for artist in artists}
    for folded, artist in sorted(folded_artists.items()):
        candidates = [path for path in root.rglob("*") if path.is_file()
                      and path.suffix.casefold() in extensions
                      and path.name.casefold().startswith(folded + " - ")]
        rng.shuffle(candidates); added = 0
        for path in candidates:
            digest = store.file_sha256(path)
            if digest in seen_hashes: continue
            seen_hashes.add(digest)
            content_label = path.parent.name.casefold().replace(" ", "_")
            store.add_image(path, artist, (content_label,), ("filename_artist",))
            added += 1
            if added >= maximum: break
        counts[artist] = added
    return counts


def backend_from_args(args):
    if args.backend == "openclip":
        return OpenClipBackend(args.model, args.pretrained, args.device)
    if args.backend == "author_id":
        if args.model_path is None: raise ValueError("--model-path is required for Author_ID")
        derived = args.derived_model or Path("var/experiments/models/author-id-embedding.onnx")
        return AuthorIdEmbeddingBackend(args.model_path, derived, args.device)
    raise ValueError(f"backend is not executable yet: {args.backend}")


def encode(store: ExperimentalStore, backend, maximum_per_artist, seed) -> dict[str, object]:
    rows = balanced_sample(store.rows(), maximum_per_artist, seed)
    missing = []; cached = 0; dimensions = 0
    for row in rows:
        vector = store.cached_vector(int(row["id"]), backend.identity)
        if vector is None: missing.append(row)
        else: cached += 1; dimensions = int(vector.shape[0])
    started = time.perf_counter(); encoded = 0
    try:
        if missing: backend.prepare()
        for row in missing:
            store.save_vector(int(row["id"]), backend.identity, backend.encode(Path(row["path"])))
            encoded += 1; dimensions = backend.dimensions
    finally: backend.close()
    elapsed = time.perf_counter() - started
    return {"encoded": encoded, "cached": cached, "seconds": elapsed,
            "seconds_per_new_image": elapsed / encoded if encoded else 0,
            "identity": backend.identity.key, "dimensions": dimensions}


def performance(backend, count: int) -> dict[str, object]:
    from PIL import Image, ImageDraw
    def memory() -> int | None:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            return int(result.stdout.splitlines()[0])
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary); paths = []
        for index in range(count):
            path = root / f"{index}.png"
            image = Image.new("RGB", (512, 384), (240 - index * 3 % 200, 220, index * 11 % 256))
            draw = ImageDraw.Draw(image)
            draw.ellipse((30 + index * 4, 40, 260 + index * 5, 300),
                         fill=(index * 17 % 256, 80, 200))
            draw.line((0, index * 15 % 384, 511, 383 - index * 9 % 384),
                      fill=(20, 20, 20), width=5)
            image.save(path); paths.append(path)
        baseline = memory(); started = time.perf_counter(); backend.prepare()
        load = time.perf_counter() - started; after_load = memory(); times = []; readings = []
        try:
            started = time.perf_counter(); backend.encode(paths[0])
            warmup = time.perf_counter() - started
            for path in paths:
                started = time.perf_counter(); vector = backend.encode(path)
                times.append(time.perf_counter() - started); readings.append(memory())
        finally: backend.close()
    available_readings = [value for value in readings if value is not None]
    peak = max(available_readings) if available_readings else None
    return {"backend": backend.identity.backend, "model": backend.identity.model,
            "version": backend.identity.version, "device": getattr(backend, "device", ""),
            "dimensions": int(vector.shape[0]), "images": count, "load_seconds": load,
            "warmup_seconds": warmup,
            "mean_seconds": sum(times) / len(times), "minimum_seconds": min(times),
            "maximum_seconds": max(times), "vram_baseline_mib": baseline,
            "vram_after_load_mib": after_load, "vram_peak_mib": peak,
            "vram_delta_peak_mib": peak - baseline if peak is not None and baseline is not None else None}


def gallery(store, backend, query_id: int, output: Path, limit: int) -> None:
    records = store.vectors(backend.identity); neighbors = nearest(records, query_id, limit)
    query = next(row for row, _vector in records if int(row["id"]) == query_id)
    cards = []
    query_tags = set(json.loads(query["tags_json"]))
    for similarity, row in neighbors:
        common = sorted(query_tags & set(json.loads(row["tags_json"])))
        uri = Path(row["path"]).as_uri()
        cards.append(f"<article><img src='{html.escape(uri)}'><b>{html.escape(str(row['artist']) or '—')}</b>"
                     f"<span>{similarity:.4f}</span><small>{html.escape(' '.join(common[:20]))}</small></article>")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("""<!doctype html><meta charset='utf-8'><title>Embedding neighbors</title>
<style>body{font-family:sans-serif;background:#181818;color:#eee}main{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}article{background:#282828;padding:8px;display:grid;gap:5px}img{width:100%;height:220px;object-fit:contain;background:#111}span{color:#8fd}small{overflow-wrap:anywhere}</style>"""
        f"<h1>{html.escape(backend.identity.backend)} · query {query_id} · {html.escape(str(query['artist']))}</h1><main>"
        + "".join(cards) + "</main>", encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Experimental visual/style embedding benchmark")
    result.add_argument("--database", type=Path, default=Path("var/experiments/embeddings.sqlite"))
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("candidates")
    manifest = commands.add_parser("import-manifest"); manifest.add_argument("manifest", type=Path)
    analysis = commands.add_parser("import-image-analysis"); analysis.add_argument("source", type=Path)
    filenames = commands.add_parser("import-filename-artists")
    filenames.add_argument("root", type=Path); filenames.add_argument("artists", nargs="+")
    filenames.add_argument("--maximum", type=int, default=20)
    filenames.add_argument("--seed", type=int, default=0)
    label = commands.add_parser("label"); label.add_argument("image_id", type=int)
    label.add_argument("--artist", required=True); label.add_argument("--tags", default="")
    judge = commands.add_parser("judge"); judge.add_argument("query_id", type=int)
    judge.add_argument("candidate_id", type=int); judge.add_argument("label")
    judge.add_argument("--note", default="")
    for name in ("encode", "evaluate", "neighbors", "gallery", "performance"):
        command = commands.add_parser(name); command.add_argument("--backend", default="openclip")
        command.add_argument("--model", default="ViT-B-32")
        command.add_argument("--pretrained", default="laion2b_s34b_b79k")
        command.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
        command.add_argument("--model-path", type=Path)
        command.add_argument("--derived-model", type=Path)
        if name == "encode":
            command.add_argument("--maximum-per-artist", type=int); command.add_argument("--seed", type=int, default=0)
        if name in {"neighbors", "gallery"}:
            command.add_argument("query_id", type=int); command.add_argument("--limit", type=int, default=20)
        if name == "gallery": command.add_argument("--output", type=Path, required=True)
        if name == "performance": command.add_argument("--count", type=int, default=20)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.command == "candidates": print(json.dumps(CANDIDATES, indent=2)); return 0
    with ExperimentalStore(args.database) as store:
        if args.command == "import-manifest": result = {"imported": import_manifest(store, args.manifest)}
        elif args.command == "import-image-analysis": result = {"imported": import_analysis_database(store, args.source)}
        elif args.command == "import-filename-artists":
            result = {"imported_by_artist": import_filename_artists(
                store, args.root, args.artists, args.maximum, args.seed
            )}
        elif args.command == "label":
            row = store.connection.execute("SELECT path,groups_json FROM images WHERE id=?", (args.image_id,)).fetchone()
            if row is None: raise KeyError(args.image_id)
            store.add_image(Path(row["path"]), args.artist, tuple(args.tags.split()), tuple(json.loads(row["groups_json"])))
            result = {"updated": args.image_id}
        elif args.command == "judge": store.judge(args.query_id, args.candidate_id, args.label, args.note); result = {"saved": True}
        else:
            backend = backend_from_args(args)
            if args.command == "encode": result = encode(store, backend, args.maximum_per_artist, args.seed)
            elif args.command == "performance": result = performance(backend, args.count)
            else:
                records = store.vectors(backend.identity)
                if args.command == "evaluate": result = evaluate(records)
                elif args.command == "neighbors":
                    result = [{"similarity": score, "id": row["id"], "artist": row["artist"], "path": row["path"]}
                              for score, row in nearest(records, args.query_id, args.limit)]
                    result = {"images": result, "artists": rank_artists(records, args.query_id)}
                else: gallery(store, backend, args.query_id, args.output, args.limit); result = {"output": str(args.output)}
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR {exc}", file=sys.stderr); raise SystemExit(1) from exc
