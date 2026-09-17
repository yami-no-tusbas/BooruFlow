"""Reusable SQLite cache for complete Gelbooru/e621 post metadata."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from booruflow.domain.auto_organize import PostMetadata


class PostMetadataCache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS remote_posts(
          site TEXT NOT NULL, post_id TEXT NOT NULL, fetched_at TEXT NOT NULL,
          rating TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
          md5 TEXT NOT NULL DEFAULT '', extra_json TEXT NOT NULL DEFAULT '{}',
          PRIMARY KEY(site, post_id));
        CREATE TABLE IF NOT EXISTS remote_post_tags(
          site TEXT NOT NULL, post_id TEXT NOT NULL, tag TEXT NOT NULL,
          category TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(site, post_id, tag),
          FOREIGN KEY(site, post_id) REFERENCES remote_posts(site, post_id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS idx_remote_post_tags_tag ON remote_post_tags(site, tag);
        """)
        self.connection.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        self.connection.close()

    def get(self, site: str, post_id: str, max_age_days: int | None = None) -> PostMetadata | None:
        row = self.connection.execute(
            "SELECT fetched_at,rating,source,md5,extra_json FROM remote_posts WHERE site=? AND post_id=?",
            (site, post_id)).fetchone()
        if row is None:
            return None
        if max_age_days is not None:
            fetched = datetime.fromisoformat(str(row[0]))
            if fetched.tzinfo is None: fetched = fetched.replace(tzinfo=UTC)
            if fetched < datetime.now(UTC) - timedelta(days=max_age_days): return None
        tag_rows = list(self.connection.execute(
            "SELECT tag,category FROM remote_post_tags WHERE site=? AND post_id=? ORDER BY tag",
            (site, post_id)))
        categories = {str(tag): str(category) for tag, category in tag_rows}
        extra = json.loads(row[4] or "{}")
        by_category = lambda name: tuple(tag for tag, category in tag_rows if str(category) == name)
        return PostMetadata(site, post_id, tuple(tag for tag, _ in tag_rows), categories,
                            by_category("artist"), by_category("copyright"),
                            by_category("character"), by_category("species"),
                            str(row[1]), str(row[2]), str(row[3]), extra)

    def put(self, metadata: PostMetadata) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        categories = dict(metadata.categories)
        for category, values in (("artist", metadata.artists), ("copyright", metadata.copyrights),
                                 ("character", metadata.characters), ("species", metadata.species)):
            for tag in values: categories[tag] = category
        tags = tuple(dict.fromkeys((*metadata.tags, *categories)))
        with self.connection:
            self.connection.execute("""INSERT INTO remote_posts(site,post_id,fetched_at,rating,source,md5,extra_json)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(site,post_id) DO UPDATE SET fetched_at=excluded.fetched_at,
              rating=excluded.rating,source=excluded.source,md5=excluded.md5,extra_json=excluded.extra_json""",
              (metadata.site, metadata.post_id, now, metadata.rating, metadata.source,
               metadata.md5, json.dumps(metadata.extra, ensure_ascii=False, sort_keys=True)))
            self.connection.execute("DELETE FROM remote_post_tags WHERE site=? AND post_id=?",
                                    (metadata.site, metadata.post_id))
            self.connection.executemany("INSERT INTO remote_post_tags(site,post_id,tag,category) VALUES(?,?,?,?)",
                ((metadata.site, metadata.post_id, tag, categories.get(tag, "")) for tag in tags))
