"""Offline parser compatibility checks; runnable with any supported Python and -S."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

# Direct execution also exercises the CLI's dependency-free Python environment.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from booruflow.infrastructure.gelbooru_aliases import (
    AliasPageParser,
    AliasRelation,
    GelbooruAliasRepository,
    GelbooruAliasSynchronizer,
    parse_alias_page,
)


def listing(*statuses: str) -> str:
    rows = []
    for index, status in enumerate(statuses):
        css = " class='pending-tag'" if status == "pending" else ""
        rows.append(
            f"<tr{css}><td><a href='?tags=source_{index}'>source</a>"
            f"<a href='?tags=target_{index}'>target</a></td></tr>"
        )
    return "<table>" + "".join(rows) + "</table>"


class AliasParserRuntimeTests(unittest.TestCase):
    def test_pending_last_row_closes_without_corrupting_html_buffer(self) -> None:
        for statuses in [("pending",), ("active", "pending"), ("pending", "active")]:
            with self.subTest(statuses=statuses):
                page = parse_alias_page(listing(*statuses))
                self.assertEqual(tuple(row.status for row in page.relations), statuses)

    def test_chunked_input_keeps_pending_status_and_html_buffer(self) -> None:
        text = listing("active", "pending")
        for size in (1, 7, len(text)):
            with self.subTest(chunk_size=size):
                parser = AliasPageParser()
                for start in range(0, len(text), size):
                    parser.feed(text[start : start + size])
                parser.close()
                self.assertEqual(parser.result(), parse_alias_page(text))

    def test_incremental_with_pending_tail_preserves_tags(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "aliases.db"
            repository = GelbooruAliasRepository(database)
            repository.migrate()
            for index in range(2):
                repository.upsert(
                    AliasRelation(f"source_{index}", f"target_{index}", "active", 0, index)
                )
            repository.set_state(
                {
                    "checkpoint": repository.checkpoint(),
                    "observed_last_pid": 0,
                    "sync_in_progress": True,
                    "last_successful_sync_at": "old-success",
                }
            )
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE tags(id INTEGER PRIMARY KEY,name TEXT)")
                connection.execute("INSERT INTO tags VALUES(1,'keep_me')")
            synchronizer = GelbooruAliasSynchronizer(
                database,
                lambda pid, search: listing("active", "active", "pending"),
                delay=0,
                progress=lambda message: None,
            )
            summary = synchronizer.incremental()
            self.assertEqual(summary.state, "completed")
            self.assertEqual(summary.pending, 1)
            self.assertFalse(repository.state()["sync_in_progress"])
            self.assertNotEqual(repository.state()["last_successful_sync_at"], "old-success")
            with closing(sqlite3.connect(database)) as connection, connection:
                self.assertEqual(
                    connection.execute("SELECT name FROM tags").fetchall(), [("keep_me",)]
                )
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchall(), [("ok",)])

    def test_export_resumes_after_alias_failure_without_repeating_successful_tags(self) -> None:
        from booruflow.infrastructure.gelbooru_tag_importer import IMPORT_VERSION, SCHEMA
        from booruflow.infrastructure.gelbooru_wiki_export import (
            catalogue_status,
            export_wiki_database,
        )

        for failure in (OSError, KeyboardInterrupt):
            with self.subTest(failure=failure.__name__), TemporaryDirectory() as directory:
                root = Path(directory)
                tags, aliases, output = root / "tags.db", root / "aliases.db", root / "wiki.db"
                stamp = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
                with closing(sqlite3.connect(tags)) as connection, connection:
                    connection.executescript(SCHEMA)
                    connection.executemany(
                        "INSERT INTO tags(id,name,post_count,category,ambiguous) VALUES(?,?,?,?,0)",
                        [
                            (1, "target_0", 0, 0),
                            (2, "target_1", 0, 1),
                            (3, "series", 0, 3),
                            (4, "character", 0, 4),
                        ],
                    )
                    connection.executemany(
                        "INSERT INTO import_state VALUES(?,?)",
                        [
                            ("import_version", IMPORT_VERSION),
                            ("after_id", "4"),
                            ("completed", "1"),
                            ("last_successful_update_at", stamp),
                        ],
                    )
                repository = GelbooruAliasRepository(aliases)
                repository.migrate()
                for index in range(2):
                    repository.upsert(
                        AliasRelation(f"source_{index}", f"target_{index}", "active", 0, index)
                    )
                repository.set_state(
                    {
                        "checkpoint": repository.checkpoint(),
                        "observed_last_pid": 0,
                        "last_successful_sync_at": stamp,
                    }
                )
                options = {
                    "tags_database": tags,
                    "aliases_database": aliases,
                    "minimum_rows": 1,
                    "alias_delay": 0,
                    "retries": 0,
                    "progress": lambda message: None,
                }
                export_wiki_database(output, **options)
                previous_export = output.read_bytes()
                previous_checkpoint = repository.state()["checkpoint"]

                def fail(pid: int, search: str, failure=failure) -> str:
                    raise failure("offline interrupted alias request")

                with self.assertRaises(failure):
                    export_wiki_database(
                        output,
                        "fixture-user",
                        "fixture-key",
                        **options,
                        force_update=True,
                        tag_fetcher=lambda cursor, *_: (
                            [{"id": 5, "name": "new_tag", "count": 0, "type": 0}]
                            if cursor < 5
                            else []
                        ),
                        maximum_id_fetcher=lambda *_: 5,
                        alias_fetcher=fail,
                    )
                self.assertEqual(output.read_bytes(), previous_export)
                self.assertEqual(repository.state()["checkpoint"], previous_checkpoint)
                self.assertEqual(repository.state()["last_successful_sync_at"], stamp)
                self.assertFalse(catalogue_status(aliases, "aliases", datetime.now(UTC)).fresh)
                self.assertTrue(catalogue_status(tags, "tags", datetime.now(UTC)).fresh)
                preserved_tags = tags.read_bytes()

                def forbidden(*args: object) -> None:
                    self.fail("Successful tags or fresh aliases must not be downloaded again")

                export_wiki_database(
                    output,
                    **options,
                    tag_fetcher=forbidden,
                    alias_fetcher=lambda *_: listing("active", "active", "pending"),
                )
                self.assertEqual(tags.read_bytes(), preserved_tags)
                self.assertTrue(catalogue_status(aliases, "aliases", datetime.now(UTC)).fresh)
                self.assertFalse(repository.state()["sync_in_progress"])
                export_wiki_database(
                    output,
                    **options,
                    tag_fetcher=forbidden,
                    alias_fetcher=forbidden,
                )
                with closing(sqlite3.connect(output)) as connection:
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM tags").fetchone(), (5,)
                    )
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM aliases").fetchone(), (2,)
                    )


if __name__ == "__main__":
    unittest.main()
