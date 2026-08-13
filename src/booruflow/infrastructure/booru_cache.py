"""Cache SQLite local, daté et séparé par site pour les métadonnées booru."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def is_fresh(checked_at: str | None, max_age_days: int) -> bool:
    if not checked_at or max_age_days <= 0:
        return False
    try:
        checked = datetime.fromisoformat(checked_at)
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=UTC)
    except ValueError:
        return False
    return checked >= datetime.now(UTC) - timedelta(days=max_age_days)


class BooruCache:
    def __init__(self, path: Path, site: str) -> None:
        self.path = path
        self.site = site
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._initialize()

    def _initialize(self) -> None:
        if self.site.startswith("gelbooru:"):
            existing_tables = {
                str(row[0])
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            legacy_rows = 0
            if "query_pages" in existing_tables:
                legacy_rows = int(
                    self.connection.execute("SELECT COUNT(*) FROM query_pages").fetchone()[0]
                )
            compact_rows = 0
            if "gel_queries" in existing_tables:
                compact_rows = int(
                    self.connection.execute("SELECT COUNT(*) FROM gel_queries").fetchone()[0]
                )
            if legacy_rows and not compact_rows:
                self.connection.close()
                raise RuntimeError(f"Cache Gelbooru v1 à migrer avant utilisation : {self.path}")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artist_totals (
                artist TEXT PRIMARY KEY,
                total_posts INTEGER NOT NULL,
                checked_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artist_query_counts (
                query_key TEXT NOT NULL,
                artist TEXT NOT NULL,
                matching_posts INTEGER NOT NULL,
                checked_at TEXT NOT NULL,
                PRIMARY KEY (query_key, artist)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS count_history (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                query_key TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL,
                old_count INTEGER,
                new_count INTEGER NOT NULL,
                checked_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS processed_queries (
                query_key TEXT PRIMARY KEY,
                display_query TEXT NOT NULL,
                processed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS query_results (
                query_key TEXT NOT NULL,
                name TEXT NOT NULL,
                matching_posts INTEGER NOT NULL,
                scanned_posts INTEGER NOT NULL,
                selected_at TEXT NOT NULL,
                PRIMARY KEY (query_key, name)
            ) WITHOUT ROWID;
            """
        )
        if self.site.startswith("gelbooru:"):
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS gel_queries (
                    query_key TEXT PRIMARY KEY,
                    total_count INTEGER NOT NULL,
                    checked_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gel_query_pages (
                    query_key TEXT NOT NULL
                        REFERENCES gel_queries(query_key) ON DELETE CASCADE,
                    page_number INTEGER NOT NULL,
                    post_count INTEGER NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (query_key, page_number)
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS gel_query_page_candidates (
                    query_key TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    artist TEXT NOT NULL,
                    matching_posts INTEGER NOT NULL,
                    PRIMARY KEY (query_key, page_number, artist),
                    FOREIGN KEY (query_key, page_number)
                        REFERENCES gel_query_pages(query_key, page_number)
                        ON DELETE CASCADE
                ) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS idx_gel_page_candidates_artist
                    ON gel_query_page_candidates(artist);
                """
            )
        else:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS posts (
                    post_id TEXT PRIMARY KEY,
                    fetched_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS post_tags (
                    post_id TEXT NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
                    tag TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (post_id, tag)
                ) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS idx_post_tags_tag ON post_tags(tag);
                CREATE TABLE IF NOT EXISTS query_pages (
                    query_key TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    total_count INTEGER NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (query_key, page_number)
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS query_posts (
                    query_key TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    post_id TEXT NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
                    PRIMARY KEY (query_key, page_number, post_id)
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS query_candidates (
                    query_key TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    matching_posts_seen INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (query_key, artist)
                ) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS idx_candidates_artist
                    ON query_candidates(artist);
                """
            )
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('site',?)",
            (self.site,),
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)",
            ("3" if self.site.startswith("gelbooru:") else "2",),
        )
        self.connection.commit()
        self._post_columns = (
            {str(row[1]) for row in self.connection.execute("PRAGMA table_info(posts)")}
            if not self.site.startswith("gelbooru:")
            else set()
        )

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def store_posts(
        self,
        posts: Iterable[dict[str, Any]],
        query_key: str,
        page_number: int,
        total_count: int,
        artist_names: set[str] | None = None,
    ) -> int:
        posts = list(posts)
        if self.site.startswith("gelbooru:"):
            return self._store_gelbooru_page(
                posts, query_key, page_number, total_count, artist_names
            )
        now = utc_now()
        stored = 0
        candidate_counts: dict[str, int] = {}
        for post in posts:
            post_id = post.get("id")
            if post_id is None:
                continue
            post_id_text = str(post_id)
            raw_tags = post.get("tags", "")
            tags = raw_tags.split() if isinstance(raw_tags, str) else []
            tag_categories = post.get("_tag_categories", {})
            if not isinstance(tag_categories, dict):
                tag_categories = {}
            if "raw_json" in self._post_columns:
                # Compatibilité avec les caches v1 : ne plus recopier la
                # réponse API complète, qui n'est jamais relue.
                self.connection.execute(
                    """
                    INSERT INTO posts(
                        post_id,md5,rating,source_created_at,source_updated_at,
                        fetched_at,raw_json
                    ) VALUES(?,?,?,?,?,?, '{}')
                    ON CONFLICT(post_id) DO UPDATE SET
                        fetched_at=excluded.fetched_at,
                        raw_json='{}'
                    """,
                    (
                        post_id_text,
                        post.get("md5"),
                        post.get("rating"),
                        str(post.get("created_at", "")),
                        str(post.get("updated_at", "")),
                        now,
                    ),
                )
            else:
                self.connection.execute(
                    """
                    INSERT INTO posts(post_id,fetched_at) VALUES(?,?)
                    ON CONFLICT(post_id) DO UPDATE SET
                        fetched_at=excluded.fetched_at
                    """,
                    (post_id_text, now),
                )
            self.connection.execute("DELETE FROM post_tags WHERE post_id=?", (post_id_text,))
            self.connection.executemany(
                "INSERT OR IGNORE INTO post_tags(post_id,tag,category) VALUES(?,?,?)",
                ((post_id_text, tag, str(tag_categories.get(tag, ""))) for tag in tags),
            )
            self.connection.execute(
                """
                INSERT OR IGNORE INTO query_posts(query_key,page_number,post_id)
                VALUES(?,?,?)
                """,
                (query_key, page_number, post_id_text),
            )
            if artist_names:
                for artist in artist_names.intersection(tags):
                    candidate_counts[artist] = candidate_counts.get(artist, 0) + 1
            stored += 1
        self.connection.execute(
            """
            INSERT INTO query_pages(query_key,page_number,total_count,fetched_at)
            VALUES(?,?,?,?)
            ON CONFLICT(query_key,page_number) DO UPDATE SET
                total_count=excluded.total_count,
                fetched_at=excluded.fetched_at
            """,
            (query_key, page_number, total_count, now),
        )
        for artist, seen in candidate_counts.items():
            self.connection.execute(
                """
                INSERT INTO query_candidates(
                    query_key,artist,first_seen_at,last_seen_at,matching_posts_seen
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(query_key,artist) DO UPDATE SET
                    last_seen_at=excluded.last_seen_at,
                    matching_posts_seen=MAX(
                        query_candidates.matching_posts_seen,
                        excluded.matching_posts_seen
                    )
                """,
                (query_key, artist, now, now, seen),
            )
        self.connection.commit()
        return stored

    def _store_gelbooru_page(
        self,
        posts: list[dict[str, Any]],
        query_key: str,
        page_number: int,
        total_count: int,
        artist_names: set[str] | None,
    ) -> int:
        """Stocke uniquement le résumé relationnel utile d'une page Gelbooru."""
        now = utc_now()
        candidate_counts: dict[str, int] = {}
        valid_posts = 0
        for post in posts:
            if post.get("id") is None:
                continue
            valid_posts += 1
            raw_tags = post.get("tags", "")
            tags = raw_tags.split() if isinstance(raw_tags, str) else []
            if artist_names:
                for artist in artist_names.intersection(tags):
                    candidate_counts[artist] = candidate_counts.get(artist, 0) + 1
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO gel_queries(query_key,total_count,checked_at)
                VALUES(?,?,?)
                ON CONFLICT(query_key) DO UPDATE SET
                    total_count=excluded.total_count,
                    checked_at=excluded.checked_at
                """,
                (query_key, total_count, now),
            )
            self.connection.execute(
                "DELETE FROM gel_query_pages WHERE query_key=? AND page_number=?",
                (query_key, page_number),
            )
            self.connection.execute(
                """
                INSERT INTO gel_query_pages(
                    query_key,page_number,post_count,fetched_at
                ) VALUES(?,?,?,?)
                """,
                (query_key, page_number, valid_posts, now),
            )
            self.connection.executemany(
                """
                INSERT INTO gel_query_page_candidates(
                    query_key,page_number,artist,matching_posts
                ) VALUES(?,?,?,?)
                """,
                (
                    (query_key, page_number, artist, count)
                    for artist, count in candidate_counts.items()
                ),
            )
        return valid_posts

    def get_artist_total(self, artist: str, max_age_days: int) -> int | None:
        row = self.connection.execute(
            "SELECT total_posts,checked_at FROM artist_totals WHERE artist=?",
            (artist,),
        ).fetchone()
        if row and is_fresh(row[1], max_age_days):
            return int(row[0])
        return None

    def set_artist_total(self, artist: str, count: int) -> None:
        now = utc_now()
        old = self.connection.execute(
            "SELECT total_posts FROM artist_totals WHERE artist=?", (artist,)
        ).fetchone()
        self.connection.execute(
            """
            INSERT INTO artist_totals(artist,total_posts,checked_at) VALUES(?,?,?)
            ON CONFLICT(artist) DO UPDATE SET
                total_posts=excluded.total_posts, checked_at=excluded.checked_at
            """,
            (artist, count, now),
        )
        if old is None or int(old[0]) != count:
            self.connection.execute(
                """
                INSERT INTO count_history(
                    kind,query_key,artist,old_count,new_count,checked_at
                ) VALUES('artist_total','',?,?,?,?)
                """,
                (artist, None if old is None else int(old[0]), count, now),
            )
        self.connection.commit()

    def get_query_count(self, query_key: str, artist: str, max_age_days: int) -> int | None:
        row = self.connection.execute(
            """
            SELECT matching_posts,checked_at FROM artist_query_counts
            WHERE query_key=? AND artist=?
            """,
            (query_key, artist),
        ).fetchone()
        if row and is_fresh(row[1], max_age_days):
            return int(row[0])
        return None

    def set_query_count(self, query_key: str, artist: str, count: int) -> None:
        now = utc_now()
        old = self.connection.execute(
            """
            SELECT matching_posts FROM artist_query_counts
            WHERE query_key=? AND artist=?
            """,
            (query_key, artist),
        ).fetchone()
        self.connection.execute(
            """
            INSERT INTO artist_query_counts(
                query_key,artist,matching_posts,checked_at
            ) VALUES(?,?,?,?)
            ON CONFLICT(query_key,artist) DO UPDATE SET
                matching_posts=excluded.matching_posts,
                checked_at=excluded.checked_at
            """,
            (query_key, artist, count, now),
        )
        if old is None or int(old[0]) != count:
            self.connection.execute(
                """
                INSERT INTO count_history(
                    kind,query_key,artist,old_count,new_count,checked_at
                ) VALUES('query_count',?,?,?,?,?)
                """,
                (query_key, artist, None if old is None else int(old[0]), count, now),
            )
        self.connection.commit()

    def candidate_artists(self, query_key: str) -> set[str]:
        if self.site.startswith("gelbooru:"):
            return {
                str(row[0])
                for row in self.connection.execute(
                    """
                    SELECT DISTINCT artist
                    FROM gel_query_page_candidates
                    WHERE query_key=?
                    """,
                    (query_key,),
                )
            }
        return {
            row[0]
            for row in self.connection.execute(
                "SELECT artist FROM query_candidates WHERE query_key=?",
                (query_key,),
            )
        }

    def candidate_counts(self, query_key: str) -> dict[str, int]:
        if self.site.startswith("gelbooru:"):
            return {
                str(artist): int(count)
                for artist, count in self.connection.execute(
                    """
                    SELECT artist, SUM(matching_posts)
                    FROM gel_query_page_candidates
                    WHERE query_key=?
                    GROUP BY artist
                    """,
                    (query_key,),
                )
            }
        return {
            str(artist): int(count)
            for artist, count in self.connection.execute(
                """
                SELECT artist, matching_posts_seen
                FROM query_candidates
                WHERE query_key=?
                """,
                (query_key,),
            )
        }

    def has_query_pages(self, query_key: str, first_page: int, last_page: int) -> bool:
        if last_page < first_page:
            return True
        expected = last_page - first_page + 1
        table = "gel_query_pages" if self.site.startswith("gelbooru:") else "query_pages"
        row = self.connection.execute(
            f"""
            SELECT COUNT(DISTINCT page_number)
            FROM {table}
            WHERE query_key=? AND page_number BETWEEN ? AND ?
            """,
            (query_key, first_page, last_page),
        ).fetchone()
        return row is not None and int(row[0]) >= expected

    def query_post_count(self, query_key: str, first_page: int, last_page: int) -> int:
        if self.site.startswith("gelbooru:"):
            row = self.connection.execute(
                """
                SELECT COALESCE(SUM(post_count), 0)
                FROM gel_query_pages
                WHERE query_key=? AND page_number BETWEEN ? AND ?
                """,
                (query_key, first_page, last_page),
            ).fetchone()
            return 0 if row is None else int(row[0])
        row = self.connection.execute(
            """
            SELECT COUNT(DISTINCT post_id)
            FROM query_posts
            WHERE query_key=? AND page_number BETWEEN ? AND ?
            """,
            (query_key, first_page, last_page),
        ).fetchone()
        return 0 if row is None else int(row[0])

    def query_total_count(self, query_key: str) -> int | None:
        if not self.site.startswith("gelbooru:"):
            return None
        row = self.connection.execute(
            "SELECT total_count FROM gel_queries WHERE query_key=?",
            (query_key,),
        ).fetchone()
        return None if row is None else int(row[0])

    def next_missing_page(self, query_key: str, first_page: int = 1) -> int:
        """Retourne la première page Gelbooru absente à partir de first_page."""
        if not self.site.startswith("gelbooru:"):
            return first_page
        expected = first_page
        for (page_number,) in self.connection.execute(
            """
            SELECT page_number FROM gel_query_pages
            WHERE query_key=? AND page_number>=?
            ORDER BY page_number
            """,
            (query_key, first_page),
        ):
            page = int(page_number)
            if page < expected:
                continue
            if page > expected:
                break
            expected += 1
        return expected

    def candidates_from_same_or_stricter_queries(self, query_key: str) -> set[str]:
        """Réutilise les candidats de recherches contenant tous les tags courants."""
        current_tokens = set(query_key.split())
        if not current_tokens:
            return set()
        result: set[str] = set()
        table = "gel_queries" if self.site.startswith("gelbooru:") else "query_candidates"
        rows = self.connection.execute(f"SELECT DISTINCT query_key FROM {table}")
        for (cached_query,) in rows:
            if current_tokens.issubset(set(str(cached_query).split())):
                result.update(self.candidate_artists(str(cached_query)))
        return result

    def observed_artist_counts(self, query_key: str, category: str = "artist") -> dict[str, int]:
        """Compte les posts déjà cachés par entité pour une recherche e621."""
        return {
            str(artist): int(count)
            for artist, count in self.connection.execute(
                """
                SELECT pt.tag, COUNT(DISTINCT qp.post_id)
                FROM query_posts AS qp
                JOIN post_tags AS pt ON pt.post_id=qp.post_id
                WHERE qp.query_key=? AND pt.category=?
                GROUP BY pt.tag
                """,
                (query_key, category),
            )
        }

    def processed_queries(self) -> set[str]:
        return {
            str(row[0])
            for row in self.connection.execute("SELECT query_key FROM processed_queries")
        }

    def mark_processed_queries(self, queries: Iterable[str]) -> int:
        now = utc_now()
        rows = []
        for query in queries:
            display = " ".join(str(query).strip().split())
            key = display.casefold()
            if key:
                rows.append((key, display, now))
        before = self.connection.total_changes
        self.connection.executemany(
            """
            INSERT OR IGNORE INTO processed_queries(
                query_key,display_query,processed_at
            ) VALUES(?,?,?)
            """,
            rows,
        )
        self.connection.commit()
        return self.connection.total_changes - before

    def replace_query_results(
        self,
        query: str,
        ranked: Iterable[tuple[str, int]],
        scanned_posts: int,
    ) -> None:
        display = " ".join(query.strip().split())
        key = display.casefold()
        now = utc_now()
        with self.connection:
            self.connection.execute("DELETE FROM query_results WHERE query_key=?", (key,))
            self.connection.executemany(
                """
                INSERT INTO query_results(
                    query_key,name,matching_posts,scanned_posts,selected_at
                ) VALUES(?,?,?,?,?)
                """,
                ((key, name, int(hits), int(scanned_posts), now) for name, hits in ranked),
            )

    def check(self, mode: str = "full") -> str:
        """Vérifie le cache à la demande.

        ``quick`` omet les contrôles d'unicité et de cohérence entre les
        index et les tables. Il reste toutefois proportionnel à la taille de
        la base : aucun des deux modes ne doit donc être lancé implicitement à
        la fin de chaque scan.
        """
        pragma = {
            "quick": "PRAGMA quick_check(1)",
            "full": "PRAGMA integrity_check",
        }.get(mode)
        if pragma is None:
            raise ValueError(f"Mode de vérification SQLite inconnu : {mode}")
        return str(self.connection.execute(pragma).fetchone()[0])
