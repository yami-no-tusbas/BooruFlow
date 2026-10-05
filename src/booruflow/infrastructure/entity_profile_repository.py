"""Cumulative entity datasets with atomic post counting and preserved user decisions."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from time import sleep

from booruflow.application.tagging import normalize_booru_tag
from booruflow.domain.booru_sites import category_name
from booruflow.domain.entity_profiles import (
    DEFAULT_EXCLUSIONS,
    STATUSES,
    EntitySuggestion,
    automatic_exclusion,
    contextual_relation_visible,
    tag_kind,
)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class EntityProfileRepository:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=30000")
        # Switching a newly created database to WAL can return BUSY immediately
        # when two processes open it together, even with busy_timeout configured.
        for attempt in range(5):
            try:
                self.connection.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).casefold() or attempt == 4:
                    self.close()
                    raise
                sleep(.02 * (attempt + 1))
        self.connection.execute("PRAGMA foreign_keys=ON")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            self.close()
            raise RuntimeError("Entity profile database requires a newer BooruFlow")
        if version == 0:
            schema = files("booruflow.infrastructure.schema").joinpath("entity_profiles_v1.sql")
            # Seed once. Removing a default exclusion must survive reopening.
            with self.connection:
                self.connection.execute("BEGIN IMMEDIATE")
                # Another connection may have initialized the database while we waited.
                if self.connection.execute("PRAGMA user_version").fetchone()[0] == 0:
                    for statement in schema.read_text("utf-8").split(";"):
                        if statement.strip():
                            self.connection.execute(statement)
                    self.connection.executemany("INSERT INTO global_exclusions VALUES(?)",
                                                ((tag,) for tag in sorted(DEFAULT_EXCLUSIONS)))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        self.connection.close()

    def create(self, entity_tag: str, query: str) -> int:
        entity_tag = normalize_booru_tag(entity_tag)
        query = " ".join(query.split())
        if not entity_tag or not query or len(entity_tag.split()) != 1:
            raise ValueError("An entity tag and a non-empty query are required")
        with self.connection:
            self.connection.execute(
                """INSERT INTO profiles(entity_tag,query,created_at,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(entity_tag,query) DO NOTHING""", (entity_tag, query, now(), now()))
        return int(self.connection.execute(
            "SELECT id FROM profiles WHERE entity_tag=? AND query=?", (entity_tag, query)
        ).fetchone()[0])

    def profile(self, profile_id: int) -> dict:
        row = self.connection.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if row is None:
            raise LookupError("Entity profile not found")
        return dict(row)

    def profiles(self, entity_tag: str | None = None) -> list[dict]:
        sql = "SELECT * FROM profiles"
        params = ()
        if entity_tag is not None:
            sql += " WHERE entity_tag=?"
            params = (normalize_booru_tag(entity_tag),)
        return [dict(row) for row in self.connection.execute(sql + " ORDER BY id", params)]

    def seen_ids(self, profile_id: int) -> set[str]:
        return {row[0] for row in self.connection.execute(
            "SELECT post_id FROM profile_posts WHERE profile_id=?", (profile_id,))}

    def begin_run(self, profile_id: int):
        with self.connection:
            self.connection.execute(
                "UPDATE profiles SET last_run_new=0,last_run_state='running' WHERE id=?", (profile_id,))

    def finish_run(self, profile_id: int, state: str):
        with self.connection:
            self.connection.execute(
                "UPDATE profiles SET last_run_state=?,updated_at=? WHERE id=?", (state, now(), profile_id))

    def add_posts(self, profile_id: int, posts) -> int:
        """One transaction covers IDs, witnesses, counts, and run totals.

        A crash can leave a committed page, never a seen ID without its counts.
        The unique key also protects against duplicates within pages and retries.
        """
        added = 0
        source = self.profile(profile_id)["entity_tag"]
        with self.connection:
            for post in posts:
                cursor = self.connection.execute(
                    """INSERT INTO profile_posts VALUES(?,?,?)
                       ON CONFLICT(profile_id,post_id) DO NOTHING""",
                    (profile_id, post.post_id, str(post.extra.get("preview_url", ""))))
                if not cursor.rowcount:
                    continue
                tags = {normalize_booru_tag(tag) for tag in post.tags if tag.strip()}
                for tag in tags:
                    category = category_name("gelbooru", post.categories.get(tag))
                    self.connection.execute("INSERT INTO profile_post_tags VALUES(?,?,?)",
                                            (profile_id, post.post_id, tag))
                    if tag == source:
                        continue
                    self.connection.execute(
                        """INSERT INTO recurring_tags(profile_id,tag,category,kind,occurrences)
                           VALUES(?,?,?,?,1) ON CONFLICT(profile_id,tag) DO UPDATE SET
                           occurrences=occurrences+1,
                           category=CASE WHEN excluded.category='unknown' THEN category ELSE excluded.category END,
                           kind=CASE WHEN excluded.category='unknown' THEN kind ELSE excluded.kind END""",
                        (profile_id, tag, category, tag_kind(category)))
                added += 1
            self.connection.execute(
                """UPDATE profiles SET sample_count=sample_count+?,last_run_new=last_run_new+?,
                   updated_at=? WHERE id=?""", (added, added, now(), profile_id))
        return added

    def tags(self, profile_id: int) -> list[dict]:
        return [dict(row) for row in self.connection.execute(
            """SELECT t.*,CASE WHEN p.sample_count=0 THEN 0.0 ELSE
               CAST(t.occurrences AS REAL)/p.sample_count END AS frequency,
               (e.tag IS NOT NULL) AS excluded FROM recurring_tags t JOIN profiles p ON p.id=t.profile_id
               LEFT JOIN global_exclusions e ON e.tag=t.tag WHERE p.id=?
               ORDER BY occurrences DESC,t.tag""", (profile_id,))]

    def set_status(self, profile_id: int, tag: str, status: str):
        if status not in STATUSES:
            raise ValueError("Invalid entity tag status")
        with self.connection:
            self.connection.execute("UPDATE recurring_tags SET status=? WHERE profile_id=? AND tag=?",
                                    (status, profile_id, tag))

    def exclusions(self) -> list[str]:
        return [row[0] for row in self.connection.execute("SELECT tag FROM global_exclusions ORDER BY tag")]

    def exclude(self, tag: str, enabled: bool = True):
        tag = normalize_booru_tag(tag)
        if not tag:
            raise ValueError("An exclusion tag is required")
        with self.connection:
            if enabled:
                self.connection.execute("INSERT INTO global_exclusions VALUES(?) ON CONFLICT DO NOTHING", (tag,))
            else:
                self.connection.execute("DELETE FROM global_exclusions WHERE tag=?", (tag,))

    def suggestions(self, seeds: set[str], source_categories: dict | None = None) -> list[EntitySuggestion]:
        # Categories come from the existing local catalogue/source metadata.
        # Old datasets need no migration or rewrite of their historical counts.
        source_categories = source_categories or {}
        result = []
        for seed in sorted(seeds):
            for profile in self.profiles(seed):
                result.extend(EntitySuggestion(profile["id"], profile["entity_tag"], row["tag"],
                                               row["category"], row["frequency"])
                              for row in self.tags(profile["id"])
                              if row["status"] == "keep" and not row["excluded"]
                              and not automatic_exclusion(row["tag"], row["category"])
                              and contextual_relation_visible(source_categories.get(seed),
                                                              row["tag"], row["category"]))
        return result

    def matching_posts(self, profile_id: int, tag: str, limit: int = 100) -> list[dict]:
        return [dict(row) for row in self.connection.execute(
            """SELECT p.* FROM profile_posts p JOIN profile_post_tags t
               ON p.profile_id=t.profile_id AND p.post_id=t.post_id
               WHERE t.profile_id=? AND t.tag=? ORDER BY CAST(p.post_id AS INTEGER) DESC LIMIT ?""",
            (profile_id, tag, limit))]

    def review_decisions(self, item_id: int) -> dict[str, dict]:
        return {row["tag"]: dict(row) for row in self.connection.execute(
            "SELECT * FROM tagging_profile_decisions WHERE item_id=?", (item_id,))}

    def save_review_decision(self, item_id: int, tag: str, decision: str, observation_id=None):
        with self.connection:
            self.connection.execute(
                """INSERT INTO tagging_profile_decisions VALUES(?,?,?,?) ON CONFLICT(item_id,tag)
                   DO UPDATE SET decision=excluded.decision,observation_id=excluded.observation_id""",
                (item_id, tag, decision, observation_id))
