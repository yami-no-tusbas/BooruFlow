"""Safe full reconstruction of the local Gelbooru tag database."""

from __future__ import annotations

import html
import json
import os
import shutil
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

API_URL = "https://gelbooru.com/index.php"
IMPORT_VERSION = "2.1-after-id-ascending"
SCHEMA = """
CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    post_count INTEGER NOT NULL DEFAULT 0,
    category INTEGER NOT NULL DEFAULT 0,
    ambiguous INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE import_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass(frozen=True, slots=True)
class ImportSummary:
    rows: int
    maximum_id: int
    zero_counts: int
    backup: Path | None
    destination: Path


def decode_name(value: object) -> str:
    result = str(value or "")
    for _index in range(10):
        decoded = html.unescape(result)
        if decoded == result:
            break
        result = decoded
    result = result.strip()
    invisible = {"\ufeff", "\u200b", "\u200c", "\u200d", "\u2060"}
    while result and (result[0] in invisible or unicodedata.category(result[0]) == "Cf"):
        result = result[1:]
    while result and (result[-1] in invisible or unicodedata.category(result[-1]) == "Cf"):
        result = result[:-1]
    return result.strip()


def prepare_rows(tags: list[dict]) -> list[tuple[int, str, int, int, int]]:
    rows: list[tuple[int, str, int, int, int]] = []
    for tag in tags:
        if not isinstance(tag, dict) or tag.get("id") is None or not tag.get("name"):
            continue
        rows.append((
            int(tag["id"]), decode_name(tag["name"]),
            int(tag.get("count", tag.get("post_count", 0)) or 0),
            int(tag.get("type", tag.get("category", 0)) or 0),
            int(tag.get("ambiguous", 0) or 0),
        ))
    return rows


def extract_tags(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [value for value in payload if isinstance(value, dict)]
    if not isinstance(payload, dict):
        return []
    if str(payload.get("success", "true")).lower() == "false":
        raise RuntimeError(str(payload.get("message", "Gelbooru API error")))
    values = payload.get("tag", payload.get("tags", []))
    if isinstance(values, dict):
        return [values]
    return [value for value in values if isinstance(value, dict)] if isinstance(values, list) else []


def fetch_page(after_id: int, user_id: str, api_key: str, *, limit: int = 100) -> list[dict]:
    parameters = urllib.parse.urlencode({
        "page": "dapi", "s": "tag", "q": "index", "json": 1,
        "limit": limit, "after_id": after_id, "orderby": "id", "order": "asc",
        "user_id": user_id, "api_key": api_key,
    })
    request = urllib.request.Request(
        f"{API_URL}?{parameters}",
        headers={"User-Agent": "BooruFlow-GelbooruTagImporter/2.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return extract_tags(json.loads(response.read().decode("utf-8")))


def fetch_maximum_id(user_id: str, api_key: str) -> int:
    parameters = urllib.parse.urlencode({
        "page": "dapi", "s": "tag", "q": "index", "json": 1,
        "limit": 1, "orderby": "id", "order": "desc",
        "user_id": user_id, "api_key": api_key,
    })
    request = urllib.request.Request(
        f"{API_URL}?{parameters}",
        headers={"User-Agent": "BooruFlow-GelbooruTagImporter/2.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        tags = extract_tags(json.loads(response.read().decode("utf-8")))
    return max((int(tag.get("id", 0)) for tag in tags), default=0)


def _open_staging(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tags'"
    ).fetchone() is None:
        connection.executescript(SCHEMA)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def _validate(path: Path, minimum_rows: int, required_tags: tuple[str, ...]) -> tuple[int, int, int]:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        rows, maximum_id, zeros = connection.execute(
            "SELECT COUNT(*),COALESCE(MAX(id),0),SUM(post_count=0) FROM tags"
        ).fetchone()
        if rows < minimum_rows:
            raise RuntimeError(f"Only {rows:,} tags imported; expected at least {minimum_rows:,}")
        duplicate_names = connection.execute(
            "SELECT lower(name),COUNT(*) FROM tags GROUP BY lower(name) HAVING COUNT(*)>1 LIMIT 5"
        ).fetchall()
        if duplicate_names:
            preview = ", ".join(f"{name} ({count})" for name, count in duplicate_names)
            raise RuntimeError(f"Duplicate canonical tag names detected: {preview}")
        encoded_names = connection.execute(
            "SELECT name FROM tags WHERE name LIKE '%&#%' OR name LIKE '%&amp;%' "
            "OR name LIKE '%&quot;%' OR name LIKE '%&apos;%' OR name LIKE '%&lt;%' "
            "OR name LIKE '%&gt;%' LIMIT 5"
        ).fetchall()
        if encoded_names:
            preview = ", ".join(str(row[0]) for row in encoded_names)
            raise RuntimeError(f"HTML entities remain in canonical tag names: {preview}")
        invisible_names = connection.execute(
            "SELECT name FROM tags WHERE instr(name,char(65279))>0 OR instr(name,char(8203))>0 "
            "OR instr(name,char(8204))>0 OR instr(name,char(8205))>0 OR instr(name,char(8288))>0 LIMIT 5"
        ).fetchall()
        if invisible_names:
            preview = ", ".join(repr(str(row[0])) for row in invisible_names)
            raise RuntimeError(f"Invisible Unicode format characters remain in tag names: {preview}")
        missing = [name for name in required_tags if connection.execute(
            "SELECT 1 FROM tags WHERE name=? COLLATE NOCASE AND category=0 AND post_count>0", (name,)
        ).fetchone() is None]
        if missing:
            raise RuntimeError("Required tags missing: " + ", ".join(missing))
        return int(rows), int(maximum_id), int(zeros or 0)
    finally:
        connection.close()


def _consolidate_names(connection: sqlite3.Connection, progress: Callable[[str], None]) -> None:
    """Keep the most-used ID for each canonical name and retain an audit trail."""
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS import_collisions (
            discarded_id INTEGER PRIMARY KEY,
            kept_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            discarded_post_count INTEGER NOT NULL,
            kept_post_count INTEGER NOT NULL,
            discarded_category INTEGER NOT NULL,
            kept_category INTEGER NOT NULL,
            reason TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS import_rejections (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            post_count INTEGER NOT NULL,
            category INTEGER NOT NULL,
            reason TEXT NOT NULL
        );
    """)
    blank_rows = connection.execute(
        "SELECT id,name,post_count,category FROM tags WHERE trim(name)=''"
    ).fetchall()
    connection.executemany(
        "INSERT OR REPLACE INTO import_rejections VALUES(?,?,?,?,?)",
        [(*row, "blank canonical name") for row in blank_rows],
    )
    if blank_rows:
        connection.executemany("DELETE FROM tags WHERE id=?", [(row[0],) for row in blank_rows])

    groups = connection.execute(
        "SELECT lower(name) FROM tags GROUP BY lower(name) HAVING COUNT(*)>1"
    ).fetchall()
    discarded: list[tuple[int, int, str, int, int, int, int, str]] = []
    for (canonical,) in groups:
        rows = connection.execute(
            "SELECT id,name,post_count,category FROM tags WHERE lower(name)=?",
            (canonical,),
        ).fetchall()
        kept = max(rows, key=lambda row: (int(row[2]), -int(row[0])))
        for row in rows:
            if row[0] == kept[0]:
                continue
            discarded.append((
                int(row[0]), int(kept[0]), str(row[1]), int(row[2]), int(kept[2]),
                int(row[3]), int(kept[3]), "duplicate canonical name; highest post_count kept",
            ))
    connection.executemany(
        "INSERT OR REPLACE INTO import_collisions VALUES(?,?,?,?,?,?,?,?)", discarded,
    )
    connection.executemany("DELETE FROM tags WHERE id=?", [(row[0],) for row in discarded])
    connection.commit()
    progress(
        f"Canonical consolidation: {len(discarded):,} duplicate row(s) archived; "
        f"{len(blank_rows):,} blank name(s) rejected"
    )


