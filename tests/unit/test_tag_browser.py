import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from booruflow.domain.booru_sites import site_definition
from booruflow.infrastructure.gelbooru_aliases import (
    AliasRelation,
    GelbooruAliasRepository,
    ensure_alias_schema,
)
from booruflow.infrastructure.tag_browser import TagSearch, search_tags


class TagBrowserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "tags.db"
        connection = sqlite3.connect(self.database)
        connection.execute("CREATE TABLE tags(id INTEGER,name TEXT,post_count INTEGER,category INTEGER,ambiguous INTEGER)")
        connection.executemany("INSERT INTO tags VALUES(?,?,?,?,?)", [
            (1, "cat_ears", 500, 0, 0), (2, "fox_ears", 300, 0, 0),
            (3, "office_lady", 21000, 0, 0), (4, "cat_ears_artist", 20, 1, 0),
            (5, "alias_a", 10, 6, 0), (6, "alias_b", 9, 0, 0),
            (7, "canonical_tag", 8, 4, 0), (8, "ambiguous_tag", 7, 0, 1),
            (9, "old_bad_tag", 6, 6, 0), (10, "cycle_a", 5, 0, 0),
            (11, "cycle_b", 4, 0, 0),
        ])
        connection.commit(); connection.close()
        self.alias_database = Path(self.temporary.name) / "aliases.db"
        ensure_alias_schema(self.alias_database)
        aliases = GelbooruAliasRepository(self.alias_database)
        aliases.upsert(AliasRelation("alias_a", "alias_b", "active"))
        aliases.upsert(AliasRelation("alias_b", "canonical_tag", "active"))
        aliases.upsert(AliasRelation("missing_alias", "does_not_exist", "active"))
        aliases.upsert(AliasRelation("cycle_a", "cycle_b", "active"))
        aliases.upsert(AliasRelation("cycle_b", "cycle_a", "active"))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_glob_and_category_filter(self) -> None:
        rows = search_tags(self.database, TagSearch(text="*_ears", mode="glob", category=0))
        self.assertEqual([row.name for row in rows], ["cat_ears", "fox_ears"])

    def test_auto_mode_detects_wildcards(self) -> None:
        rows = search_tags(self.database, TagSearch(text="*_ears", mode="auto", category=0))
        self.assertEqual([row.name for row in rows], ["cat_ears", "fox_ears"])

    def test_regex_and_minimum_count(self) -> None:
        rows = search_tags(self.database, TagSearch(
            text=r"^(cat|fox)_ears$", mode="regex", minimum_count=400,
        ))
        self.assertEqual([row.name for row in rows], ["cat_ears"])

    def test_invalid_regex_is_reported(self) -> None:
        with self.assertRaises(re.error):
            search_tags(self.database, TagSearch(text="[", mode="regex"))

    def test_e621_schema_without_ambiguous_column_is_supported(self) -> None:
        database = Path(self.temporary.name) / "e621.db"
        connection = sqlite3.connect(database)
        connection.execute(
            "CREATE TABLE tags(id INTEGER,name TEXT,category INTEGER,post_count INTEGER,"
            "created_at TEXT,updated_at TEXT,is_locked INTEGER)"
        )
        connection.execute("INSERT INTO tags VALUES(1,'fox_ears',0,900,'','','0')")
        connection.commit(); connection.close()
        rows = search_tags(database, TagSearch(text="*_ears", mode="auto"))
        self.assertEqual([(row.name, row.ambiguous) for row in rows], [("fox_ears", 0)])

    def test_site_category_mappings_and_urls_are_distinct(self) -> None:
        gelbooru = site_definition("gelbooru")
        e621 = site_definition("e621")
        self.assertEqual(gelbooru.categories[6], "deprecated")
        self.assertEqual(e621.categories[5], "species")
        self.assertEqual(e621.categories[6], "invalid")
        self.assertNotEqual(gelbooru.categories[5], e621.categories[5])
        self.assertEqual(
            gelbooru.search_url("foo bar"),
            "https://gelbooru.com/index.php?page=post&s=list&tags=foo+bar",
        )
        self.assertEqual(e621.search_url("foo bar"), "https://e621.net/posts?tags=foo+bar")

    def test_alias_chain_is_resolved_in_batch_and_keeps_raw_name(self) -> None:
        rows = search_tags(
            self.database, TagSearch(text="alias_a", mode="exact"),
            site="gelbooru", alias_database=self.alias_database,
        )
        self.assertEqual(rows[0].name, "alias_a")
        self.assertEqual(rows[0].direct_alias, "alias_b")
        self.assertEqual(rows[0].canonical_name, "canonical_tag")

    def test_cycles_and_missing_targets_never_expose_a_canonical_target(self) -> None:
        missing = sqlite3.connect(self.database)
        missing.execute("INSERT INTO tags VALUES(12,'missing_alias',3,6,0)")
        missing.commit(); missing.close()
        rows = search_tags(
            self.database, TagSearch(text="*_a*", mode="glob"),
            site="gelbooru", alias_database=self.alias_database,
        )
        by_name = {row.name: row for row in rows}
        self.assertEqual(by_name["cycle_a"].alias_diagnostic, "cycle")
        self.assertIsNone(by_name["cycle_a"].canonical_name)
        self.assertEqual(by_name["missing_alias"].alias_diagnostic, "missing_target")
        self.assertIsNone(by_name["missing_alias"].canonical_name)

    def test_state_and_outgoing_alias_filters_can_be_combined(self) -> None:
        with_alias = search_tags(
            self.database, TagSearch(state="deprecated", alias="with"),
            site="gelbooru", alias_database=self.alias_database,
        )
        without_alias = search_tags(
            self.database, TagSearch(state="deprecated", alias="without"),
            site="gelbooru", alias_database=self.alias_database,
        )
        self.assertEqual([row.name for row in with_alias], ["alias_a"])
        self.assertEqual([row.name for row in without_alias], ["old_bad_tag"])

    def test_gelbooru_alias_database_is_never_used_for_e621(self) -> None:
        rows = search_tags(
            self.database, TagSearch(text="alias_a", mode="exact"),
            site="e621", alias_database=self.alias_database,
        )
        self.assertIsNone(rows[0].canonical_name)

    def test_large_result_batch_does_not_query_aliases_per_row(self) -> None:
        connection = sqlite3.connect(self.database)
        connection.executemany(
            "INSERT INTO tags VALUES(?,?,?,?,?)",
            [(1000 + index, f"bulk_{index}", 1000 - index, 0, 0) for index in range(1000)],
        )
        connection.commit(); connection.close()
        rows = search_tags(
            self.database, TagSearch(text="bulk_", limit=1000),
            site="gelbooru", alias_database=self.alias_database,
        )
        self.assertEqual(len(rows), 1000)


if __name__ == "__main__":
    unittest.main()
