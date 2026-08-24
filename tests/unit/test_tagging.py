import unittest
from types import SimpleNamespace

from booruflow.application.tagging import (
    LocalMatchState,
    TaggingRequest,
    analysis_resume_action,
    build_clipboard_tags,
    is_rating_observation,
    match_local_tag,
    normalize_booru_tag,
    tagging_priority,
    tags_to_add,
)
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

    def test_safe_normalization_and_exact_matching(self) -> None:
        self.assertEqual(normalize_booru_tag(" Blue Hair "), "blue_hair")
        match = match_local_tag("Blue Hair", {"blue_hair"}, {"solo"})
        self.assertEqual(match.state, LocalMatchState.EXACT)
        self.assertEqual(tags_to_add([match]), ["blue_hair"])

    def test_mapping_and_already_present_are_distinct(self) -> None:
        mapped = match_local_tag("grey hair", {"gray_hair"}, set(), "gray_hair")
        present = match_local_tag("solo", {"solo"}, {"SOLO"})
        self.assertEqual(mapped.state, LocalMatchState.MAPPING)
        self.assertEqual(present.state, LocalMatchState.ALREADY_PRESENT)
        self.assertEqual(tags_to_add([mapped, present]), ["gray_hair"])

    def test_missing_mapping_target_is_not_proposed(self) -> None:
        match = match_local_tag("unknown", {"known"}, set(), "also_unknown")
        self.assertEqual(match.state, LocalMatchState.MISSING)
        self.assertEqual(tags_to_add([match]), [])

    def test_ratings_are_recognized_by_category_and_legacy_name(self) -> None:
        for name in ("safe", "sensitive", "questionable", "explicit", "general"):
            self.assertTrue(is_rating_observation(name, "rating"))
            self.assertTrue(is_rating_observation(name, None))
        self.assertFalse(is_rating_observation("blue_hair", "general"))

    def test_clipboard_builder_has_one_leading_space_and_stable_deduplication(self) -> None:
        self.assertEqual(
            build_clipboard_tags(["chair", "indoors", "chair", "sitting"]),
            " chair indoors sitting",
        )
        self.assertEqual(build_clipboard_tags([]), "")

    def test_rating_never_reaches_local_lookup_or_mapping(self) -> None:
        from booruflow.domain.image_analysis import (
            DecisionState,
            ObservationSource,
            TagObservation,
        )
        from booruflow.presentation.pyside6.tagging_controller import TaggingController

        rating = TagObservation(
            "sensitive", ObservationSource.WD14, 0.9, DecisionState.ACCEPTED,
            category="rating",
        )
        captured = []
        repository = SimpleNamespace(
            item_by_remote_source=lambda *_args: SimpleNamespace(
                id=1, state=SimpleNamespace(value="ready_for_review"),
                cached_path=None, last_error=None,
            ),
            source_tags=lambda _item_id: (),
            observations=lambda _item_id: [(7, rating)],
            tag_mapping=lambda *_args: self.fail("rating attempted a mapping lookup"),
        )
        fake = SimpleNamespace(
            image_analysis=SimpleNamespace(repository=repository), current_post_id=42,
            current_post={"tags": "solo"},
            _local_names=lambda names: self.assertEqual(names, []) or set(),
            _log=lambda *_args, **_kwargs: None,
            page=SimpleNamespace(show_local_review=lambda *args: captured.append(args)),
        )
        TaggingController.refresh_local_review(fake)
        self.assertEqual(captured[0][4], [])
        self.assertEqual(captured[0][5], [])
        self.assertEqual(captured[0][3], [])

    def test_analysis_resume_actions_cover_deduplication_states(self) -> None:
        expected = {
            "ready_for_review": "reuse", "reviewed": "reuse",
            "skipped": "restore_review", "failed": "retry",
            "pending": "restore_pending", "processing": "follow",
        }
        self.assertEqual(
            {state: analysis_resume_action(state) for state in expected}, expected
        )
