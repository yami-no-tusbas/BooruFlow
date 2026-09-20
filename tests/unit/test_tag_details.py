import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from booruflow.infrastructure.tag_details import _recurring_tags, fetch_tag_details
from booruflow.infrastructure.wiki_page_cache import WikiPageCache, WikiPageRecord
from booruflow.infrastructure.wiki_tag_importer import WikiPageNotFoundError


class TagDetailsTests(unittest.TestCase):
    def test_cache_schema_is_additive_on_an_existing_taxonomy_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "existing.sqlite"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE existing_data(value TEXT)")
                connection.execute("INSERT INTO existing_data VALUES('kept')")
            WikiPageCache(database).put(WikiPageRecord(
                "gelbooru", "tag", "Body", "https://wiki/1"
            ))
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    connection.execute("SELECT value FROM existing_data").fetchone()[0], "kept"
                )

    def test_cache_round_trip_keeps_empty_wiki_and_nullable_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = WikiPageCache(Path(directory) / "taxonomy.sqlite")
            stored = cache.put(WikiPageRecord(
                "gelbooru", "empty_tag", "", "https://example.test", wiki_id=7
            ))
            loaded = cache.get("gelbooru", "EMPTY_TAG")
            self.assertEqual(loaded.content, "")
            self.assertEqual(loaded.wiki_id, 7)
            self.assertIsNone(loaded.author)
            self.assertTrue(stored.cached_at)

    def test_cache_key_is_site_and_tag_and_upsert_replaces_the_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = WikiPageCache(Path(directory) / "taxonomy.sqlite")
            cache.put(WikiPageRecord(
                "gelbooru", "shared_tag", "Old", "https://gelbooru/wiki/1",
                cached_at="2000-01-01T00:00:00+00:00",
            ))
            cache.put(WikiPageRecord(
                "e621", "shared_tag", "E621", "https://e621.net/wiki/1"
            ))
            updated = cache.put(WikiPageRecord(
                "gelbooru", "shared_tag", "New", "https://gelbooru/wiki/2"
            ))

            self.assertEqual(cache.get("gelbooru", "shared_tag").content, "New")
            self.assertEqual(cache.get("e621", "shared_tag").content, "E621")
            self.assertNotEqual(updated.cached_at, "2000-01-01T00:00:00+00:00")

    def test_cache_get_many_is_case_insensitive_and_site_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = WikiPageCache(Path(directory) / "taxonomy.sqlite")
            cache.put(WikiPageRecord("gelbooru", "Tag_A", "A", "https://g/a"))
            cache.put(WikiPageRecord("gelbooru", "tag_b", "B", "https://g/b"))
            cache.put(WikiPageRecord("e621", "tag_a", "Other", "https://e/a"))

            records = cache.get_many("gelbooru", ["tag_a", "TAG_B", "missing"])

            self.assertEqual(set(records), {"tag_a", "tag_b"})
            self.assertEqual(records["tag_a"].content, "A")
            self.assertEqual(records["tag_b"].content, "B")

    def test_online_empty_wiki_and_samples_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("booruflow.infrastructure.tag_details.fetch_wiki_page_details", return_value={
                "content": "", "content_format": "plain", "source_url": "https://wiki",
                "wiki_id": 26107, "referenced_tags": ["related_tag"],
            }) as wiki, patch(
                "booruflow.infrastructure.tag_details._gelbooru_samples",
                return_value={
                    "samples": [{"id": 1, "preview_url": "preview", "post_url": "post"}],
                    "sample_size": 1,
                    "recurring": [{"tag": "solo", "count": 1}],
                },
            ):
                details = fetch_tag_details(
                    "gelbooru", "Abukuma_(Azur_Lane)", Path(directory) / "cache.sqlite",
                    wiki_url="https://wiki/26107",
                )
            self.assertTrue(details["online"])
            self.assertEqual(details["definition"], "")
            self.assertEqual(details["samples"][0]["id"], 1)
            self.assertEqual(details["recurring"], [{"tag": "solo", "count": 1}])
            self.assertEqual(details["wiki_tags"], ["related_tag"])
            wiki.assert_called_once_with("gelbooru", "Abukuma_(Azur_Lane)", "https://wiki/26107")

    def test_cache_hit_performs_no_remote_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "cache.sqlite"
            WikiPageCache(database).put(WikiPageRecord(
                "gelbooru", "cached_tag", "Cached body", "https://wiki/5"
            ))
            with patch("booruflow.infrastructure.tag_details.fetch_wiki_page_details") as wiki, patch(
                "booruflow.infrastructure.tag_details._gelbooru_samples"
            ) as samples:
                details = fetch_tag_details("gelbooru", "cached_tag", database)
            self.assertEqual(details["definition"], "Cached body")
            self.assertTrue(details["cache_hit"])
            wiki.assert_not_called()
            samples.assert_not_called()

    def test_refresh_success_replaces_page_and_keeps_cached_samples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "cache.sqlite"
            cache = WikiPageCache(database)
            cache.put(WikiPageRecord(
                "gelbooru", "tag", "Old", "https://wiki/1",
                samples=({"id": 1},), sample_size=1,
            ))
            with patch("booruflow.infrastructure.tag_details.fetch_wiki_page_details", return_value={
                "content": "New", "source_url": "https://wiki/2", "wiki_id": 2,
                "referenced_tags": [],
            }), patch("booruflow.infrastructure.tag_details._gelbooru_samples") as samples:
                details = fetch_tag_details("gelbooru", "tag", database, force=True)
            self.assertEqual(details["definition"], "New")
            self.assertEqual(details["samples"], [{"id": 1}])
            self.assertEqual(cache.get("gelbooru", "tag").content, "New")
            samples.assert_not_called()

    def test_refresh_failure_preserves_previous_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "cache.sqlite"
            cache = WikiPageCache(database)
            cache.put(WikiPageRecord("gelbooru", "tag", "Old", "https://wiki/1"))
            with patch(
                "booruflow.infrastructure.tag_details.fetch_wiki_page_details",
                side_effect=OSError("offline"),
            ):
                details = fetch_tag_details("gelbooru", "tag", database, force=True)
            self.assertTrue(details["refresh_failed"])
            self.assertEqual(details["definition"], "Old")
            self.assertEqual(cache.get("gelbooru", "tag").content, "Old")

    def test_missing_wiki_is_distinct_and_not_negatively_cached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "cache.sqlite"
            with patch(
                "booruflow.infrastructure.tag_details.fetch_wiki_page_details",
                side_effect=WikiPageNotFoundError("missing"),
            ):
                details = fetch_tag_details("gelbooru", "missing", database)
            self.assertIs(details["wiki_exists"], False)
            self.assertIsNone(WikiPageCache(database).get("gelbooru", "missing"))

    def test_recurring_tags_exclude_current_and_count_once_per_post(self) -> None:
        posts = [
            {"tags": "hero solo blue_eyes solo"},
            {"tags": "hero solo red_hair"},
            {"tags": "hero blue_eyes"},
        ]
        recurring = _recurring_tags(posts, "hero", lambda post: post["tags"].split())
        self.assertEqual(recurring[:2], [
            {"tag": "blue_eyes", "count": 2},
            {"tag": "solo", "count": 2},
        ])
        self.assertNotIn("hero", [entry["tag"] for entry in recurring])

    def test_recurring_tags_can_exclude_metadata(self) -> None:
        posts = [{"tags": "injury highres blood"}, {"tags": "injury highres blood"}]
        recurring = _recurring_tags(
            posts, "injury", lambda post: post["tags"].split(), {"highres"}
        )
        self.assertEqual(recurring, [{"tag": "blood", "count": 2}])
