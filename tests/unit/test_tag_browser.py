import sqlite3
import tempfile
import unittest
from pathlib import Path

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
        ])
        connection.commit(); connection.close()

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
        with self.assertRaises(Exception):
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


if __name__ == "__main__":
    unittest.main()
