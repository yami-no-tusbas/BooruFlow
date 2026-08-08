import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from booruflow.infrastructure.tag_details import TagDetailsCache, _recurring_tags, fetch_tag_details


class TagDetailsTests(unittest.TestCase):
    def test_cache_round_trip_keeps_empty_wiki_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = TagDetailsCache(Path(directory) / "details.json")
            cache.save("gelbooru", "empty_tag", {"definition": "", "wiki_url": "https://example.test"})
            self.assertEqual(cache.load("gelbooru", "empty_tag")["definition"], "")

    def test_online_empty_wiki_and_samples_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("booruflow.infrastructure.tag_details.tag_definition_details", return_value=("", "https://wiki", ["related_tag"])), patch(
                "booruflow.infrastructure.tag_details._gelbooru_samples",
                return_value={
                    "samples": [{"id": 1, "preview_url": "preview", "post_url": "post"}],
                    "sample_size": 1,
                    "recurring": [{"tag": "solo", "count": 1}],
                },
            ):
                details = fetch_tag_details(
                    "gelbooru", "Abukuma_(Azur_Lane)", Path(directory) / "cache.json"
                )
            self.assertTrue(details["online"])
            self.assertEqual(details["definition"], "")
            self.assertEqual(details["samples"][0]["id"], 1)
            self.assertEqual(details["recurring"], [{"tag": "solo", "count": 1}])
            self.assertEqual(details["wiki_tags"], ["related_tag"])

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
