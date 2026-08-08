#!/usr/bin/env python3
"""Reconstruit un cache Booru sans les données historiques inutilisées."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from legacy.booru_cache import BooruCache


COMMON_COPY_TABLES = (
    "artist_totals",
    "artist_query_counts",
    "count_history",
    "processed_queries",
    "query_results",
)


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Crée une copie compacte d'un cache. L'original n'est remplacé "
            "qu'avec --apply et reste alors conservé comme sauvegarde datée."
        )
    )
    parser.add_argument("cache", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reprend une destination compacte interrompue.",
    )
    return parser.parse_args()


def table_exists(connection: sqlite3.Connection, schema: str, table: str) -> bool:
    return connection.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def table_columns(
    connection: sqlite3.Connection, schema: str, table: str
) -> list[str]:
    return [
        str(row[1])
        for row in connection.execute(f"PRAGMA {schema}.table_info({table})")
    ]


def copy_table(connection: sqlite3.Connection, table: str) -> int:
    if not table_exists(connection, "source", table):
        return 0
    destination_columns = set(table_columns(connection, "main", table))
    columns = [
        column
        for column in table_columns(connection, "source", table)
        if column in destination_columns
    ]
    if not columns:
        return 0
    names = ",".join(f'"{column}"' for column in columns)
    connection.execute(
        f'INSERT INTO main."{table}"({names}) '
        f'SELECT {names} FROM source."{table}"'
    )
    return int(connection.execute("SELECT changes()").fetchone()[0])


def migrate_legacy_gelbooru(connection: sqlite3.Connection) -> None:
    """Convertit les pages v1 en résumés v3 sans recopier les posts."""
    page_filter = (
        "query_key NOT GLOB 'artist:*' "
        "AND query_key NOT GLOB 'artist_query:*'"
    )
    if (
        connection.execute("SELECT COUNT(*) FROM gel_queries").fetchone()[0]
        and not table_exists(connection, "main", "migration_progress")
        and connection.execute(
            "SELECT COUNT(*) FROM gel_query_page_candidates"
        ).fetchone()[0]
    ):
        log("Migration Gelbooru déjà complète dans la destination.")
        return
    if connection.execute("SELECT COUNT(*) FROM gel_queries").fetchone()[0] == 0:
        log("Migration des recherches et des totaux uniques...")
        connection.execute(
            f"""
            INSERT INTO gel_queries(query_key,total_count,checked_at)
            SELECT pages.query_key,
                   (
                       SELECT latest.total_count
                       FROM source.query_pages AS latest
                       WHERE latest.query_key=pages.query_key
                       ORDER BY latest.fetched_at DESC
                       LIMIT 1
                   ),
                   MAX(pages.fetched_at)
            FROM source.query_pages AS pages
            WHERE {page_filter}
            GROUP BY pages.query_key
            """
        )
        log(f"gel_queries : {connection.execute('SELECT changes()').fetchone()[0]:,}")

        log("Migration des pages et de leur nombre de posts...")
        connection.execute(
            f"""
            INSERT INTO gel_query_pages(
                query_key,page_number,post_count,fetched_at
            )
            SELECT pages.query_key,
                   pages.page_number,
                   (
                       SELECT COUNT(*)
                       FROM source.query_posts AS posts
                       WHERE posts.query_key=pages.query_key
                         AND posts.page_number=pages.page_number
                   ),
                   pages.fetched_at
            FROM source.query_pages AS pages
            WHERE {page_filter}
            """
        )
        log(f"gel_query_pages : {connection.execute('SELECT changes()').fetchone()[0]:,}")
        connection.commit()

    connection.execute(
        "CREATE TABLE IF NOT EXISTS migration_progress(query_key TEXT PRIMARY KEY)"
    )
    completed = {
        str(row[0])
        for row in connection.execute("SELECT query_key FROM migration_progress")
    }

    query_keys = [
        str(row[0])
        for row in connection.execute(
            "SELECT query_key FROM gel_queries ORDER BY query_key"
        )
    ]
    log(
        "Reconstruction exacte des occurrences par page : "
        f"{len(completed)}/{len(query_keys)} recherche(s)"
    )
    for index, query_key in enumerate(query_keys, start=1):
        if query_key in completed:
            continue
        with connection:
            connection.execute(
                """
                INSERT INTO gel_query_page_candidates(
                    query_key,page_number,artist,matching_posts
                )
                SELECT posts.query_key,
                       posts.page_number,
                       tags.tag,
                       COUNT(DISTINCT posts.post_id)
                FROM source.query_posts AS posts
                CROSS JOIN source.post_tags AS tags ON tags.post_id=posts.post_id
                CROSS JOIN source.query_candidates AS candidates
                  ON candidates.query_key=posts.query_key
                 AND candidates.artist=tags.tag
                WHERE posts.query_key=?
                GROUP BY posts.query_key,posts.page_number,tags.tag
                """,
                (query_key,),
            )
            connection.execute(
                "INSERT INTO migration_progress(query_key) VALUES(?)",
                (query_key,),
            )
        if index == 1 or index % 10 == 0 or index == len(query_keys):
            log(
                "Occurrences par page : "
                f"{index}/{len(query_keys)} recherche(s)"
            )
    total = connection.execute(
        "SELECT COUNT(*) FROM gel_query_page_candidates"
    ).fetchone()[0]
    log(f"gel_query_page_candidates : {total:,}")
    connection.execute("DROP TABLE migration_progress")
    connection.commit()


def copy_gelbooru(connection: sqlite3.Connection) -> None:
    compact_rows = (
        int(
            connection.execute(
                "SELECT COUNT(*) FROM source.gel_queries"
            ).fetchone()[0]
        )
        if table_exists(connection, "source", "gel_queries")
        else 0
    )
    has_legacy_pages = table_exists(connection, "source", "query_pages")
    if compact_rows or not has_legacy_pages:
        for table in (
            "gel_queries",
            "gel_query_pages",
            "gel_query_page_candidates",
        ):
            copied = copy_table(connection, table)
            log(f"{table} : {copied:,} ligne(s)")
    else:
        migrate_legacy_gelbooru(connection)


def main() -> int:
    args = parse_args()
    source = args.cache.resolve()
    if not source.is_file():
        raise SystemExit(f"Cache introuvable : {source}")
    output = (args.output or source.with_name(source.stem + ".compact.sqlite")).resolve()
    if output == source:
        raise SystemExit("La destination doit être distincte du cache source.")
    if output.exists() and not args.resume:
        raise SystemExit(f"Destination déjà présente : {output}")
    if args.resume and not output.exists():
        raise SystemExit(f"Aucune destination à reprendre : {output}")

    free = shutil.disk_usage(output.parent).free
    log(f"Source : {source} ({source.stat().st_size:,} octets)")
    log(f"Destination : {output}")
    log(f"Espace libre : {free:,} octets")
    if free < source.stat().st_size:
        raise SystemExit(
            "Espace libre inférieur à la taille source ; reconstruction refusée."
        )

    reader = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        site_row = reader.execute(
            "SELECT value FROM metadata WHERE key='site'"
        ).fetchone()
        site = str(site_row[0]) if site_row else "unknown"
    finally:
        reader.close()

    if not args.resume:
        BooruCache(output, site).close()
    connection = sqlite3.connect(f"file:{output.as_posix()}", uri=True)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA cache_size=-131072")
        connection.execute("PRAGMA temp_store=MEMORY")
        source_uri = f"file:{source.as_posix()}?mode=ro"
        connection.execute("ATTACH DATABASE ? AS source", (source_uri,))
        connection.execute("PRAGMA source.cache_size=-524288")
        connection.execute("PRAGMA source.mmap_size=2147483648")
        if site.startswith("gelbooru:"):
            copy_gelbooru(connection)
        else:
            copied = copy_table(connection, "posts")
            log(f"posts : {copied:,} ligne(s)")
            copied = copy_table(connection, "post_tags")
            log(f"post_tags : {copied:,} ligne(s)")
            for table in ("query_pages", "query_posts", "query_candidates"):
                copied = copy_table(connection, table)
                log(f"{table} : {copied:,} ligne(s)")
        for table in COMMON_COPY_TABLES:
            current = connection.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0]
            copied = current if current else copy_table(connection, table)
            log(f"{table} : {copied:,} ligne(s)")
        connection.commit()
        connection.execute("PRAGMA foreign_keys=ON")
        foreign_key_error = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchone()
        integrity = connection.execute("PRAGMA quick_check(1)").fetchone()[0]
        if foreign_key_error or integrity != "ok":
            raise RuntimeError(
                f"Validation refusée : foreign_keys={foreign_key_error}, "
                f"quick_check={integrity}"
            )
    except BaseException:
        connection.close()
        raise
    else:
        connection.close()

    log(f"Cache compact validé : {output} ({output.stat().st_size:,} octets)")
    if args.apply:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = source.with_name(f"{source.stem}.backup-{stamp}{source.suffix}")
        os.replace(source, backup)
        os.replace(output, source)
        log(f"Cache actif remplacé : {source}")
        log(f"Original conservé : {backup}")
    else:
        log("Original inchangé. --apply exige un accord explicite.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
