"""Bounded SQLite BLOB cache for remotely acquired analysis images."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

IMAGE_ANALYSIS_CACHE_MAX_BYTES = 2 * 1024**3
IMAGE_ANALYSIS_CACHE_BUSY_TIMEOUT_SECONDS = 30.0
IMAGE_ANALYSIS_CACHE_WAL_AUTOCHECKPOINT_PAGES = 256
IMAGE_ANALYSIS_CACHE_JOURNAL_SIZE_LIMIT = 16 * 1024**2
TEST_BLOCKED_IMAGE_CACHE_ENV = "BOORUFLOW_TEST_BLOCKED_IMAGE_CACHE_DATABASE"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def image_cache_database_path(legacy_directory: Path) -> Path:
    """Place the dedicated DB beside the historical loose-file directory."""
    legacy_directory = Path(legacy_directory)
    if legacy_directory.name == "image_analysis":
        return legacy_directory.with_name("image_analysis_cache.db")
    return legacy_directory.with_name(f"{legacy_directory.name}_cache.db")


@dataclass(frozen=True, slots=True)
class ImageCacheEntry:
    cache_key: str
    site: str
    post_id: str
    source_kind: str
    mime_type: str
    width: int
    height: int
    byte_size: int
    sha256: str
    created_at: str
    last_accessed_at: str
    data: bytes


@dataclass(frozen=True, slots=True)
class ImageCachePutResult:
    stored: bool
    replaced: bool
    evicted: int
    logical_bytes: int
    entry_count: int


class ImageAnalysisCache:
    """Small persistence boundary; callers never issue cache SQL directly."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = IMAGE_ANALYSIS_CACHE_MAX_BYTES,
        timeout_seconds: float = IMAGE_ANALYSIS_CACHE_BUSY_TIMEOUT_SECONDS,
    ) -> None:
        self.path = Path(path)
        self.max_bytes = max(0, int(max_bytes))
        blocked = os.environ.get(TEST_BLOCKED_IMAGE_CACHE_ENV, "").strip()
        if blocked and self.path.resolve() == Path(blocked).resolve():
            raise RuntimeError("tests must use a temporary image-analysis cache database")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=timeout_seconds)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(f"PRAGMA busy_timeout={int(timeout_seconds * 1000)}")
        self.connection.execute(
            f"PRAGMA wal_autocheckpoint={IMAGE_ANALYSIS_CACHE_WAL_AUTOCHECKPOINT_PAGES}"
        )
        self.connection.execute(
            f"PRAGMA journal_size_limit={IMAGE_ANALYSIS_CACHE_JOURNAL_SIZE_LIMIT}"
        )
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version > 1:
            self.connection.close()
            raise RuntimeError(f"unsupported image cache schema version: {version}")
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS image_cache (
                    cache_key TEXT PRIMARY KEY,
                    site TEXT NOT NULL DEFAULT '',
                    post_id TEXT NOT NULL DEFAULT '',
                    source_kind TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL,
                    width INTEGER NOT NULL CHECK (width > 0),
                    height INTEGER NOT NULL CHECK (height > 0),
                    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL,
                    data BLOB NOT NULL,
                    CHECK (byte_size = length(data))
                );
                CREATE INDEX IF NOT EXISTS idx_image_cache_last_accessed
                    ON image_cache(last_accessed_at, cache_key);
                CREATE INDEX IF NOT EXISTS idx_image_cache_site_post
                    ON image_cache(site, post_id);
                CREATE TABLE IF NOT EXISTS image_cache_stats (
                    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
                    logical_bytes INTEGER NOT NULL,
                    entry_count INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO image_cache_stats(singleton,logical_bytes,entry_count)
                    SELECT 1,COALESCE(SUM(byte_size),0),COUNT(*) FROM image_cache;
                CREATE TRIGGER IF NOT EXISTS image_cache_stats_insert
                    AFTER INSERT ON image_cache BEGIN
                        UPDATE image_cache_stats SET
                            logical_bytes=logical_bytes+NEW.byte_size,
                            entry_count=entry_count+1 WHERE singleton=1;
                    END;
                CREATE TRIGGER IF NOT EXISTS image_cache_stats_delete
                    AFTER DELETE ON image_cache BEGIN
                        UPDATE image_cache_stats SET
                            logical_bytes=logical_bytes-OLD.byte_size,
                            entry_count=entry_count-1 WHERE singleton=1;
                    END;
                CREATE TRIGGER IF NOT EXISTS image_cache_stats_update
                    AFTER UPDATE OF byte_size ON image_cache BEGIN
                        UPDATE image_cache_stats SET
                            logical_bytes=logical_bytes-OLD.byte_size+NEW.byte_size
                            WHERE singleton=1;
                    END;
                """
            )
            if version == 0:
                self.connection.execute("PRAGMA user_version=1")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def contains(self, cache_key: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM image_cache WHERE cache_key=?", (cache_key,)
        ).fetchone() is not None

    def contains_many(self, cache_keys: list[str] | tuple[str, ...]) -> set[str]:
        """Return available keys using bounded metadata-only queries."""
        unique = list(dict.fromkeys(str(key) for key in cache_keys if key))
        available: set[str] = set()
        for offset in range(0, len(unique), 800):
            batch = unique[offset : offset + 800]
            placeholders = ",".join("?" for _ in batch)
            rows = self.connection.execute(
                f"SELECT cache_key FROM image_cache WHERE cache_key IN ({placeholders})",
                batch,
            ).fetchall()
            available.update(str(row[0]) for row in rows)
        return available

    def get(self, cache_key: str) -> ImageCacheEntry | None:
        accessed = utc_now()
        with self.connection:
            row = self.connection.execute(
                "SELECT * FROM image_cache WHERE cache_key=?", (cache_key,)
            ).fetchone()
            if row is None:
                return None
            payload = bytes(row["data"])
            digest = hashlib.sha256(payload).hexdigest()
            if digest != str(row["sha256"]) or digest != str(row["cache_key"]):
                self.connection.execute(
                    "DELETE FROM image_cache WHERE cache_key=?", (cache_key,)
                )
                return None
            self.connection.execute(
                "UPDATE image_cache SET last_accessed_at=? WHERE cache_key=?",
                (accessed, cache_key),
            )
        return ImageCacheEntry(
            cache_key=str(row["cache_key"]), site=str(row["site"]),
            post_id=str(row["post_id"]), source_kind=str(row["source_kind"]),
            mime_type=str(row["mime_type"]), width=int(row["width"]),
            height=int(row["height"]), byte_size=int(row["byte_size"]),
            sha256=str(row["sha256"]), created_at=str(row["created_at"]),
            last_accessed_at=accessed, data=payload,
        )

    def touch(self, cache_key: str) -> bool:
        with self.connection:
            return bool(self.connection.execute(
                "UPDATE image_cache SET last_accessed_at=? WHERE cache_key=?",
                (utc_now(), cache_key),
            ).rowcount)

    def put(
        self,
        cache_key: str,
        data: bytes,
        *,
        site: str = "",
        post_id: str = "",
        source_kind: str = "",
        mime_type: str,
        width: int,
        height: int,
        sha256: str,
    ) -> ImageCachePutResult:
        payload = bytes(data)
        if len(payload) > self.max_bytes:
            stats = self.stats()
            return ImageCachePutResult(
                False, False, 0, int(stats["logical_bytes"]), int(stats["entry_count"])
            )
        digest = hashlib.sha256(payload).hexdigest()
        if cache_key != digest or sha256 != digest:
            raise ValueError("image cache key and sha256 must match the BLOB content")
        now = utc_now()
        with self.connection:
            previous = self.connection.execute(
                "SELECT 1 FROM image_cache WHERE cache_key=?", (cache_key,)
            ).fetchone()
            self.connection.execute(
                """INSERT INTO image_cache(
                       cache_key,site,post_id,source_kind,mime_type,width,height,
                       byte_size,sha256,created_at,last_accessed_at,data)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(cache_key) DO UPDATE SET
                       site=excluded.site,post_id=excluded.post_id,
                       source_kind=excluded.source_kind,mime_type=excluded.mime_type,
                       width=excluded.width,height=excluded.height,
                       byte_size=excluded.byte_size,sha256=excluded.sha256,
                       last_accessed_at=excluded.last_accessed_at,data=excluded.data""",
                (cache_key, site, str(post_id), source_kind, mime_type, width, height,
                 len(payload), sha256, now, now, payload),
            )
            evicted = self._evict_locked(exclude_key=cache_key)
            logical_bytes, entry_count = self._size_locked()
        if evicted:
            self._checkpoint_passive()
        return ImageCachePutResult(
            True, previous is not None, evicted, logical_bytes, entry_count
        )

    def delete(self, cache_key: str) -> bool:
        with self.connection:
            return bool(self.connection.execute(
                "DELETE FROM image_cache WHERE cache_key=?", (cache_key,)
            ).rowcount)

    def cleanup(self) -> dict[str, int]:
        with self.connection:
            evicted = self._evict_locked()
            logical_bytes, entry_count = self._size_locked()
        if evicted:
            self._checkpoint_passive()
        return {"evicted": evicted, "logical_bytes": logical_bytes,
                "entry_count": entry_count}

    def stats(self) -> dict[str, int]:
        logical_bytes, entry_count = self._size_locked()
        return {"logical_bytes": logical_bytes, "entry_count": entry_count,
                "max_bytes": self.max_bytes}

    @contextmanager
    def materialize(self, cache_key: str, *, suffix: str = ".img") -> Iterator[Path]:
        entry = self.get(cache_key)
        if entry is None:
            raise KeyError(cache_key)
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="booruflow-analysis-", suffix=suffix or ".img", delete=False
            ) as stream:
                stream.write(entry.data)
                path = Path(stream.name)
            yield path
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    def _size_locked(self) -> tuple[int, int]:
        row = self.connection.execute(
            "SELECT logical_bytes,entry_count FROM image_cache_stats WHERE singleton=1"
        ).fetchone()
        return int(row[0]), int(row[1])

    def _evict_locked(self, *, exclude_key: str | None = None) -> int:
        logical_bytes, _count = self._size_locked()
        evicted = 0
        while logical_bytes > self.max_bytes:
            row = self.connection.execute(
                """SELECT cache_key,byte_size FROM image_cache
                   WHERE cache_key<>COALESCE(?, '')
                   ORDER BY last_accessed_at,cache_key LIMIT 1""",
                (exclude_key,),
            ).fetchone()
            if row is None:
                break
            self.connection.execute(
                "DELETE FROM image_cache WHERE cache_key=?", (row["cache_key"],)
            )
            logical_bytes -= int(row["byte_size"])
            evicted += 1
        return evicted

    def _checkpoint_passive(self) -> None:
        try:
            self.connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.OperationalError:
            pass
