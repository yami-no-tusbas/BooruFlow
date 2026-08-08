import unittest

from legacy.artist_by_tag_gui import (
    gelbooru_post_tags,
    gelbooru_posts_from_payload,
    tagging_priority,
)


class TaggingHelpersTests(unittest.TestCase):
    def test_decodes_and_counts_post_tags(self):
        self.assertEqual(
            gelbooru_post_tags({"tags": "solo dragon&amp;girl blue_hair"}),
            ["solo", "dragon&girl", "blue_hair"],
        )

    def test_accepts_both_gelbooru_json_shapes(self):
        self.assertEqual(
            gelbooru_posts_from_payload({"@attributes": {}, "post": [{"id": 1}]}),
            [{"id": 1}],
        )
        self.assertEqual(gelbooru_posts_from_payload([{"id": 2}]), [{"id": 2}])

    def test_priority_boundaries_are_inclusive(self):
        self.assertEqual(tagging_priority(5, 5, 10), "Critique")
        self.assertEqual(tagging_priority(6, 5, 10), "Haute")
        self.assertEqual(tagging_priority(10, 5, 10), "Haute")
        self.assertEqual(tagging_priority(11, 5, 10), "Faible")


if __name__ == "__main__":
    unittest.main()
