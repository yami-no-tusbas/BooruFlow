"""Persistent cache for wiki pages explicitly consulted in Wiki Browser."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

WIKI_PAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_pages(
    site TEXT NOT NULL,
    tag TEXT NOT NULL COLLATE NOCASE,
    wiki_id INTEGER,
    tag_type TEXT,
    content TEXT NOT NULL,
    content_format TEXT NOT NULL DEFAULT 'plain',
    author TEXT,
    remote_updated_at TEXT,
    wiki_version INTEGER,
    cached_at TEXT NOT NULL,
    source_url TEXT NOT NULL,
    referenced_tags_json TEXT NOT NULL DEFAULT '[]',
    samples_json TEXT NOT NULL DEFAULT '[]',
    recurring_json TEXT NOT NULL DEFAULT '[]',
    sample_size INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(site, tag)
);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_site_tag ON wiki_pages(site, tag COLLATE NOCASE);
"""


def ensure_wiki_page_schema(connection: sqlite3.Connection) -> None:
    """Apply the additive cache schema to an existing taxonomy database."""
    connection.executescript(WIKI_PAGE_SCHEMA)


@dataclass(frozen=True, slots=True)
class WikiPageRecord:
    site: str
    tag: str
    content: str
    source_url: str
    content_format: str = "plain"
    wiki_id: int | None = None
    tag_type: str | None = None
    author: str | None = None
    remote_updated_at: str | None = None
    version: int | None = None
    cached_at: str | None = None
    referenced_tags: tuple[str, ...] = ()
    samples: tuple[dict, ...] = ()
    recurring: tuple[dict, ...] = ()
    sample_size: int = 0


class WikiPageCache:
    """Atomic cache stored inside the existing per-site taxonomy database."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database)

    def _connect(self) -> sqlite3.Connection:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database)
        ensure_wiki_page_schema(connection)
        return connection

    @staticmethod
    def _record(row: tuple) -> WikiPageRecord:
        return WikiPageRecord(
            site=str(row[0]),
            tag=str(row[1]),
            content=str(row[2]),
            source_url=str(row[3]),
            content_format=str(row[4]),
            wiki_id=row[5],
            tag_type=row[6],
            author=row[7],
            remote_updated_at=row[8],
            version=row[9],
            cached_at=str(row[10]),
            referenced_tags=tuple(json.loads(row[11] or "[]")),
            samples=tuple(json.loads(row[12] or "[]")),
            recurring=tuple(json.loads(row[13] or "[]")),
            sample_size=int(row[14]),
        )

    def get(self, site: str, tag: str) -> WikiPageRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT site,tag,content,source_url,content_format,wiki_id,tag_type,author,"
                "remote_updated_at,wiki_version,cached_at,referenced_tags_json,"
                "samples_json,recurring_json,sample_size FROM wiki_pages "
                "WHERE site=? AND tag=? COLLATE NOCASE",
                (site, tag),
            ).fetchone()
        if row is None:
            return None
        return self._record(row)

    def get_many(self, site: str, tags: list[str] | tuple[str, ...]) -> dict[str, WikiPageRecord]:
        """Load cached pages in bounded batches using one database connection."""
        wanted = list(dict.fromkeys(tag for tag in tags if tag))
        if not wanted:
            return {}
        records: dict[str, WikiPageRecord] = {}
        with closing(self._connect()) as connection:
            for offset in range(0, len(wanted), 500):
                batch = wanted[offset : offset + 500]
                placeholders = ",".join("?" for _tag in batch)
                rows = connection.execute(
                    "SELECT site,tag,content,source_url,content_format,wiki_id,tag_type,author,"
                    "remote_updated_at,wiki_version,cached_at,referenced_tags_json,"
                    "samples_json,recurring_json,sample_size FROM wiki_pages "
                    f"WHERE site=? AND tag COLLATE NOCASE IN ({placeholders})",
                    (site, *batch),
                ).fetchall()
                records.update(
                    (record.tag.casefold(), record)
                    for record in map(self._record, rows)
                )
        return records

    def put(self, record: WikiPageRecord) -> WikiPageRecord:
        cached = replace(
            record,
            cached_at=record.cached_at or datetime.now(UTC).isoformat(timespec="seconds"),
        )
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO wiki_pages(
                    site,tag,wiki_id,tag_type,content,content_format,author,
                    remote_updated_at,wiki_version,cached_at,source_url,
                    referenced_tags_json,samples_json,recurring_json,sample_size
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(site,tag) DO UPDATE SET
                    wiki_id=excluded.wiki_id,
                    tag_type=excluded.tag_type,
                    content=excluded.content,
                    content_format=excluded.content_format,
                    author=excluded.author,
                    remote_updated_at=excluded.remote_updated_at,
                    wiki_version=excluded.wiki_version,
                    cached_at=excluded.cached_at,
                    source_url=excluded.source_url,
                    referenced_tags_json=excluded.referenced_tags_json,
                    samples_json=excluded.samples_json,
                    recurring_json=excluded.recurring_json,
                    sample_size=excluded.sample_size""",
                (
                    cached.site,
                    cached.tag,
                    cached.wiki_id,
                    cached.tag_type,
                    cached.content,
                    cached.content_format,
                    cached.author,
                    cached.remote_updated_at,
                    cached.version,
                    cached.cached_at,
                    cached.source_url,
                    json.dumps(cached.referenced_tags, ensure_ascii=False),
                    json.dumps(cached.samples, ensure_ascii=False),
                    json.dumps(cached.recurring, ensure_ascii=False),
                    cached.sample_size,
                ),
            )
        return cached