def rebuild_database(
    destination: Path,
    user_id: str,
    api_key: str,
    *,
    fetcher: Callable[[int, str, str], list[dict]] = fetch_page,
    progress: Callable[[str], None] = print,
    minimum_rows: int = 100_000,
    required_tags: tuple[str, ...] = ("office_lady", "military", "teacher", "witch"),
    retries: int = 6,
    maximum_id: int | None = None,
) -> ImportSummary:
    """Build beside the destination, validate, back up, then atomically activate."""
    if not user_id.strip() or not api_key.strip():
        raise ValueError("Gelbooru credentials are required")
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name + ".rebuild.part.sqlite")
    if staging.exists():
        existing = sqlite3.connect(staging)
        try:
            has_state = existing.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='import_state'"
            ).fetchone() is not None
            version_row = existing.execute(
                "SELECT value FROM import_state WHERE key='import_version'"
            ).fetchone() if has_state else None
        finally:
            existing.close()
        if version_row is None or str(version_row[0]) != IMPORT_VERSION:
            stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
            archived = staging.with_name(staging.name + f".obsolete-{stamp}")
            os.replace(staging, archived)
            progress(f"Archived incompatible staging database: {archived}")
    connection = _open_staging(staging)
    connection.execute(
        "INSERT OR REPLACE INTO import_state VALUES('import_version',?)", (IMPORT_VERSION,)
    )
    connection.commit()
    state = connection.execute(
        "SELECT value FROM import_state WHERE key='after_id'"
    ).fetchone()
    after_id = int(state[0]) if state else 0
    pages = 0
    imported = int(connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0])
    empty_pages = 0
    if maximum_id is None and fetcher is fetch_page:
        maximum_id = fetch_maximum_id(user_id, api_key)
    if maximum_id:
        progress(
            f"Progress reference: ID 0/{maximum_id:,}. The exact tag total will be known after import."
        )
    if after_id:
        progress(f"Resuming staging database at after_id {after_id:,} ({imported:,} tags)")
    try:
        while True:
            error: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    tags = fetcher(after_id, user_id, api_key)
                    error = None
                    break
                except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                    error = exc
                    if attempt >= retries:
                        raise
                    delay = min(30, 2 ** attempt)
                    progress(f"Retry after_id={after_id} in {delay}s: {exc}")
                    time.sleep(delay)
            if error is not None:
                raise error
            rows = prepare_rows(tags)
            if not rows:
                empty_pages += 1
                if empty_pages >= 2:
                    break
                progress(f"Empty page after_id={after_id}; confirming end of collection")
                continue
            empty_pages = 0
            new_after_id = max(row[0] for row in rows)
            if new_after_id <= after_id:
                raise RuntimeError(f"API cursor did not advance after id {after_id}")
            connection.executemany(
                "INSERT INTO tags VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name,post_count=excluded.post_count,category=excluded.category,"
                "ambiguous=excluded.ambiguous", rows,
            )
            after_id = new_after_id
            pages += 1
            imported += len(rows)
            if pages % 50 == 0:
                connection.execute(
                    "INSERT OR REPLACE INTO import_state VALUES('after_id',?)", (str(after_id),)
                )
                connection.commit()
            if pages == 1 or pages % 10 == 0:
                if maximum_id:
                    percent = min(after_id / maximum_id * 100, 100.0)
                    progress(
                        f"Pages {pages:,} | tags {imported:,} | "
                        f"ID {after_id:,}/{maximum_id:,} | {percent:.2f}% of ID range"
                    )
                else:
                    progress(f"Pages {pages:,} | tags {imported:,} | after_id {after_id:,}")
        connection.execute("INSERT OR REPLACE INTO import_state VALUES('completed','1')")
        connection.execute(
            "INSERT OR REPLACE INTO import_state VALUES('after_id',?)", (str(after_id),)
        )
        connection.execute(
            "INSERT OR REPLACE INTO import_state VALUES('completed_at',?)",
            (datetime.now().astimezone().isoformat(timespec="seconds"),),
        )
        _consolidate_names(connection, progress)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name)")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_name_nocase ON tags(name COLLATE NOCASE)"
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_tags_category ON tags(category)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_tags_post_count ON tags(post_count)")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        connection.close()
        progress(f"Staging database preserved for diagnosis: {staging}")
        raise
    connection.close()

    rows, maximum_id, zero_counts = _validate(staging, minimum_rows, required_tags)
    backup: Path | None = None
    previous_rows = 0
    previous_bytes = destination.stat().st_size if destination.exists() else 0
    if destination.exists():
        old: sqlite3.Connection | None = None
        try:
            old = sqlite3.connect(f"file:{destination.as_posix()}?mode=ro", uri=True)
            previous_rows = int(old.execute("SELECT COUNT(*) FROM tags").fetchone()[0])
        except sqlite3.Error:
            previous_rows = 0
        finally:
            if old is not None:
                old.close()
        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
        backup = destination.with_name(f"{destination.stem}.backup-{stamp}{destination.suffix}")
        shutil.copy2(destination, backup)
        progress(f"Backup: {backup} ({backup.stat().st_size:,} bytes)")
    try:
        os.replace(staging, destination)
    except OSError as exc:
        raise RuntimeError(
            f"Validated database could not be activated. Close BooruFlow/SQLite tools and retry: {staging}"
        ) from exc
    progress(f"Activated: {destination} ({destination.stat().st_size:,} bytes, {rows:,} tags)")
    progress(
        f"Comparison: rows {previous_rows:,} -> {rows:,} ({rows - previous_rows:+,}); "
        f"bytes {previous_bytes:,} -> {destination.stat().st_size:,}"
    )
    return ImportSummary(rows, maximum_id, zero_counts, backup, destination)
