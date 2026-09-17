import sqlite3
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from booruflow.infrastructure.gelbooru_tag_importer import decode_name, fetch_page, rebuild_database


class GelbooruTagImporterTests(unittest.TestCase):
    def test_names_are_repeatedly_html_decoded(self) -> None:
        self.assertEqual(decode_name("dragon&amp;#039;s_crown"), "dragon's_crown")
        self.assertEqual(decode_name("tom_&amp;_jerry"), "tom_&_jerry")
        self.assertEqual(decode_name("say_&quot;hello&quot;"), 'say_"hello"')
        self.assertEqual(decode_name("hex_&#x27;apostrophe"), "hex_'apostrophe")
        self.assertEqual(decode_name("\ufeffpurple_swim_trunks"), "purple_swim_trunks")
        self.assertEqual(decode_name("witch\u200b"), "witch")

    def test_live_request_forces_ascending_id_order(self) -> None:
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def read(self): return b'{"tag": []}'
        captured = []
        def fake_open(request, timeout):
            captured.append(request.full_url); return Response()
        with patch("urllib.request.urlopen", fake_open):
            fetch_page(123, "1", "key")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(captured[0]).query)
        self.assertEqual(query["after_id"], ["123"])
        self.assertEqual(query["orderby"], ["id"])
        self.assertEqual(query["order"], ["asc"])

    def test_rebuild_uses_increasing_after_id_and_activates_valid_database(self) -> None:
        calls = []
        pages = {
            0: [{"id": 2, "name": "office_lady", "count": 21000, "type": 0}],
            2: [{"id": 7, "name": "witch", "count": 100, "type": 0}],
            7: [],
        }

        def fetcher(after_id, _user_id, _api_key):
            calls.append(after_id)
            return pages[after_id]

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "tags.db"
            summary = rebuild_database(
                target, "1", "key", fetcher=fetcher, progress=lambda _line: None,
                minimum_rows=2, required_tags=("office_lady", "witch"), maximum_id=7,
            )
            self.assertEqual(calls, [0, 2, 7, 7])
            self.assertEqual(summary.rows, 2)
            connection = sqlite3.connect(target)
            self.assertEqual(connection.execute(
                "SELECT post_count,category FROM tags WHERE name='office_lady'"
            ).fetchone(), (21000, 0))
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            connection.close()

    def test_existing_destination_is_backed_up(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "tags.db"
            target.write_bytes(b"old database")
            pages = {0: [{"id": 1, "name": "office_lady", "count": 50, "type": 0}], 1: []}
            summary = rebuild_database(
                target, "1", "key", fetcher=lambda cursor, *_args: pages[cursor],
                progress=lambda _line: None, minimum_rows=1, required_tags=("office_lady",), maximum_id=1,
            )
            self.assertIsNotNone(summary.backup)
            self.assertEqual(summary.backup.read_bytes(), b"old database")

    def test_duplicate_ids_are_updated_not_inserted_twice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "tags.db"
            calls = 0
            def fetcher(cursor, *_args):
                nonlocal calls
                calls += 1
                if calls == 1: return [{"id": 1, "name": "office_lady", "count": 10, "type": 0}]
                if calls == 2: return [{"id": 1, "name": "office_lady", "count": 20, "type": 0}, {"id": 2, "name": "witch", "count": 30, "type": 0}]
                return []
            summary = rebuild_database(
                target, "1", "key", fetcher=fetcher, progress=lambda _line: None,
                minimum_rows=2, required_tags=("office_lady", "witch"), maximum_id=2,
            )
            self.assertEqual(summary.rows, 2)
            connection = sqlite3.connect(target)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*),MAX(post_count) FROM tags WHERE id=1"
            ).fetchone(), (1, 20))
            connection.close()

    def test_duplicate_names_keep_highest_count_and_are_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "tags.db"
            pages = {
                0: [
                    {"id": 1, "name": "office_lady", "count": 0, "type": 0},
                    {"id": 2, "name": "office_lady", "count": 21000, "type": 0},
                    {"id": 3, "name": "witch", "count": 100, "type": 0},
                    {"id": 4, "name": "&nbsp;", "count": 0, "type": 0},
                ],
                4: [],
            }
            rebuild_database(
                target, "1", "key", fetcher=lambda cursor, *_args: pages[cursor],
                progress=lambda _line: None, minimum_rows=2,
                required_tags=("office_lady", "witch"), maximum_id=4,
            )
            connection = sqlite3.connect(target)
            self.assertEqual(connection.execute(
                "SELECT id,post_count FROM tags WHERE name='office_lady'"
            ).fetchone(), (2, 21000))
            self.assertEqual(connection.execute(
                "SELECT discarded_id,kept_id FROM import_collisions"
            ).fetchone(), (1, 2))
            self.assertEqual(connection.execute(
                "SELECT id,reason FROM import_rejections"
            ).fetchone(), (4, "blank canonical name"))
            connection.close()

    def test_rebuild_leaves_alias_catalogues_out_of_the_tag_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "tags.db"
            with sqlite3.connect(target) as connection:
                connection.execute(
                    "CREATE TABLE tags(id INTEGER PRIMARY KEY,name TEXT,post_count INTEGER,"
                    "category INTEGER,ambiguous INTEGER)"
                )
                connection.execute("INSERT INTO tags VALUES(99,'old',1,0,0)")
            connection.close()
            pages = {
                0: [{"id": 1, "name": "office_lady", "count": 50, "type": 0}],
                1: [],
            }
            rebuild_database(
                target, "1", "key", fetcher=lambda cursor, *_args: pages[cursor],
                progress=lambda _line: None, minimum_rows=1,
                required_tags=("office_lady",), maximum_id=1,
            )
            connection = sqlite3.connect(target)
            try:
                names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                connection.close()
            self.assertNotIn("gelbooru_aliases", names)
            self.assertNotIn("alias_sync_state", names)


if __name__ == "__main__":
    unittest.main()
