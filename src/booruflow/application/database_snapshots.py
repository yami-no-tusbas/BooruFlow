"""Verified, opt-in Gelbooru catalogue snapshots.

The manifest URL is supplied by configuration; no publication endpoint is assumed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from booruflow.infrastructure.gelbooru_aliases import ALIAS_SCHEMA_VERSION
from booruflow.infrastructure.gelbooru_tag_importer import IMPORT_VERSION, fetch_page, prepare_rows

SNAPSHOT_FILES = {"tags": "gelbooru-tags.zip", "aliases": "gelbooru-aliases.zip"}
MANIFEST_SCHEMA_VERSION = 1
Progress = Callable[[str, int, int], None]


def read_manifest(url: str) -> dict:
    if not url:
        raise ValueError("Snapshot manifest URL is not configured")
    with urllib.request.urlopen(url, timeout=60) as response:
        manifest = json.load(response)
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("Unsupported snapshot manifest version")
    return manifest


def _entry(manifest: dict, kind: str) -> dict:
    entry = manifest.get("databases", {}).get(kind)
    if not isinstance(entry, dict) or entry.get("filename") != SNAPSHOT_FILES[kind]:
        raise ValueError(f"Invalid {kind} snapshot filename")
    if not isinstance(entry.get("size"), int) or entry["size"] <= 0:
        raise ValueError("Invalid snapshot size")
    if not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64:
        raise ValueError("Invalid snapshot SHA256")
    if not entry.get("generated_at"):
        raise ValueError("Missing snapshot generation date")
    if kind == "tags" and (entry.get("database_schema_version") != IMPORT_VERSION
                           or not isinstance(entry.get("last_tag_id"), int)):
        raise ValueError("Incompatible tag snapshot schema or checkpoint")
    if kind == "aliases" and entry.get("database_schema_version") != ALIAS_SCHEMA_VERSION:
        raise ValueError("Incompatible alias snapshot schema")
    return entry


def validate_snapshot(path: Path, kind: str, entry: dict) -> None:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Snapshot SQLite integrity check failed")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        needed = {"tags", "import_state"} if kind == "tags" else {
            "gelbooru_aliases", "alias_sync_state"
        }
        if not needed <= tables:
            raise ValueError("Snapshot database schema is incomplete")
        if kind == "tags":
            version = connection.execute(
                "SELECT value FROM import_state WHERE key='import_version'"
            ).fetchone()
            maximum = connection.execute("SELECT COALESCE(MAX(id),0) FROM tags").fetchone()[0]
            if version is None or version[0] != IMPORT_VERSION or maximum != entry["last_tag_id"]:
                raise ValueError("Snapshot tag version or checkpoint mismatch")
        else:
            version = connection.execute(
                "SELECT value FROM alias_sync_state WHERE key='schema_version'"
            ).fetchone()
            if version is None or version[0] != ALIAS_SCHEMA_VERSION:
                raise ValueError("Snapshot alias schema version mismatch")
            checkpoint = connection.execute(
                "SELECT value FROM alias_sync_state WHERE key='checkpoint'"
            ).fetchone()
            if checkpoint is None or len(json.loads(checkpoint[0])) < 2:
                raise ValueError("Snapshot alias checkpoint is missing")
    finally:
        connection.close()


def install_snapshot(manifest_url: str, kind: str, destination: Path,
                     progress: Progress = lambda *_: None) -> dict:
    """Download, verify, validate, and atomically activate next to the DB."""
    if kind not in SNAPSHOT_FILES:
        raise ValueError("Unknown snapshot type")
    manifest = read_manifest(manifest_url)
    entry = _entry(manifest, kind)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".booruflow-snapshot-", dir=destination.parent) as tmp:
        staging = Path(tmp)
        archive = staging / entry["filename"]
        url = urllib.parse.urljoin(manifest_url, entry["filename"])
        digest = hashlib.sha256()
        done = 0
        with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                done += len(chunk)
                if done > entry["size"]:
                    raise ValueError("Snapshot exceeds manifest size")
                digest.update(chunk)
                output.write(chunk)
                progress(entry["filename"], done, entry["size"])
        if done != entry["size"] or digest.hexdigest().lower() != entry["sha256"].lower():
            raise ValueError("Snapshot size or SHA256 mismatch")
        with zipfile.ZipFile(archive) as zipped:
            members = zipped.infolist()
            if len(members) != 1 or members[0].filename != f"{kind}.db":
                raise ValueError("Snapshot archive must contain exactly one database")
            if members[0].file_size > 4 * 1024**3:
                raise ValueError("Snapshot database is too large")
            temporary_db = staging / f"{kind}.db"
            with zipped.open(members[0]) as source, temporary_db.open("wb") as target:
                shutil.copyfileobj(source, target)
        validate_snapshot(temporary_db, kind, entry)
        if destination.exists():
            if any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm")):
                raise RuntimeError("Close SQLite users and checkpoint the database before replacing it")
            backup = staging / "old.db"
            with sqlite3.connect(destination) as source, sqlite3.connect(backup) as target:
                source.backup(target)
            durable_backup = destination.with_name(
                f"{destination.stem}.backup-{os.urandom(4).hex()}{destination.suffix}"
            )
            os.replace(backup, durable_backup)
        os.replace(temporary_db, destination)
    return entry


def incremental_tags(destination: Path, last_tag_id: int, user_id: str, api_key: str,
                     progress: Callable[[str], None] = print,
                     fetcher=fetch_page) -> int:
    """Continue from the verified snapshot cursor; commit only complete pages."""
    after_id = last_tag_id
    pages = 0
    with sqlite3.connect(destination) as connection:
        while True:
            rows = prepare_rows(fetcher(after_id, user_id, api_key))
            if not rows:
                break
            new_id = max(row[0] for row in rows)
            if new_id <= after_id:
                raise ValueError("Gelbooru incremental cursor did not advance")
            connection.executemany(
                "INSERT INTO tags VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name,post_count=excluded.post_count,category=excluded.category,"
                "ambiguous=excluded.ambiguous", rows,
            )
            after_id = new_id
            connection.execute(
                "INSERT OR REPLACE INTO import_state VALUES('after_id',?)", (str(after_id),)
            )
            connection.commit()
            pages += 1
            progress(f"Pages {pages:,} | tags {len(rows):,} | after_id {after_id:,}")
    return after_id
