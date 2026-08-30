"""Versioned SQLite persistence for the image-analysis workflow."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Self

from booruflow.domain.image_analysis import (
    AnalysisItem,
    AnalysisState,
    ColorStatistics,
    DecisionState,
    InputKind,
    ObservationSource,
    PublishState,
    SourceReference,
    SourceTag,
    TagObservation,
    validate_transition,
)

SCHEMA_VERSION = 19


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ImageAnalysisRepository:
    def __init__(self, path: Path, *, timeout_seconds: float = 30.0) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=timeout_seconds)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute(f"PRAGMA busy_timeout={int(timeout_seconds * 1000)}")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self._migrate()

    def _migrate(self) -> None:
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"image-analysis database version {version} is newer than supported "
                f"version {SCHEMA_VERSION}"
            )
        if version == 0:
            migration = (
                files("booruflow.infrastructure.schema")
                .joinpath("image_analysis_v1.sql")
                .read_text(encoding="utf-8")
            )
            with self.connection:
                self.connection.executescript(migration)
            version = 1
        if version == 1:
            migration = (
                files("booruflow.infrastructure.schema")
                .joinpath("image_analysis_v2.sql")
                .read_text(encoding="utf-8")
            )
            with self.connection:
                self.connection.executescript(migration)
            version = 2
        if version == 2:
            migration = (
                files("booruflow.infrastructure.schema")
                .joinpath("image_analysis_v3.sql")
                .read_text(encoding="utf-8")
            )
            with self.connection:
                self.connection.executescript(migration)
            version = 3
        if version == 3:
            migration = (
                files("booruflow.infrastructure.schema")
                .joinpath("image_analysis_v4.sql")
                .read_text(encoding="utf-8")
            )
            with self.connection:
                self.connection.executescript(migration)
            version = 4
        if version == 4:
            migration = (
                files("booruflow.infrastructure.schema")
                .joinpath("image_analysis_v5.sql")
                .read_text(encoding="utf-8")
            )
            with self.connection:
                self.connection.executescript(migration)
            version = 5
        if version == 5:
            migration = (
                files("booruflow.infrastructure.schema")
                .joinpath("image_analysis_v6.sql")
                .read_text(encoding="utf-8")
            )
            with self.connection:
                self.connection.executescript(migration)
            version = 6
        if version == 6:
            migration = (
                files("booruflow.infrastructure.schema")
                .joinpath("image_analysis_v7.sql")
                .read_text(encoding="utf-8")
            )
            with self.connection:
                self.connection.executescript(migration)
            version = 7
        if version == 7:
            migration = (
                files("booruflow.infrastructure.schema")
                .joinpath("image_analysis_v8.sql")
                .read_text(encoding="utf-8")
            )
            with self.connection:
                self.connection.executescript(migration)
            version = 8
        if version == 8:
            migration = (
                files("booruflow.infrastructure.schema")
                .joinpath("image_analysis_v9.sql")
                .read_text(encoding="utf-8")
            )
            with self.connection:
                self.connection.executescript(migration)
            version = 9
        if version == 9:
            migration = (
                files("booruflow.infrastructure.schema")
                .joinpath("image_analysis_v10.sql")
                .read_text(encoding="utf-8")
            )
            with self.connection:
                self.connection.executescript(migration)
            version = 10
        if version == 10:
            migration = (
                files("booruflow.infrastructure.schema")
                .joinpath("image_analysis_v11.sql")
                .read_text(encoding="utf-8")
            )
            with self.connection:
                self.connection.executescript(migration)
            version = 11
        if version == 11:
            migration = (
                files("booruflow.infrastructure.schema")
                .joinpath("image_analysis_v12.sql")
                .read_text(encoding="utf-8")
            )
            with self.connection:
                self.connection.executescript(migration)
            version = 12
        if version == 12:
            migration = files("booruflow.infrastructure.schema").joinpath("image_analysis_v13.sql").read_text(encoding="utf-8")
            with self.connection:
                self.connection.executescript(migration)
            version = 13
        if version == 13:
            migration = files("booruflow.infrastructure.schema").joinpath("image_analysis_v14.sql").read_text(encoding="utf-8")
            with self.connection:
                self.connection.executescript(migration)
            version = 14
        if version == 14:
            migration = files("booruflow.infrastructure.schema").joinpath("image_analysis_v15.sql").read_text(encoding="utf-8")
            with self.connection:
                self.connection.executescript(migration)
            version = 15
        if version == 15:
            migration = files("booruflow.infrastructure.schema").joinpath("image_analysis_v16.sql").read_text(encoding="utf-8")
            with self.connection:
                self.connection.executescript(migration)
            version = 16
        if version == 16:
            migration = files("booruflow.infrastructure.schema").joinpath("image_analysis_v17.sql").read_text(encoding="utf-8")
            with self.connection:
                self.connection.executescript(migration)
            version = 17
        if version == 17:
            migration = files("booruflow.infrastructure.schema").joinpath("image_analysis_v18.sql").read_text(encoding="utf-8")
            with self.connection:
                self.connection.executescript(migration)
            version = 18
        if version == 18:
            columns = {
                str(row[1]) for row in self.connection.execute(
                    "PRAGMA table_info(tagging_review_batch_entries)"
                )
            }
            with self.connection:
                if "batch_visible" not in columns:
                    migration = files("booruflow.infrastructure.schema").joinpath("image_analysis_v19.sql").read_text(encoding="utf-8")
                    self.connection.executescript(migration)
                else:
                    self.connection.execute("PRAGMA user_version=19")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def add_item(
        self,
        item: AnalysisItem,
        source_tags: tuple[SourceTag, ...] = (),
        artist_tags: tuple[str, ...] = (),
        *,
        request_analysis: bool = True,
    ) -> int:
        now = utc_now()
        source = item.source
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO analysis_items(
                    input_kind,source_site,source_post_id,original_path,cached_path,
                    content_sha256,mime_type,width,height,state,last_error,
                    analysis_requested,queue_visible,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    source.kind.value,
                    source.site,
                    source.post_id,
                    str(source.original_path) if source.original_path else None,
                    str(item.cached_path) if item.cached_path else None,
                    item.content_sha256,
                    item.mime_type,
                    item.width,
                    item.height,
                    item.state.value,
                    item.last_error,
                    int(request_analysis), int(request_analysis), now, now,
                ),
            )
            item_id = int(cursor.lastrowid)
            self.connection.executemany(
                """
                INSERT INTO source_tags(item_id,site,tag_name,category,fetched_at)
                VALUES(?,?,?,?,?)
                """,
                ((item_id, tag.source.value, tag.name, tag.category, now) for tag in source_tags),
            )
            if source.site:
                self.connection.executemany(
                    """
                    INSERT INTO item_artists(item_id,site,artist_tag,provenance)
                    VALUES(?,?,?,'source_tag')
                    """,
                    ((item_id, source.site, artist) for artist in dict.fromkeys(artist_tags)),
                )
            self._insert_provenance(item_id, source, now)
        return item_id

    def _insert_provenance(
        self, item_id: int, source: SourceReference, created_at: str | None = None
    ) -> None:
        self.connection.execute(
            """INSERT OR IGNORE INTO image_provenances(
                   item_id,kind,local_path,site,post_id,created_at)
               VALUES(?,?,?,?,?,?)""",
            (
                item_id, source.kind.value,
                str(source.original_path) if source.original_path else None,
                source.site, source.post_id, created_at or utc_now(),
            ),
        )

    def item_by_sha256(self, sha256: str) -> AnalysisItem | None:
        row = self.connection.execute(
            "SELECT id FROM analysis_items WHERE content_sha256=? ORDER BY id LIMIT 1",
            (sha256,),
        ).fetchone()
        return self.get_item(int(row[0])) if row else None

    def item_by_remote_source(self, site: str, post_id: str) -> AnalysisItem | None:
        """Find canonical assets through every provenance, including hidden queue rows."""
        row = self.connection.execute(
            """SELECT item_id FROM image_provenances
               WHERE site=? AND post_id=? ORDER BY id LIMIT 1""",
            (site, str(post_id)),
        ).fetchone()
        if row is None:
            row = self.connection.execute(
                """SELECT id FROM analysis_items WHERE source_site=? AND source_post_id=?
                   ORDER BY id LIMIT 1""",
                (site, str(post_id)),
            ).fetchone()
        return self.get_item(int(row[0])) if row else None

    def tag_mapping(self, source_namespace: str, source_tag: str, target_site: str) -> str | None:
        row = self.connection.execute(
            """SELECT target_tag FROM tag_mappings
               WHERE source_namespace=? AND source_tag=? COLLATE NOCASE AND target_site=?""",
            (source_namespace, source_tag, target_site),
        ).fetchone()
        return str(row[0]) if row else None

    def set_tag_mapping(
        self, source_namespace: str, source_tag: str, target_site: str, target_tag: str
    ) -> None:
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """INSERT INTO tag_mappings(source_namespace,source_tag,target_site,target_tag,
                                              provenance,created_at,updated_at)
                   VALUES(?,?,?,?,'manual',?,?)
                   ON CONFLICT(source_namespace,source_tag,target_site) DO UPDATE SET
                     target_tag=excluded.target_tag,provenance='manual',updated_at=excluded.updated_at""",
                (source_namespace, source_tag, target_site, target_tag, now, now),
            )

    def delete_tag_mapping(self, source_namespace: str, source_tag: str, target_site: str) -> None:
        with self.connection:
            self.connection.execute(
                """DELETE FROM tag_mappings
                   WHERE source_namespace=? AND source_tag=? COLLATE NOCASE AND target_site=?""",
                (source_namespace, source_tag, target_site),
            )

    def item_queue_visible(self, item_id: int) -> bool:
        row = self.connection.execute(
            "SELECT queue_visible FROM analysis_items WHERE id=?", (item_id,)
        ).fetchone()
        return bool(row[0]) if row else False

    def make_queue_visible(self, item_id: int) -> None:
        with self.connection:
            changed = self.connection.execute(
                "UPDATE analysis_items SET queue_visible=1,updated_at=? WHERE id=?",
                (utc_now(), item_id),
            ).rowcount
        if not changed:
            raise KeyError(item_id)

    def reuse_item(
        self, item_id: int, source: SourceReference,
        source_tags: tuple[SourceTag, ...] = (), artist_tags: tuple[str, ...] = (),
        *, queue_visible: bool | None = True,
    ) -> None:
        now = utc_now(); site = source.site
        with self.connection:
            self._insert_provenance(item_id, source, now)
            if queue_visible is not None:
                self.connection.execute(
                    "UPDATE analysis_items SET queue_visible=?,updated_at=? WHERE id=?",
                    (int(queue_visible), now, item_id),
                )
            self.connection.executemany(
                """INSERT OR REPLACE INTO source_tags(item_id,site,tag_name,category,fetched_at)
                   VALUES(?,?,?,?,?)""",
                ((item_id, tag.source.value, tag.name, tag.category, now) for tag in source_tags),
            )
            if site:
                self.connection.executemany(
                    """INSERT OR IGNORE INTO item_artists(item_id,site,artist_tag,provenance)
                       VALUES(?,?,?,'source_tag')""",
                    ((item_id, site, artist) for artist in dict.fromkeys(artist_tags)),
                )

    def provenances(self, item_id: int) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            "SELECT * FROM image_provenances WHERE item_id=? ORDER BY id", (item_id,)
        ))

    def add_unresolved_remote(self, kind: InputKind, post_id: str, priority: int = 0) -> int:
        if kind not in {InputKind.GELBOORU_POST, InputKind.E621_POST}:
            raise ValueError("only remote posts can be unresolved")
        site = "gelbooru" if kind is InputKind.GELBOORU_POST else "e621"
        now = utc_now()
        with self.connection:
            existing = self.connection.execute(
                "SELECT id FROM analysis_items WHERE source_site=? AND source_post_id=?",
                (site, str(post_id)),
            ).fetchone()
            if existing:
                item_id = int(existing[0])
                self.connection.execute(
                    """UPDATE analysis_items SET queue_visible=1,analysis_requested=1,
                       priority=MAX(priority,?),updated_at=? WHERE id=?""",
                    (priority, now, item_id),
                )
                return item_id
            cursor = self.connection.execute(
                """
                INSERT INTO analysis_items(
                    input_kind,source_site,source_post_id,source_state,state,
                    analysis_requested,priority,
                    created_at,updated_at
                ) VALUES(?,?,?,'unresolved','pending',1,?,?,?)
                """,
                (kind.value, site, str(post_id), priority, now, now),
            )
        return int(cursor.lastrowid)

    def request_analysis(self, item_id: int, priority: int = 0) -> None:
        with self.connection:
            changed = self.connection.execute(
                """UPDATE analysis_items SET analysis_requested=1,
                   priority=MAX(priority,?),updated_at=? WHERE id=?""",
                (priority, utc_now(), item_id),
            ).rowcount
        if not changed:
            raise KeyError(item_id)

    def suppress_analysis_request(self, item_id: int) -> None:
        """Keep a newly imported asset canonical without scheduling the full analysis stack."""
        with self.connection:
            changed = self.connection.execute(
                "UPDATE analysis_items SET analysis_requested=0,updated_at=? WHERE id=?",
                (utc_now(), item_id),
            ).rowcount
        if not changed:
            raise KeyError(item_id)

    def resolve_item(
        self,
        item_id: int,
        item: AnalysisItem,
        source_tags: tuple[SourceTag, ...],
        artist_tags: tuple[str, ...],
    ) -> int:
        now = utc_now()
        canonical = self.item_by_sha256(str(item.content_sha256))
        if canonical is not None and canonical.id != item_id:
            self.reuse_item(canonical.id, item.source, source_tags, artist_tags)
            with self.connection:
                self.connection.execute("DELETE FROM analysis_items WHERE id=?", (item_id,))
            return canonical.id
        with self.connection:
            changed = self.connection.execute(
                """
                UPDATE analysis_items SET source_state='resolved',source_resolution_started_at=NULL,
                    cached_path=?,
                    content_sha256=?,mime_type=?,width=?,height=?,last_error=NULL,updated_at=?
                WHERE id=? AND source_state='unresolved'
                """,
                (
                    str(item.cached_path), item.content_sha256, item.mime_type,
                    item.width, item.height, now, item_id,
                ),
            ).rowcount
            if not changed:
                raise ValueError(f"item {item_id} is not unresolved")
            self.connection.executemany(
                """INSERT INTO source_tags(item_id,site,tag_name,category,fetched_at)
                   VALUES(?,?,?,?,?)""",
                ((item_id, tag.source.value, tag.name, tag.category, now) for tag in source_tags),
            )
            site = item.source.site
            self.connection.executemany(
                """INSERT INTO item_artists(item_id,site,artist_tag,provenance)
                   VALUES(?,?,?,'source_tag')""",
                ((item_id, site, artist) for artist in dict.fromkeys(artist_tags)),
            )
            self._insert_provenance(item_id, item.source, now)
        return item_id

    def fail_source(self, item_id: int, error: str) -> None:
        with self.connection:
            self.connection.execute(
                """UPDATE analysis_items SET source_state='failed',state='failed',
                   source_resolution_started_at=NULL,
                   last_error=?,updated_at=? WHERE id=?""",
                (error, utc_now(), item_id),
            )

    def claim_sources_to_resolve(self, limit: int) -> list[int]:
        if limit < 1:
            return []
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            active = int(self.connection.execute(
                """SELECT COUNT(*) FROM analysis_items
                   WHERE source_state='unresolved'
                     AND analysis_requested=1
                     AND source_resolution_started_at IS NOT NULL"""
            ).fetchone()[0])
            available = max(0, limit - active)
            rows = self.connection.execute(
                """SELECT id FROM analysis_items
                   WHERE source_state='unresolved'
                     AND analysis_requested=1
                     AND source_resolution_started_at IS NULL
                   ORDER BY created_at,id LIMIT ?""",
                (available,),
            ).fetchall()
            ids = [int(row[0]) for row in rows]
            self.connection.executemany(
                """UPDATE analysis_items SET source_resolution_started_at=?,updated_at=?
                   WHERE id=?""",
                ((now, now, item_id) for item_id in ids),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return ids

    def release_source_claim(self, item_id: int) -> None:
        with self.connection:
            self.connection.execute(
                """UPDATE analysis_items SET source_resolution_started_at=NULL,updated_at=?
                   WHERE id=? AND source_state='unresolved'""",
                (utc_now(), item_id),
            )

    def get_item(self, item_id: int) -> AnalysisItem | None:
        row = self.connection.execute(
            "SELECT * FROM analysis_items WHERE id=?", (item_id,)
        ).fetchone()
        if row is None:
            return None
        source = SourceReference(
            InputKind(row["input_kind"]),
            Path(row["original_path"]) if row["original_path"] else None,
            row["source_site"],
            row["source_post_id"],
        )
        return AnalysisItem(
            source=source,
            state=AnalysisState(row["state"]),
            id=int(row["id"]),
            cached_path=Path(row["cached_path"]) if row["cached_path"] else None,
            content_sha256=row["content_sha256"],
            mime_type=row["mime_type"],
            width=row["width"],
            height=row["height"],
            last_error=row["last_error"],
        )

    def source_tags(self, item_id: int) -> tuple[SourceTag, ...]:
        return tuple(
            SourceTag(str(row["tag_name"]), ObservationSource(row["site"]), row["category"])
            for row in self.connection.execute(
                "SELECT site,tag_name,category FROM source_tags WHERE item_id=? ORDER BY tag_name",
                (item_id,),
            )
        )

    def replace_source_metadata(
        self, item_id: int, site: str, source_tags: tuple[SourceTag, ...], artists: tuple[str, ...]
    ) -> None:
        """Atomically refresh remote metadata without touching analyses or decisions."""
        now = utc_now()
        with self.connection:
            self.connection.execute("DELETE FROM source_tags WHERE item_id=? AND site=?", (item_id, site))
            self.connection.executemany(
                """INSERT INTO source_tags(item_id,site,tag_name,category,fetched_at)
                   VALUES(?,?,?,?,?)""",
                ((item_id, site, tag.name, tag.category, now) for tag in source_tags),
            )
            self.connection.execute(
                "DELETE FROM item_artists WHERE item_id=? AND site=? AND provenance='source_tag'",
                (item_id, site),
            )
            self.connection.executemany(
                """INSERT INTO item_artists(item_id,site,artist_tag,provenance)
                   VALUES(?,?,?,'source_tag')""",
                ((item_id, site, artist) for artist in dict.fromkeys(artists)),
            )

    def link_local_source(self, item_id: int, site: str, post_id: str, confidence: str) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT OR IGNORE INTO local_source_links(
                       item_id,site,post_id,confidence,detected_at)
                   VALUES(?,?,?,?,?)""",
                (item_id, site, post_id, confidence, utc_now()),
            )
            kind=InputKind.GELBOORU_POST if site=="gelbooru" else InputKind.E621_POST
            self._insert_provenance(item_id,SourceReference(kind,site=site,post_id=post_id))

    def cached_post_metadata(self, site: str, post_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM post_metadata_cache WHERE site=? AND post_id=?",
            (site, post_id),
        ).fetchone()

    def cache_post_metadata(
        self, site: str, post_id: str, file_url: str,
        tags: tuple[SourceTag, ...], artist_tags: tuple[str, ...],
    ) -> None:
        serialized_tags = json.dumps([
            {"name": tag.name, "category": tag.category} for tag in tags
        ], ensure_ascii=False)
        with self.connection:
            self.connection.execute(
                """INSERT OR REPLACE INTO post_metadata_cache(
                       site,post_id,fetched_at,state,file_url,tags_json,artist_tags_json,last_error)
                   VALUES(?,?,?,'resolved',?,?,?,NULL)""",
                (site, post_id, utc_now(), file_url, serialized_tags,
                 json.dumps(list(artist_tags), ensure_ascii=False)),
            )

    def cache_missing_post(self, site: str, post_id: str, error: str) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT OR REPLACE INTO post_metadata_cache(
                       site,post_id,fetched_at,state,tags_json,artist_tags_json,last_error)
                   VALUES(?,?,?,'missing','[]','[]',?)""",
                (site, post_id, utc_now(), error),
            )

    def apply_local_enrichment(
        self, item_id: int, site: str, post_id: str,
        tags: tuple[SourceTag, ...], artist_tags: tuple[str, ...],
    ) -> None:
        now = utc_now()
        with self.connection:
            self.connection.executemany(
                """INSERT OR REPLACE INTO source_tags(item_id,site,tag_name,category,fetched_at)
                   VALUES(?,?,?,?,?)""",
                ((item_id, site, tag.name, tag.category, now) for tag in tags),
            )
            self.connection.executemany(
                """INSERT OR IGNORE INTO item_artists(item_id,site,artist_tag,provenance)
                   VALUES(?,?,?,'source_tag')""",
                ((item_id, site, artist) for artist in dict.fromkeys(artist_tags)),
            )
            self.connection.executemany(
                """UPDATE tag_observations SET source_present=1
                   WHERE item_id=? AND tag_name=?""",
                ((item_id, tag.name) for tag in tags),
            )
            self.connection.execute(
                """UPDATE local_source_links SET enrichment_state='resolved',last_error=NULL,
                   enriched_at=? WHERE item_id=? AND site=? AND post_id=?""",
                (now, item_id, site, post_id),
            )

    def fail_local_enrichment(self, item_id: int, error: str) -> None:
        with self.connection:
            self.connection.execute(
                """UPDATE local_source_links SET enrichment_state='failed',last_error=?
                   WHERE item_id=?""",
                (error, item_id),
            )

    def artist_tags(self, item_id: int) -> tuple[str, ...]:
        return tuple(
            str(row[0])
            for row in self.connection.execute(
                "SELECT artist_tag FROM item_artists WHERE item_id=? ORDER BY artist_tag",
                (item_id,),
            )
        )

    def artist_associations(self,item_id:int)->list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT site,artist_tag,provenance FROM item_artists WHERE item_id=? ORDER BY site,artist_tag,provenance",(item_id,))]

    def item_artist_identities(self,item_id:int)->tuple[tuple[str,str],...]:
        return tuple((str(row[0]),str(row[1])) for row in self.connection.execute("SELECT site,artist_tag FROM item_artists WHERE item_id=? ORDER BY site,artist_tag",(item_id,)))

    def set_cached_path(self,item_id:int,path:Path|None)->None:
        with self.connection:self.connection.execute("UPDATE analysis_items SET cached_path=?,updated_at=? WHERE id=?",(str(path) if path else None,utc_now(),item_id))

    def record_filename_metadata(self,item_id:int,path:Path,artist:str,post_id:str,rating:str,source_md5:str,site:str,state:str,conflict_reason:str|None=None)->None:
        with self.connection:
            self.connection.execute(
                """INSERT OR REPLACE INTO local_filename_metadata(
                       item_id,local_path,artist_tag,post_id,rating,source_md5,site,state,
                       conflict_reason,parsed_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (item_id,str(path),artist,post_id,rating,source_md5,site,state,conflict_reason,utc_now()),
            )

    def assign_artist(
        self, item_ids: list[int] | tuple[int, ...], site: str, artist_tag: str,
        provenance: str = "manual",
    ) -> int:
        """Explicitly associate canonical items; profile dirty triggers handle rebuilds."""
        unique=tuple(dict.fromkeys(int(value) for value in item_ids));tag=artist_tag.strip()
        if not unique or not tag or site not in {"local","gelbooru","e621"}:return 0
        with self.connection:
            cursor=self.connection.executemany(
                """INSERT OR IGNORE INTO item_artists(item_id,site,artist_tag,provenance)
                   SELECT id,?,?,? FROM analysis_items WHERE id=?""",
                ((site,tag,provenance,item_id) for item_id in unique),
            )
        return max(0,int(cursor.rowcount))

    def repair_structured_artist_associations(self) -> dict[str,int]:
        """Backfill only artist identities explicitly supplied by cached booru metadata."""
        repaired={"gelbooru":0,"e621":0}
        rows=self.connection.execute(
            """SELECT DISTINCT p.item_id,p.site,m.artist_tags_json
               FROM image_provenances p JOIN post_metadata_cache m
                 ON m.site=p.site AND m.post_id=p.post_id
               WHERE m.state='resolved' AND p.site IN ('gelbooru','e621')"""
        )
        for row in rows:
            try:artists=tuple(str(value).strip() for value in json.loads(str(row["artist_tags_json"])) if str(value).strip())
            except (TypeError,ValueError):artists=()
            repaired[str(row["site"])]+=self.assign_artist([int(row["item_id"])],str(row["site"]),artists[0],"source_tag") if len(artists)==1 else 0
            if len(artists)>1:
                for artist in artists:repaired[str(row["site"])]+=self.assign_artist([int(row["item_id"])],str(row["site"]),artist,"source_tag")
        return repaired

    def repair_gelbooru_tag_categories(self, category_lookup) -> int:
        """Reclassify persisted Gelbooru tags using the configured authoritative catalogue."""
        repaired=0
        rows=list(self.connection.execute(
            """SELECT DISTINCT p.item_id FROM image_provenances p
               WHERE p.site='gelbooru' AND NOT EXISTS(
                   SELECT 1 FROM item_artists a WHERE a.item_id=p.item_id AND a.site='gelbooru')"""
        ))
        for row in rows:
            item_id=int(row[0]);names=tuple(str(value[0]) for value in self.connection.execute("SELECT tag_name FROM source_tags WHERE item_id=? AND site='gelbooru'",(item_id,)))
            categories=category_lookup(names)
            with self.connection:
                self.connection.executemany("UPDATE source_tags SET category=? WHERE item_id=? AND site='gelbooru' AND tag_name=?",((category,item_id,name) for name,category in categories.items()))
            for artist in (name for name,category in categories.items() if category=="artist"):
                repaired+=self.assign_artist([item_id],"gelbooru",artist,"source_tag")
        return repaired

    def unassigned_artist_diagnostics(self) -> list[dict]:
        """Explain every resolved canonical image lacking an artist association."""
        items=self.connection.execute(
            """SELECT id,content_sha256,cached_path FROM analysis_items i
               WHERE source_state='resolved' AND cached_path IS NOT NULL
                 AND NOT EXISTS(SELECT 1 FROM item_artists a WHERE a.item_id=i.id)
               ORDER BY id"""
        )
        result=[]
        for item in items:
            provenances=[dict(row) for row in self.provenances(int(item["id"]))]
            remote=[row for row in provenances if row.get("site") and row.get("post_id")]
            source_tags=[{"site":tag.source.value,"name":tag.name,"category":tag.category} for tag in self.source_tags(int(item["id"]))]
            metadata=[]
            for source in remote:
                cached=self.cached_post_metadata(str(source["site"]),str(source["post_id"]))
                if cached:metadata.append(dict(cached))
            artists=[]
            for cached in metadata:
                try:artists.extend(str(value) for value in json.loads(str(cached["artist_tags_json"])))
                except (TypeError,ValueError):pass
            artists.extend(value["name"] for value in source_tags if value["category"]=="artist")
            metadata_available=bool(metadata or source_tags)
            if remote and not metadata_available:reason="remote_metadata_not_resolved"
            elif remote and artists:reason="remote_artist_tag_not_extracted"
            elif remote:reason="remote_metadata_without_artist"
            elif provenances and all(row.get("kind")=="local_file" for row in provenances):reason="local_only_no_artist_metadata"
            else:reason="unknown"
            result.append({"item_id":int(item["id"]),"sha":str(item["content_sha256"] or "")[:12],"cached_path":str(item["cached_path"]),"provenances":provenances,"source_tags":source_tags,"metadata_available":metadata_available,"metadata_cache_available":bool(metadata),"metadata_artists":tuple(dict.fromkeys(artists)),"reason":reason})
        return result

    def transition(self, item_id: int, target: AnalysisState, error: str | None = None) -> None:
        row = self.connection.execute(
            "SELECT state FROM analysis_items WHERE id=?", (item_id,)
        ).fetchone()
        if row is None:
            raise KeyError(item_id)
        current = AnalysisState(row[0])
        validate_transition(current, target)
        now = utc_now()
        values: dict[str, object] = {
            "state": target.value,
            "updated_at": now,
            "last_error": error,
        }
        if target is AnalysisState.PROCESSING:
            values.update(processing_started_at=now, processing_heartbeat_at=now)
        elif target is AnalysisState.READY_FOR_REVIEW:
            values["ready_at"] = now
        elif target is AnalysisState.REVIEWED:
            values["reviewed_at"] = now
        assignments = ",".join(f"{key}=?" for key in values)
        with self.connection:
            self.connection.execute(
                f"UPDATE analysis_items SET {assignments} WHERE id=?",
                (*values.values(), item_id),
            )

    def claim_next(self, analysis_prefetch: int | None = None) -> AnalysisItem | None:
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """SELECT id FROM analysis_items
                   WHERE state='pending' AND source_state='resolved'
                     AND analysis_requested=1 AND priority>=100
                   ORDER BY priority DESC,created_at,id LIMIT 1"""
            ).fetchone()
            if row is None and analysis_prefetch is not None:
                ahead = int(
                    self.connection.execute(
                        """
                        SELECT COUNT(*) FROM analysis_items
                        WHERE queue_visible=1 AND (
                            state='processing'
                            OR (state='ready_for_review' AND review_active=0)
                        )
                        """
                    ).fetchone()[0]
                )
                if ahead >= analysis_prefetch:
                    self.connection.commit()
                    return None
            if row is None:
                row = self.connection.execute(
                """
                SELECT id FROM analysis_items
                WHERE state='pending' AND source_state='resolved' AND analysis_requested=1
                ORDER BY priority DESC, created_at, id LIMIT 1
                """
                ).fetchone()
            if row is None:
                self.connection.commit()
                return None
            item_id = int(row[0])
            changed = self.connection.execute(
                """
                UPDATE analysis_items
                SET state='processing', attempt_count=attempt_count+1,
                    processing_started_at=?, processing_heartbeat_at=?, updated_at=?
                WHERE id=? AND state='pending'
                """,
                (now, now, now, item_id),
            ).rowcount
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_item(item_id) if changed else None

    def scheduler_diagnostic(self, analysis_prefetch: int) -> dict[str, object]:
        counts = self.connection.execute(
            """SELECT
               SUM(state='pending') pending,
               SUM(state='processing' AND queue_visible=1) processing,
               SUM(state='ready_for_review' AND review_active=0 AND queue_visible=1) ready_ahead,
               SUM(review_active=1) review_active
               FROM analysis_items WHERE analysis_requested=1"""
        ).fetchone()
        eligible = int(self.connection.execute(
            """SELECT COUNT(*) FROM analysis_items WHERE analysis_requested=1
               AND state='pending' AND source_state='resolved'"""
        ).fetchone()[0])
        interactive = int(self.connection.execute(
            """SELECT COUNT(*) FROM analysis_items WHERE analysis_requested=1
               AND state='pending' AND source_state='resolved' AND priority>=100"""
        ).fetchone()[0])
        ahead = int(counts[1] or 0) + int(counts[2] or 0)
        reason = "interactive_eligible" if interactive else (
            "prefetch_limit" if eligible and ahead >= analysis_prefetch else
            "eligible_pending" if eligible else "no_eligible_pending_item"
        )
        exclusions = [dict(row) for row in self.connection.execute(
            """SELECT id,queue_visible,analysis_requested,source_state,priority
               FROM analysis_items WHERE state='pending'
                 AND NOT (analysis_requested=1 AND source_state='resolved')
               ORDER BY priority DESC,id LIMIT 5"""
        )]
        candidate_ids = tuple(int(row[0]) for row in self.connection.execute(
            """SELECT id FROM analysis_items WHERE analysis_requested=1
               AND state='pending' ORDER BY priority DESC,id LIMIT 20"""
        ))
        return {
            "pending": int(counts[0] or 0), "processing": int(counts[1] or 0),
            "ready_ahead": int(counts[2] or 0), "review_active": int(counts[3] or 0),
            "analysis_prefetch": analysis_prefetch, "eligible": eligible,
            "interactive": interactive, "reason": reason, "exclusions": exclusions,
            "candidate_ids": candidate_ids,
        }

    def heartbeat(self, item_id: int) -> None:
        with self.connection:
            changed = self.connection.execute(
                """UPDATE analysis_items SET processing_heartbeat_at=?,updated_at=?
                   WHERE id=? AND state='processing'""",
                (utc_now(), utc_now(), item_id),
            ).rowcount
        if not changed:
            raise ValueError(f"item {item_id} is not processing")

    def recover_interrupted(self, stale_before: str | None = None) -> int:
        now = utc_now()
        condition = "state='processing'"
        condition_parameters: list[object] = []
        if stale_before is not None:
            condition += " AND (processing_heartbeat_at IS NULL OR processing_heartbeat_at < ?)"
            condition_parameters.append(stale_before)
        with self.connection:
            item_ids = [
                int(row[0])
                for row in self.connection.execute(
                    f"SELECT id FROM analysis_items WHERE {condition}", condition_parameters
                )
            ]
            if not item_ids:
                return 0
            placeholders = ",".join("?" for _item_id in item_ids)
            self.connection.execute(
                f"""
                UPDATE analysis_items
                SET state='pending', processing_started_at=NULL,
                    processing_heartbeat_at=NULL, updated_at=?,
                    last_error='Interrupted before completion'
                WHERE id IN ({placeholders})
                """,
                (now, *item_ids),
            )
            self.connection.execute(
                f"""UPDATE model_runs SET state='interrupted',finished_at=?,
                   error='Worker interrupted' WHERE state='running'
                   AND item_id IN ({placeholders})""",
                (now, *item_ids),
            )
        return len(item_ids)

    def queue_counts(self) -> dict[str, int]:
        counts = {
            str(state): int(count)
            for state, count in self.connection.execute(
                "SELECT state,COUNT(*) FROM analysis_items WHERE queue_visible=1 GROUP BY state"
            )
        }
        counts["unresolved"] = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM analysis_items WHERE source_state='unresolved' AND queue_visible=1"
            ).fetchone()[0]
        )
        counts["resolved"] = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM analysis_items WHERE source_state='resolved' AND queue_visible=1"
            ).fetchone()[0]
        )
        counts["ready_ahead"] = int(
            self.connection.execute(
                """SELECT COUNT(*) FROM analysis_items
                   WHERE state='ready_for_review' AND review_active=0 AND queue_visible=1"""
            ).fetchone()[0]
        )
        return counts

    def list_items(self) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            """SELECT analysis_items.*,local_source_links.site AS linked_site,
                      local_source_links.post_id AS linked_post_id,
                      local_source_links.enrichment_state,local_source_links.last_error AS enrichment_error,
                      (SELECT GROUP_CONCAT(artist_tag, ', ')
                       FROM item_artists WHERE item_id=analysis_items.id) AS artist_tags
               FROM analysis_items
               LEFT JOIN local_source_links ON local_source_links.item_id=analysis_items.id
               WHERE analysis_items.queue_visible=1
               ORDER BY analysis_items.id"""
        ))

    def tagging_pool(self, item_ids: list[int] | tuple[int, ...] | None = None) -> list[sqlite3.Row]:
        """Canonical items available to Tagging; no queue mutation or model request."""
        parameters: tuple[object, ...] = ()
        condition = ""
        if item_ids is not None:
            values = tuple(dict.fromkeys(int(item_id) for item_id in item_ids))
            if not values:
                return []
            condition = f"WHERE i.id IN ({','.join('?' for _ in values)})"
            parameters = values
        return list(self.connection.execute(
            f"""SELECT i.id,i.state,i.analysis_requested,i.queue_visible,i.cached_path,
                       i.source_site,i.source_post_id,i.review_active,
                       b.publish_state,b.reviewed_at
                FROM analysis_items i JOIN tagging_pool_items p ON p.item_id=i.id
                LEFT JOIN tagging_review_batch_entries b ON b.item_id=i.id {condition}
                ORDER BY i.updated_at DESC,i.id DESC""",
            parameters,
        ))

    def add_to_tagging_pool(self, item_ids: list[int] | tuple[int, ...], source: str) -> int:
        values = tuple(dict.fromkeys(int(item_id) for item_id in item_ids)); now = utc_now()
        with self.connection:
            self.connection.executemany("INSERT OR IGNORE INTO tagging_pool_items(item_id,source,added_at) VALUES(?,?,?)", ((item_id, source, now) for item_id in values))
        return len(values)

    @staticmethod
    def _stable_tags(values: list[str] | tuple[str, ...]) -> list[str]:
        return sorted({str(value).strip() for value in values if str(value).strip()})

    def save_review_batch_entry(
        self,
        item_id: int,
        *,
        original_tags: list[str] | tuple[str, ...],
        additions: list[str] | tuple[str, ...],
        removals: list[str] | tuple[str, ...],
        reviewed_final_tags: list[str] | tuple[str, ...],
    ) -> PublishState:
        """Create or update the one durable publication-review snapshot for an item.

        A local item has no speculative remote target and remains ``reviewed``.
        A remote item returns to ``pending_publish`` only when its desired final
        tags differ from the snapshot saved by its last successful publication.
        """
        item = self.connection.execute(
            "SELECT source_site,source_post_id FROM analysis_items WHERE id=?", (item_id,)
        ).fetchone()
        if item is None:
            raise KeyError(item_id)
        site = str(item["source_site"]) if item["source_site"] else None
        post_id = str(item["source_post_id"]) if item["source_post_id"] else None
        default_state = (
            PublishState.PENDING_PUBLISH if site is not None and post_id is not None
            else PublishState.REVIEWED
        )
        stable_final_tags = self._stable_tags(reviewed_final_tags)
        previous = self.connection.execute(
            """SELECT publish_state,published_final_tags_json,published_verified_at
               FROM tagging_review_batch_entries WHERE item_id=?""",
            (item_id,),
        ).fetchone()
        published_final_tags = None
        if (
            previous is not None
            and previous["published_final_tags_json"] is not None
            and previous["published_verified_at"] is not None
        ):
            published_final_tags = self._stable_tags(
                json.loads(previous["published_final_tags_json"])
            )
        state = default_state
        if site is not None and post_id is not None and published_final_tags is not None:
            state = (
                PublishState.PUBLISHED
                if stable_final_tags == published_final_tags
                else PublishState.PENDING_PUBLISH
            )
        snapshot = (
            json.dumps(self._stable_tags(original_tags)),
            json.dumps(self._stable_tags(additions)),
            json.dumps(self._stable_tags(removals)),
            json.dumps(stable_final_tags),
        )
        with self.connection:
            self.connection.execute(
                """INSERT INTO tagging_review_batch_entries(
                       item_id,site,post_id,original_tags_json,additions_json,
                       removals_json,reviewed_final_tags_json,reviewed_at,publish_state
                   ) VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(item_id) DO UPDATE SET
                       site=excluded.site,post_id=excluded.post_id,
                       original_tags_json=excluded.original_tags_json,
                       additions_json=excluded.additions_json,
                       removals_json=excluded.removals_json,
                       reviewed_final_tags_json=excluded.reviewed_final_tags_json,
                       reviewed_at=excluded.reviewed_at,publish_state=excluded.publish_state,
                       batch_visible=1""",
                (item_id, site, post_id, *snapshot, utc_now(), state.value),
            )
        return state

    def batch_entry(self, item_id: int) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT * FROM tagging_review_batch_entries WHERE item_id=?", (item_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "item_id": int(row["item_id"]), "site": row["site"], "post_id": row["post_id"],
            "original_tags": json.loads(row["original_tags_json"]),
            "additions": json.loads(row["additions_json"]),
            "removals": json.loads(row["removals_json"]),
            "reviewed_final_tags": json.loads(row["reviewed_final_tags_json"]),
            "reviewed_at": str(row["reviewed_at"]),
            "publish_state": PublishState(str(row["publish_state"])),
            "publish_attempts": int(row["publish_attempts"]),
            "last_error": row["last_error"],
            "last_attempt_at": row["last_attempt_at"],
            "published_at": row["published_at"],
            "published_verified_at": row["published_verified_at"],
            "published_final_tags": (
                json.loads(row["published_final_tags_json"])
                if row["published_final_tags_json"] is not None else None
            ),
        }

    def list_batch_entries(
        self, publish_state: PublishState | None = None
    ) -> list[dict[str, object]]:
        condition = " WHERE batch_visible=1"
        if publish_state is not None:
            condition += " AND publish_state=?"
        parameters = (publish_state.value,) if publish_state is not None else ()
        rows = self.connection.execute(
            f"SELECT item_id FROM tagging_review_batch_entries{condition} "
            "ORDER BY CASE publish_state "
            "WHEN 'pending_publish' THEN 0 WHEN 'failed' THEN 1 "
            "WHEN 'reviewed' THEN 2 WHEN 'published' THEN 3 ELSE 4 END, "
            "reviewed_at DESC,item_id DESC", parameters,
        )
        return [entry for row in rows if (entry := self.batch_entry(int(row["item_id"]))) is not None]

    def remove_batch_entry(self, item_id: int) -> bool:
        with self.connection:
            row = self.connection.execute(
                "SELECT publish_state FROM tagging_review_batch_entries WHERE item_id=?",
                (item_id,),
            ).fetchone()
            if row is not None and row["publish_state"] == PublishState.PUBLISHED.value:
                return bool(self.connection.execute(
                    "UPDATE tagging_review_batch_entries SET batch_visible=0 WHERE item_id=?",
                    (item_id,),
                ).rowcount)
            return bool(self.connection.execute(
                "DELETE FROM tagging_review_batch_entries WHERE item_id=?", (item_id,)
            ).rowcount)

    def reviewed_remote_post_ids(self) -> set[int]:
        rows = self.connection.execute(
            "SELECT post_id FROM tagging_review_batch_entries "
            "WHERE site='gelbooru' AND post_id IS NOT NULL"
        )
        return {int(row["post_id"]) for row in rows}

    def update_publish_state(self, item_id: int, state: PublishState) -> None:
        now = utc_now()
        with self.connection:
            if state is PublishState.PUBLISHED:
                changed = self.connection.execute(
                    """UPDATE tagging_review_batch_entries
                       SET publish_state=?,
                           published_final_tags_json=reviewed_final_tags_json,
                           published_at=COALESCE(published_at, ?),
                           published_verified_at=COALESCE(published_verified_at, ?)
                       WHERE item_id=?""",
                    (state.value, now, now, item_id),
                ).rowcount
            else:
                changed = self.connection.execute(
                    "UPDATE tagging_review_batch_entries SET publish_state=? WHERE item_id=?",
                    (state.value, item_id),
                ).rowcount
        if not changed:
            raise KeyError(item_id)

    def begin_publish_attempt(self, item_id: int) -> int:
        """Atomically reserve one remote attempt before its fresh fetch and POST."""
        with self.connection:
            changed = self.connection.execute(
                """UPDATE tagging_review_batch_entries
                   SET publish_state=?, publish_attempts=publish_attempts+1,
                       last_attempt_at=?, last_error=NULL
                   WHERE item_id=? AND publish_state=?""",
                (PublishState.PUBLISHING.value, utc_now(), item_id, PublishState.PENDING_PUBLISH.value),
            ).rowcount
        if not changed:
            raise ValueError(f"batch entry {item_id} is not pending publication")
        entry = self.batch_entry(item_id)
        assert entry is not None
        return int(entry["publish_attempts"])

    def publish_succeeded(self, item_id: int) -> None:
        now = utc_now()
        with self.connection:
            changed = self.connection.execute(
                """UPDATE tagging_review_batch_entries
                   SET publish_state=?, published_at=?, last_error=NULL,
                       published_final_tags_json=reviewed_final_tags_json,
                       published_verified_at=?
                   WHERE item_id=? AND publish_state=?""",
                (
                    PublishState.PUBLISHED.value, now, now, item_id,
                    PublishState.PUBLISHING.value,
                ),
            ).rowcount
        if not changed:
            raise ValueError(f"batch entry {item_id} is not publishing")

    def publish_failed(self, item_id: int, error: str) -> None:
        with self.connection:
            changed = self.connection.execute(
                """UPDATE tagging_review_batch_entries SET publish_state=?, last_error=?
                   WHERE item_id=? AND publish_state=?""",
                (PublishState.FAILED.value, str(error)[:2000], item_id, PublishState.PUBLISHING.value),
            ).rowcount
        if not changed:
            raise ValueError(f"batch entry {item_id} is not publishing")

    def publish_deferred(self, item_id: int, error: str) -> None:
        """Return a globally blocked, pre-submit attempt directly to the retryable queue."""
        with self.connection:
            changed = self.connection.execute(
                """UPDATE tagging_review_batch_entries SET publish_state=?, last_error=?
                   WHERE item_id=? AND publish_state=?""",
                (
                    PublishState.PENDING_PUBLISH.value, str(error)[:2000], item_id,
                    PublishState.PUBLISHING.value,
                ),
            ).rowcount
        if not changed:
            raise ValueError(f"batch entry {item_id} is not publishing")

    def recover_interrupted_publishes(self) -> int:
        """Make a crash-interrupted request retryable; remote success remains unknown."""
        with self.connection:
            return self.connection.execute(
                """UPDATE tagging_review_batch_entries SET publish_state=?,
                   last_error=COALESCE(last_error || ' · ', '') || 'Publication interrompue avant confirmation; à vérifier puis réessayer.'
                   WHERE publish_state=?""",
                (PublishState.PENDING_PUBLISH.value, PublishState.PUBLISHING.value),
            ).rowcount

    def retry_failed_publishes(self, item_ids: object) -> int:
        values = tuple(dict.fromkeys(int(item_id) for item_id in item_ids))
        if not values:
            return 0
        placeholders = ",".join("?" for _ in values)
        with self.connection:
            return self.connection.execute(
                f"UPDATE tagging_review_batch_entries SET publish_state=? WHERE publish_state=? AND item_id IN ({placeholders})",
                (PublishState.PENDING_PUBLISH.value, PublishState.FAILED.value, *values),
            ).rowcount

    def next_tagging_pool_item(
        self, current_item_id: int, scope: str = "all"
    ) -> sqlite3.Row | None:
        conditions = ["p.item_id<>?", "b.item_id IS NULL", "i.state IN ('ready_for_review','reviewed')"]
        if scope == "remote":
            conditions.append("i.source_site IS NOT NULL")
        elif scope == "local":
            conditions.append("i.source_site IS NULL")
        elif scope != "all":
            raise ValueError(f"unknown tagging pool scope: {scope}")
        return self.connection.execute(
            f"""SELECT i.id,i.source_site,i.source_post_id,i.state
                FROM tagging_pool_items p JOIN analysis_items i ON i.id=p.item_id
                LEFT JOIN tagging_review_batch_entries b ON b.item_id=i.id
                WHERE {' AND '.join(conditions)}
                ORDER BY p.added_at,i.id LIMIT 1""",
            (current_item_id,),
        ).fetchone()

    def activate_next_review(self) -> AnalysisItem | None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            active = self.connection.execute(
                "SELECT id FROM analysis_items WHERE review_active=1"
            ).fetchone()
            if active is not None:
                self.connection.commit()
                return self.get_item(int(active[0]))
            row = self.connection.execute(
                """SELECT id FROM analysis_items WHERE state='ready_for_review' AND queue_visible=1
                   ORDER BY ready_at,id LIMIT 1"""
            ).fetchone()
            if row is None:
                self.connection.commit()
                return None
            item_id = int(row[0])
            self.connection.execute(
                "UPDATE analysis_items SET review_active=1,updated_at=? WHERE id=?",
                (utc_now(), item_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_item(item_id)

    def active_review(self) -> AnalysisItem | None:
        row = self.connection.execute(
            "SELECT id FROM analysis_items WHERE review_active=1"
        ).fetchone()
        return self.get_item(int(row[0])) if row is not None else None

    def activate_review(self, item_id: int) -> AnalysisItem:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT state FROM analysis_items WHERE id=?", (item_id,)
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            if row["state"] != AnalysisState.READY_FOR_REVIEW.value:
                raise ValueError("only a ready item can become the active review")
            self.connection.execute("UPDATE analysis_items SET review_active=0 WHERE review_active=1")
            self.connection.execute(
                "UPDATE analysis_items SET review_active=1,updated_at=? WHERE id=?",
                (utc_now(), item_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        item = self.get_item(item_id)
        if item is None:
            raise KeyError(item_id)
        return item

    def requeue_skipped(self, item_id: int) -> None:
        self.requeue_skipped_many([item_id])

    def requeue_skipped_many(self, item_ids: list[int] | tuple[int, ...]) -> int:
        """Reopen skipped reviews without scheduling any model backend."""
        values = tuple(dict.fromkeys(int(item_id) for item_id in item_ids))
        if not values:
            return 0
        placeholders = ",".join("?" for _item_id in values)
        with self.connection:
            return self.connection.execute(
                f"""UPDATE analysis_items SET state='ready_for_review',queue_visible=1,
                    review_active=0,updated_at=? WHERE state='skipped' AND id IN ({placeholders})""",
                (utc_now(), *values),
            ).rowcount

    def clean_queue(self, mode: str) -> tuple[int, int]:
        conditions = {
            "reviewed": "state='reviewed'",
            "skipped": "state='skipped'",
            "finished": "state IN ('reviewed','skipped')",
            "active": "state IN ('pending','ready_for_review','failed') AND review_active=0",
        }
        if mode not in conditions:
            raise ValueError(f"unknown queue cleanup mode: {mode}")
        with self.connection:
            changed = self.connection.execute(
                f"UPDATE analysis_items SET queue_visible=0,updated_at=? "
                f"WHERE queue_visible=1 AND {conditions[mode]}",
                (utc_now(),),
            ).rowcount
        retained = int(self.connection.execute(
            """SELECT COUNT(*) FROM analysis_items WHERE queue_visible=1
               AND (state='processing' OR review_active=1)"""
        ).fetchone()[0])
        return changed, retained

    def finish_review(self, item_id: int, target: AnalysisState) -> None:
        if target not in {AnalysisState.REVIEWED, AnalysisState.SKIPPED}:
            raise ValueError("review can only be completed or skipped")
        self.transition(item_id, target)
        with self.connection:
            self.connection.execute(
                "UPDATE analysis_items SET review_active=0 WHERE id=?", (item_id,)
            )

    def retry(self, item_id: int) -> None:
        self.request_analysis(item_id)
        self.transition(item_id, AnalysisState.PENDING)

    def begin_model_run(
        self, item_id: int, backend: str, name: str, version: str, config_hash: str,
        runtime: str = "", device: str = "",
    ) -> int | None:
        existing = self.connection.execute(
            """SELECT id,state FROM model_runs WHERE item_id=? AND backend=?
               AND model_name=? AND model_version=? AND configuration_hash=?""",
            (item_id, backend, name, version, config_hash),
        ).fetchone()
        if existing is not None and existing["state"] == "completed":
            return None
        now = utc_now()
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO model_runs(
                       item_id,backend,model_name,model_version,configuration_hash,
                       state,started_at,finished_at,error,runtime,device)
                   VALUES(?,?,?,?,?,'running',?,NULL,NULL,?,?)
                   ON CONFLICT(item_id,backend,model_name,model_version,configuration_hash)
                   DO UPDATE SET state='running',started_at=excluded.started_at,
                                 finished_at=NULL,error=NULL,runtime=excluded.runtime,
                                 device=excluded.device
                   RETURNING id""",
                (item_id, backend, name, version, config_hash, now, runtime, device),
            )
            run_id = int(cursor.fetchone()[0])
        return run_id

    def save_statistics(
        self, item_id: int, run_id: int, statistics: ColorStatistics
    ) -> None:
        now = utc_now()
        palette = json.dumps(list(statistics.dominant_colors), separators=(",", ":"))
        with self.connection:
            self.connection.execute(
                """INSERT INTO image_statistics(
                       item_id,model_run_id,mean_saturation,mean_luminance,
                       luminance_stddev,contrast,pastel_score,dominant_colors_json)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(model_run_id) DO UPDATE SET
                       mean_saturation=excluded.mean_saturation,
                       mean_luminance=excluded.mean_luminance,
                       luminance_stddev=excluded.luminance_stddev,
                       contrast=excluded.contrast,pastel_score=excluded.pastel_score,
                       dominant_colors_json=excluded.dominant_colors_json""",
                (
                    item_id, run_id, statistics.mean_saturation,
                    statistics.mean_luminance, statistics.luminance_stddev,
                    statistics.contrast, statistics.pastel_score, palette,
                ),
            )
            self.connection.execute(
                """UPDATE model_runs SET state='completed',finished_at=?,error=NULL
                   WHERE id=?""",
                (now, run_id),
            )

    def save_embedding(
        self, item_id: int, run_id: int, vector: bytes, dimensions: int,
        dtype: str = "float32", normalized: bool = True,
    ) -> None:
        if dimensions < 1 or not vector or not dtype.strip():
            raise ValueError("invalid embedding payload")
        with self.connection:
            self.connection.execute(
                """INSERT INTO embeddings(item_id,model_run_id,vector,dimensions,dtype,normalized)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(item_id,model_run_id) DO UPDATE SET
                     vector=excluded.vector,dimensions=excluded.dimensions,
                     dtype=excluded.dtype,normalized=excluded.normalized""",
                (item_id, run_id, vector, dimensions, dtype, int(normalized)),
            )
            self.connection.execute(
                "UPDATE model_runs SET state='completed',finished_at=?,error=NULL WHERE id=?",
                (utc_now(), run_id),
            )

    def embedding(self, item_id: int, backend: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """SELECT e.*,r.backend,r.model_name,r.model_version,
                      r.configuration_hash,r.runtime,r.device,r.finished_at
               FROM embeddings e JOIN model_runs r ON r.id=e.model_run_id
               WHERE e.item_id=? AND r.backend=? AND r.state='completed'
               ORDER BY r.finished_at DESC,r.id DESC LIMIT 1""",
            (item_id, backend),
        ).fetchone()

    def embedding_for_identity(
        self, item_id: int, backend: str, model_name: str,
        model_version: str, configuration_hash: str,
    ) -> sqlite3.Row | None:
        return self.connection.execute(
            """SELECT e.*,r.backend,r.model_name,r.model_version,
                      r.configuration_hash,r.runtime,r.device,r.finished_at
               FROM embeddings e JOIN model_runs r ON r.id=e.model_run_id
               WHERE e.item_id=? AND r.backend=? AND r.model_name=?
                 AND r.model_version=? AND r.configuration_hash=? AND r.state='completed'
               LIMIT 1""",
            (item_id, backend, model_name, model_version, configuration_hash),
        ).fetchone()

    def embeddings_for_artist(self, site: str, artist_tag: str) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            """SELECT DISTINCT e.item_id,e.vector,e.dimensions,e.dtype,e.normalized,
                      r.backend,r.model_name,r.model_version,r.configuration_hash,
                      r.runtime,r.device,r.finished_at
               FROM item_artists a JOIN embeddings e ON e.item_id=a.item_id
               JOIN model_runs r ON r.id=e.model_run_id
               WHERE a.site=? AND a.artist_tag=? COLLATE NOCASE AND r.state='completed'
               ORDER BY r.backend,e.item_id""",
            (site, artist_tag),
        ))

    def artist_profile_inputs(self, site: str, artist_tag: str) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            """SELECT DISTINCT i.id AS item_id,s.mean_saturation,s.mean_luminance,
                      s.contrast,s.pastel_score
               FROM item_artists a JOIN analysis_items i ON i.id=a.item_id
               LEFT JOIN image_statistics s ON s.model_run_id=(
                   SELECT model_run_id FROM image_statistics
                   WHERE item_id=i.id ORDER BY model_run_id DESC LIMIT 1)
               WHERE a.site=? AND a.artist_tag=? COLLATE NOCASE ORDER BY i.id""",
            (site, artist_tag),
        ))

    def artist_tag_frequencies(
        self, site: str, artist_tag: str,
    ) -> tuple[dict[str, int], dict[str, int]]:
        source = self.connection.execute(
            """SELECT s.tag_name,COUNT(DISTINCT s.item_id) AS count
               FROM item_artists a JOIN source_tags s ON s.item_id=a.item_id
               WHERE a.site=? AND a.artist_tag=? COLLATE NOCASE
               GROUP BY s.tag_name ORDER BY s.tag_name""",
            (site, artist_tag),
        )
        accepted = self.connection.execute(
            """SELECT COALESCE(o.reviewed_name,o.tag_name) AS tag_name,
                      COUNT(DISTINCT o.item_id) AS count
               FROM item_artists a JOIN tag_observations o ON o.item_id=a.item_id
               WHERE a.site=? AND a.artist_tag=? COLLATE NOCASE
                 AND o.source='wd14' AND o.decision='accepted'
               GROUP BY COALESCE(o.reviewed_name,o.tag_name) ORDER BY tag_name""",
            (site, artist_tag),
        )
        return (
            {str(row[0]): int(row[1]) for row in source},
            {str(row[0]): int(row[1]) for row in accepted},
        )

    def list_artist_identities(self) -> list[tuple[str, str]]:
        return [(str(row[0]), str(row[1])) for row in self.connection.execute(
            "SELECT DISTINCT site,artist_tag FROM item_artists ORDER BY site,artist_tag"
        )]

    def embeddable_item_ids(self) -> list[int]:
        return [int(row[0]) for row in self.connection.execute(
            """SELECT id FROM analysis_items
               WHERE source_state='resolved' AND cached_path IS NOT NULL ORDER BY id"""
        )]

    def similar_corpus_summary(self) -> dict[str, int]:
        eligible = int(self.connection.execute(
            "SELECT COUNT(DISTINCT item_id) FROM item_artists"
        ).fetchone()[0])
        total = int(self.connection.execute(
            """SELECT COUNT(*) FROM analysis_items
               WHERE source_state='resolved' AND cached_path IS NOT NULL"""
        ).fetchone()[0])
        embedded = int(self.connection.execute(
            """SELECT COUNT(DISTINCT e.item_id) FROM embeddings e
               WHERE EXISTS(SELECT 1 FROM item_artists a WHERE a.item_id=e.item_id)"""
        ).fetchone()[0])
        return {"images_eligible": eligible, "images_skipped": max(0, total - eligible),
                "embeddings_missing": max(0, eligible - embedded)}

    def embedding_counts(self) -> dict[str, int]:
        return {str(row[0]): int(row[1]) for row in self.connection.execute(
            """SELECT r.backend,COUNT(DISTINCT e.item_id)
               FROM embeddings e JOIN model_runs r ON r.id=e.model_run_id
               WHERE r.state='completed' GROUP BY r.backend ORDER BY r.backend"""
        )}

    def artist_image_rows(self, site: str, artist_tag: str) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            """SELECT DISTINCT i.id,i.cached_path FROM item_artists a
               JOIN analysis_items i ON i.id=a.item_id
               WHERE a.site=? AND a.artist_tag=? COLLATE NOCASE
                 AND i.cached_path IS NOT NULL ORDER BY i.id""",
            (site, artist_tag),
        ))

    def save_artist_profile(
        self, site: str, artist_tag: str, profile_version: str,
        dependency_hash: str, embedding_versions: dict[str, str],
        image_count: int, profile_json: str,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO artist_profiles(
                     site,artist_tag,profile_version,dependency_hash,
                     source_embedding_versions_json,image_count,profile_json,dirty,built_at)
                   VALUES(?,?,?,?,?,?,?,0,?)
                   ON CONFLICT(site,artist_tag,profile_version) DO UPDATE SET
                     dependency_hash=excluded.dependency_hash,
                     source_embedding_versions_json=excluded.source_embedding_versions_json,
                     image_count=excluded.image_count,profile_json=excluded.profile_json,
                     dirty=0,built_at=excluded.built_at""",
                (site, artist_tag, profile_version, dependency_hash,
                 json.dumps(embedding_versions, sort_keys=True), image_count,
                 profile_json, utc_now()),
            )

    def create_library_job(self,job_id:str,roots:list[str])->None:
        now=utc_now()
        with self.connection:self.connection.execute("INSERT INTO library_index_jobs(id,roots_json,state,created_at,updated_at) VALUES(?,?,'pending',?,?)",(job_id,json.dumps(roots,ensure_ascii=False),now,now))

    def library_job(self,job_id:str)->dict|None:
        row=self.connection.execute("SELECT * FROM library_index_jobs WHERE id=?",(job_id,)).fetchone();return dict(row) if row else None

    def update_library_job(self,job_id:str,**values)->None:
        allowed={"state","detected","scanned","imported","duplicates","invalid","metadata_parsed","artists_found","last_path","last_error"};changes={key:value for key,value in values.items() if key in allowed}
        if not changes:return
        changes["updated_at"]=utc_now();columns=", ".join(f"{key}=?" for key in changes)
        with self.connection:self.connection.execute(f"UPDATE library_index_jobs SET {columns} WHERE id=?",(*changes.values(),job_id))

    def resumable_library_jobs(self)->list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM library_index_jobs WHERE state IN ('pending','running','paused','failed') ORDER BY created_at")]

    def library_path_processed(self,job_id:str,path:Path)->bool:
        return bool(self.connection.execute("SELECT 1 FROM library_index_paths WHERE job_id=? AND path=?",(job_id,str(path))).fetchone())

    def record_library_path(self,job_id:str,path:Path,item_id:int|None,outcome:str)->None:
        stored=item_id if item_id is not None and self.connection.execute("SELECT 1 FROM analysis_items WHERE id=?",(item_id,)).fetchone() else None
        with self.connection:self.connection.execute("INSERT OR REPLACE INTO library_index_paths(job_id,path,item_id,outcome,processed_at) VALUES(?,?,?,?,?)",(job_id,str(path),stored,outcome,utc_now()))

    def library_match_counts(self,job_id:str)->dict[str,int]:
        rows=self.connection.execute("SELECT outcome,COUNT(*) count FROM library_index_paths WHERE job_id=? GROUP BY outcome",(job_id,))
        return {str(row["outcome"]):int(row["count"]) for row in rows}

    def local_binary_duplicates(self)->list[dict]:
        rows=self.connection.execute("""SELECT i.id,i.content_sha256,i.cached_path
            FROM analysis_items i WHERE (SELECT COUNT(*) FROM image_provenances p
            WHERE p.item_id=i.id AND p.kind='local_file')>1 ORDER BY i.id""")
        result=[]
        for row in rows:
            paths=[str(value[0]) for value in self.connection.execute("SELECT local_path FROM image_provenances WHERE item_id=? AND kind='local_file' ORDER BY local_path",(row["id"],))]
            result.append({"item_id":int(row["id"]),"sha256":str(row["content_sha256"] or ""),"thumbnail":str(row["cached_path"] or paths[0]),"paths":paths})
        return result

    def touch_remote_artist(self,site:str,artist_tag:str,*,used:bool=False)->None:
        now=utc_now()
        with self.connection:self.connection.execute(
            """INSERT INTO remote_artist_state(site,artist_tag,last_seen_at,last_used_at)
               VALUES(?,?,?,?) ON CONFLICT(site,artist_tag) DO UPDATE SET
               last_seen_at=excluded.last_seen_at,
               last_used_at=CASE WHEN excluded.last_used_at IS NULL THEN remote_artist_state.last_used_at ELSE excluded.last_used_at END""",
            (site,artist_tag,now,now if used else None))

    def artist_collection_state(self,site:str,artist_tag:str)->str:
        local=bool(self.connection.execute("""SELECT 1 FROM item_artists a JOIN image_provenances p ON p.item_id=a.item_id WHERE a.site=? AND a.artist_tag=? COLLATE NOCASE AND p.kind='local_file' LIMIT 1""",(site,artist_tag)).fetchone())
        remote=bool(self.connection.execute("""SELECT 1 FROM item_artists a JOIN image_provenances p ON p.item_id=a.item_id WHERE a.site=? AND a.artist_tag=? COLLATE NOCASE AND p.site IS NOT NULL LIMIT 1""",(site,artist_tag)).fetchone())
        return "mixed" if local and remote else "collection" if local else "remote_only" if remote else "metadata_only"

    def preview_remote_profile_purge(self,unused_before:str)->dict:
        identities=[(str(row[0]),str(row[1])) for row in self.connection.execute("""SELECT r.site,r.artist_tag FROM remote_artist_state r WHERE r.protected=0 AND COALESCE(r.last_used_at,r.last_seen_at)<? AND NOT EXISTS(SELECT 1 FROM item_artists a JOIN image_provenances p ON p.item_id=a.item_id WHERE a.site=r.site AND a.artist_tag=r.artist_tag COLLATE NOCASE AND p.kind='local_file')""",(unused_before,))]
        embeddings=0
        for site,tag in identities:embeddings+=int(self.connection.execute("SELECT COUNT(*) FROM embeddings e WHERE EXISTS(SELECT 1 FROM item_artists a WHERE a.item_id=e.item_id AND a.site=? AND a.artist_tag=? COLLATE NOCASE)",(site,tag)).fetchone()[0])
        return {"identities":identities,"profiles":len(identities),"embeddings":embeddings}

    def purge_remote_profiles(self,identities:list[tuple[str,str]])->dict:
        removed_profiles=removed_embeddings=0
        with self.connection:
            for site,tag in identities:
                cursor=self.connection.execute("DELETE FROM artist_profiles WHERE site=? AND artist_tag=? COLLATE NOCASE",(site,tag));removed_profiles+=max(0,cursor.rowcount)
                item_ids=[int(row[0]) for row in self.connection.execute("""SELECT DISTINCT a.item_id FROM item_artists a WHERE a.site=? AND a.artist_tag=? COLLATE NOCASE AND NOT EXISTS(SELECT 1 FROM image_provenances p WHERE p.item_id=a.item_id AND p.kind='local_file')""",(site,tag))]
                for item_id in item_ids:
                    cursor=self.connection.execute("DELETE FROM embeddings WHERE item_id=?",(item_id,));removed_embeddings+=max(0,cursor.rowcount)
        return {"profiles":removed_profiles,"embeddings":removed_embeddings}

    def artist_profile_row(
        self, site: str, artist_tag: str, profile_version: str,
    ) -> sqlite3.Row | None:
        return self.connection.execute(
            """SELECT * FROM artist_profiles WHERE site=? AND artist_tag=? COLLATE NOCASE
               AND profile_version=?""",
            (site, artist_tag, profile_version),
        ).fetchone()

    def list_profile_rows(self, profile_version: str) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            "SELECT * FROM artist_profiles WHERE profile_version=? ORDER BY site,artist_tag",
            (profile_version,),
        ))

    def fail_model_run(self, run_id: int, error: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE model_runs SET state='failed',finished_at=?,error=? WHERE id=?",
                (utc_now(), error, run_id),
            )

    def complete_model_run(self, run_id: int) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE model_runs SET state='completed',finished_at=?,error=NULL WHERE id=?",
                (utc_now(), run_id),
            )

    def save_tag_predictions(
        self,
        item_id: int,
        run_id: int,
        predictions: list[tuple[str, str, float]],
        source: ObservationSource = ObservationSource.WD14,
    ) -> int:
        source_names = {
            str(row[0]).strip().casefold().replace(" ", "_")
            for row in self.connection.execute(
                "SELECT tag_name FROM source_tags WHERE item_id=?", (item_id,)
            )
        }
        now = utc_now()
        rows = []
        for raw_name, category, confidence in predictions:
            raw = str(raw_name).strip()
            normalized = raw.replace(" ", "_")
            rows.append((
                item_id, run_id, normalized, source.value, float(confidence),
                category, raw, int(normalized.casefold() in source_names), now,
            ))
        with self.connection:
            self.connection.execute(
                "DELETE FROM tag_observations WHERE model_run_id=?", (run_id,)
            )
            self.connection.executemany(
                """INSERT INTO tag_observations(
                       item_id,model_run_id,tag_name,source,confidence,decision,
                       category,raw_tag_name,source_present,created_at)
                   VALUES(?,?,?,?,?,'unreviewed',?,?,?,?)""",
                rows,
            )
            self.connection.execute(
                "UPDATE model_runs SET state='completed',finished_at=?,error=NULL WHERE id=?",
                (now, run_id),
            )
        return len(rows)

    def statistics(self, item_id: int) -> ColorStatistics | None:
        row = self.connection.execute(
            """SELECT s.* FROM image_statistics AS s
               JOIN model_runs AS r ON r.id=s.model_run_id
               WHERE s.item_id=? ORDER BY r.finished_at DESC,r.id DESC LIMIT 1""",
            (item_id,),
        ).fetchone()
        if row is None:
            return None
        return ColorStatistics(
            tuple(json.loads(row["dominant_colors_json"])),
            float(row["mean_saturation"]), float(row["mean_luminance"]),
            float(row["luminance_stddev"]), float(row["contrast"]),
            float(row["pastel_score"]),
        )

    def add_manual_observation(self, item_id: int, name: str) -> int:
        now = utc_now()
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO tag_observations(
                       item_id,tag_name,source,confidence,decision,created_at,reviewed_at)
                   VALUES(?,?,'manual',NULL,'accepted',?,?)""",
                (item_id, name.strip(), now, now),
            )
        return int(cursor.lastrowid)

    def observations(self, item_id: int) -> list[tuple[int, TagObservation]]:
        return [
            (
                int(row["id"]),
                TagObservation(
                    name=str(row["tag_name"]), source=ObservationSource(row["source"]),
                    confidence=row["confidence"], decision=DecisionState(row["decision"]),
                    reviewed_name=row["reviewed_name"], category=row["category"],
                    raw_tag_name=row["raw_tag_name"], source_present=bool(row["source_present"]),
                ),
            )
            for row in self.connection.execute(
                "SELECT * FROM tag_observations WHERE item_id=? ORDER BY id", (item_id,)
            )
        ]

    def set_existing_tag_decision(self, item_id: int, tag_name: str, decision: str) -> None:
        if decision not in {"keep", "remove"}:
            raise ValueError("existing tag decisions are keep or remove")
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """INSERT INTO tag_review_entries(item_id,tag_name,origin,decision,created_at,updated_at)
                   VALUES(?,?, 'existing',?,?,?) ON CONFLICT(item_id,tag_name,origin)
                   DO UPDATE SET decision=excluded.decision,updated_at=excluded.updated_at""",
                (item_id, tag_name, decision, now, now),
            )

    def existing_tag_decision(self, item_id: int, tag_name: str) -> str:
        """Return the current logical decision, including the implicit KEEP default."""
        row = self.connection.execute(
            """SELECT decision FROM tag_review_entries
               WHERE item_id=? AND tag_name=? AND origin='existing'""",
            (item_id, tag_name),
        ).fetchone()
        return str(row["decision"]) if row is not None else "keep"

    def tag_review_summary(self, item_id: int, original_tags: list[str] | tuple[str, ...]) -> dict[str, list[str]]:
        decisions = {str(row["tag_name"]): str(row["decision"]) for row in self.connection.execute("SELECT tag_name,decision FROM tag_review_entries WHERE item_id=? AND origin='existing'", (item_id,))}
        original = sorted(dict.fromkeys(original_tags))
        removals = sorted(tag for tag in original if decisions.get(tag) == "remove")
        additions = []
        for _observation_id, observation in self.observations(item_id):
            if observation.decision is DecisionState.ACCEPTED and observation.source in {ObservationSource.WD14, ObservationSource.MANUAL}:
                additions.append(observation.reviewed_name or observation.name)
        additions = sorted(set(additions) - set(original))
        return {"original_tags": original, "additions": additions, "removals": removals, "final_tags": sorted((set(original) - set(removals)) | set(additions))}

    def decide_observation(
        self, observation_id: int, decision: DecisionState, reviewed_name: str | None = None
    ) -> None:
        with self.connection:
            changed = self.connection.execute(
                """UPDATE tag_observations SET decision=?,reviewed_name=?,reviewed_at=?
                   WHERE id=?""",
                (decision.value, reviewed_name, utc_now(), observation_id),
            ).rowcount
        if not changed:
            raise KeyError(observation_id)

    def wd14_validation_summary(self) -> dict[str, object]:
        rows = list(self.connection.execute(
            """SELECT confidence,decision FROM tag_observations
               WHERE source='wd14' AND decision IN ('accepted','rejected')"""
        ))
        accepted = sum(row["decision"] == "accepted" for row in rows)
        bins = ((0.10, 0.20), (0.20, 0.30), (0.30, 0.40), (0.40, 0.50), (0.50, 0.70), (0.70, 1.01))
        by_confidence = []
        for low, high in bins:
            selected = [row for row in rows if low <= float(row["confidence"] or 0) < high]
            selected_accepted = sum(row["decision"] == "accepted" for row in selected)
            by_confidence.append({
                "range": f"{low:.2f}-{min(high, 1.0):.2f}", "reviewed": len(selected),
                "accepted": selected_accepted,
                "rate": selected_accepted / len(selected) if selected else 0.0,
            })
        return {
            "reviewed": len(rows), "accepted": accepted, "rejected": len(rows) - accepted,
            "rate": accepted / len(rows) if rows else 0.0, "bins": by_confidence,
        }

    def register_worker(self, worker_id: str, process_id: int) -> None:
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """INSERT INTO worker_sessions(
                       id,process_id,state,started_at,heartbeat_at,stopped_at,last_error)
                   VALUES(?,?,'running',?,?,NULL,NULL)
                   ON CONFLICT(id) DO UPDATE SET process_id=excluded.process_id,
                       state='running',started_at=excluded.started_at,
                       heartbeat_at=excluded.heartbeat_at,stopped_at=NULL,last_error=NULL""",
                (worker_id, process_id, now, now),
            )

    def worker_heartbeat(self, worker_id: str) -> None:
        with self.connection:
            self.connection.execute(
                """UPDATE worker_sessions SET heartbeat_at=?
                   WHERE id=? AND state='running'""",
                (utc_now(), worker_id),
            )

    def stop_worker(self, worker_id: str, error: str | None = None) -> None:
        state = "failed" if error else "stopped"
        with self.connection:
            self.connection.execute(
                """UPDATE worker_sessions SET state=?,stopped_at=?,heartbeat_at=?,last_error=?
                   WHERE id=?""",
                (state, utc_now(), utc_now(), error, worker_id),
            )
