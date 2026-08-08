import unittest

from legacy.artist_by_tag_gui import compose_search_tags, remaining_review_tabs, unique_lines


class LegacyBaselineTests(unittest.TestCase):
    def test_unique_lines_preserves_order_and_decodes_html(self) -> None:
        self.assertEqual(
            unique_lines("alpha;beta\nalpha\ndragons&#039;_crown"),
            ["alpha", "beta", "dragons'_crown"],
        )

    def test_compose_search_tags_keeps_sections_ordered(self) -> None:
        self.assertEqual(
            compose_search_tags("-rating:general", "blue_hair", "solo -comic"),
            ["-rating:general", "blue_hair", "solo", "-comic"],
        )

    def test_remaining_review_tabs_ignores_empty_and_non_tag_tabs(self) -> None:
        data = {
            "tabs": [
                {"type": "tag", "tags": ["artist_name"]},
                {"type": "tag", "tags": []},
                {"type": "search", "tags": ["ignored"]},
            ]
        }
        self.assertEqual(remaining_review_tabs(data), [data["tabs"][0]])


if __name__ == "__main__":
    unittest.main()
