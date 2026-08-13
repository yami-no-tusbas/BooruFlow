#!/usr/bin/env python3
"""Télécharge et importe l'export officiel des tags e621 dans SQLite."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import shutil
import sqlite3
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPORT_INDEX = "https://e621.net/db_exports.json"
USER_AGENT = "ArtistByTag/1.0 (personal local tag database)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Construit une base SQLite locale depuis l'export tags d'e621."
    )
    parser.add_argument("--db", type=Path, default=Path("e621_tags.db"), help="Base SQLite cible.")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("e621_exports"),
        help="Dossier des fichiers téléchargés.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        help="Utilise un tags.csv.gz local au lieu de télécharger le dernier export.",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Retélécharge l'export même si son checksum local est valide.",
    )
    return parser.parse_args()


def request_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_tags_export() -> dict[str, Any]:
    exports = request_json(EXPORT_INDEX)
    for item in exports:
        if item.get("name") == "tags":
            return item
    raise RuntimeError("L'index e621 ne contient aucun export nommé 'tags'.")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download_export(item: dict[str, Any], cache_dir: Path, force: bool) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / str(item["file_name"])
    expected = str(item["checksum"]).lower()
    if destination.is_file() and not force:
        print("Vérification du fichier déjà présent…", flush=True)
        if sha256_file(destination).lower() == expected:
            print(f"Export réutilisé : {destination}", flush=True)
            return destination

    temporary = destination.with_suffix(destination.suffix + ".part")
    existing = temporary.stat().st_size if temporary.is_file() else 0
    expected_size = int(item.get("file_size") or 0)
    if existing and expected_size and existing == expected_size:
        print("Validation du téléchargement repris…", flush=True)
        if sha256_file(temporary).lower() == expected:
            temporary.replace(destination)
            return destination
        temporary.unlink()
        existing = 0
    headers = {"User-Agent": USER_AGENT}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    request = urllib.request.Request(str(item["url"]), headers=headers)
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=120) as response:
        append = existing > 0 and getattr(response, "status", 200) == 206
        if not append:
            existing = 0
        mode = "ab" if append else "wb"
        total = expected_size
        downloaded = existing
        last_report = 0.0
        with temporary.open(mode) as file:
            while chunk := response.read(1024 * 1024):
                file.write(chunk)
                downloaded += len(chunk)
                now = time.monotonic()
                if now - last_report >= 1:
                    percent = downloaded * 100 / total if total else 0
                    speed = downloaded / max(now - started, 0.001) / 1024 / 1024
                    print(
                        f"\rTéléchargement : {percent:6.2f}% ({speed:5.1f} Mio/s)",
                        end="",
                        flush=True,
                    )
                    last_report = now
    print(flush=True)
    if sha256_file(temporary).lower() != expected:
        raise RuntimeError("Checksum SHA-256 incorrect pour l'export téléchargé.")
    temporary.replace(destination)
    return destination


def configure(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("PRAGMA busy_timeout=30000")


def import_tags(source: Path, database: Path, export_info: dict[str, Any]) -> int:
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.is_file():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")  # noqa: DTZ005
        backup = database.with_name(f"{database.stem}.backup-{stamp}{database.suffix}")
        shutil.copy2(database, backup)
        print(f"Sauvegarde : {backup}", flush=True)

    connection = sqlite3.connect(database)
    configure(connection)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            DROP TABLE IF EXISTS tags_import;
            CREATE TABLE tags_import(
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                category INTEGER NOT NULL,
                post_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_locked INTEGER NOT NULL
            );
            """
        )
        inserted = 0
        started = time.monotonic()
        batch: list[tuple] = []
        with gzip.open(source, "rt", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            expected = {
                "id",
                "name",
                "category",
                "post_count",
                "created_at",
                "updated_at",
                "is_locked",
            }
            if not expected.issubset(reader.fieldnames or []):
                raise RuntimeError(f"Colonnes e621 inattendues : {reader.fieldnames}")
            for row in reader:
                batch.append(
                    (
                        int(row["id"]),
                        row["name"],
                        int(row["category"]),
                        int(row["post_count"]),
                        row["created_at"],
                        row["updated_at"],
                        1 if row["is_locked"].lower() in {"t", "true", "1"} else 0,
                    )
                )
                if len(batch) >= 10_000:
                    connection.executemany("INSERT INTO tags_import VALUES(?,?,?,?,?,?,?)", batch)
                    inserted += len(batch)
                    batch.clear()
                    elapsed = max(time.monotonic() - started, 0.001)
                    print(
                        f"\rImport : {inserted:,} tags ({inserted / elapsed:,.0f}/s)",
                        end="",
                        flush=True,
                    )
            if batch:
                connection.executemany("INSERT INTO tags_import VALUES(?,?,?,?,?,?,?)", batch)
                inserted += len(batch)
        print(flush=True)
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DROP INDEX IF EXISTS idx_tags_name")
        connection.execute("DROP INDEX IF EXISTS idx_tags_category")
        connection.execute("DROP TABLE IF EXISTS tags_old")
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tags'"
        ).fetchone():
            connection.execute("ALTER TABLE tags RENAME TO tags_old")
        connection.execute("ALTER TABLE tags_import RENAME TO tags")
        connection.execute("CREATE INDEX idx_tags_name ON tags(name)")
        connection.execute("CREATE INDEX idx_tags_category ON tags(category)")
        connection.execute("DROP TABLE IF EXISTS tags_old")
        metadata = {
            "site": "e621",
            "schema_version": "1",
            "source_file": source.name,
            "source_checksum": str(export_info.get("checksum", "")),
            "source_updated_at": str(export_info.get("updated_at", "")),
            "imported_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "tag_count": str(inserted),
        }
        connection.executemany(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
            metadata.items(),
        )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        actual = connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        if integrity != "ok" or actual != inserted:
            raise RuntimeError(
                f"Validation échouée : integrity={integrity}, lignes={actual}/{inserted}"
            )
        print(f"Base créée : {database}")
        print(f"Tags : {actual:,} — intégrité : {integrity}")
        return inserted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    args = parse_args()
    if args.source:
        if not args.source.is_file():
            raise FileNotFoundError(args.source)
        source = args.source
        info = {
            "checksum": sha256_file(source),
            "updated_at": "",
            "file_name": source.name,
        }
    else:
        info = latest_tags_export()
        print(
            f"Export e621 : {info['file_name']} — "
            f"{int(info.get('file_size', 0)) / 1024 / 1024:.1f} Mio"
        )
        source = download_export(info, args.cache_dir, args.force_download)
    import_tags(source, args.db, info)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrompu par l'utilisateur.", file=sys.stderr)
        raise SystemExit(130)
