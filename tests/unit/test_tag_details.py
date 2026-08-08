import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from booruflow.infrastructure.tag_details import TagDetailsCache, fetch_tag_details


class TagDetailsTests(unittest.TestCase):
    def test_cache_round_trip_keeps_empty_wiki_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = TagDetailsCache(Path(directory) / "details.json")
            cache.save("gelbooru", "empty_tag", {"definition": "", "wiki_url": "https://example.test"})
            self.assertEqual(cache.load("gelbooru", "empty_tag")["definition"], "")

    def test_online_empty_wiki_and_samples_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("booruflow.infrastructure.tag_details.tag_definition", return_value=("", "https://wiki")), patch(
                "booruflow.infrastructure.tag_details._gelbooru_samples",
                return_value=[{"id": 1, "preview_url": "preview", "post_url": "post"}],
            ):
                details = fetch_tag_details(
                    "gelbooru", "Abukuma_(Azur_Lane)", Path(directory) / "cache.json"
                )
            self.assertTrue(details["online"])
            self.assertEqual(details["definition"], "")
            self.assertEqual(details["samples"][0]["id"], 1)
