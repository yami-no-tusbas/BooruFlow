import unittest

from booruflow.application.tagging import TaggingRequest, tagging_priority
from booruflow.infrastructure.gelbooru_tagging import payload_posts, post_tags


class TaggingTests(unittest.TestCase):
    def test_request_validates_ordered_thresholds(self) -> None:
        with self.assertRaises(ValueError):
            TaggingRequest("", 10, 1, 0, 8, 6, 5)

    def test_priority_boundaries_are_inclusive(self) -> None:
        self.assertEqual(tagging_priority(5, 5, 8), "critical")
        self.assertEqual(tagging_priority(8, 5, 8), "high")
        self.assertEqual(tagging_priority(9, 5, 8), "low")

    def test_gelbooru_payload_shapes_and_tags(self) -> None:
        post = {"id": 1, "tags": "blue_hair dragon&amp;girl solo"}
        self.assertEqual(payload_posts({"post": [post]}), [post])
        self.assertEqual(payload_posts({"post": post}), [post])
        self.assertEqual(payload_posts([post]), [post])
        self.assertEqual(post_tags(post), ["blue_hair", "dragon&girl", "solo"])
