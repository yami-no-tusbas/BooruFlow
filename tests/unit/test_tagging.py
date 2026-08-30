import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from booruflow.application.tagging import (
    LocalMatchState,
    TaggingRequest,
    analysis_resume_action,
    build_clipboard_tags,
    is_rating_observation,
    match_local_tag,
    normalize_booru_tag,
    parse_review_row_token,
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

    def test_review_row_tokens_keep_existing_tags_out_of_integer_dispatch(self) -> None:
        self.assertEqual(parse_review_row_token("existing:absurdres"), ("existing", "absurdres"))
        self.assertEqual(parse_review_row_token(42), ("observation", 42))

    def test_bulk_review_dispatches_existing_and_observation_tokens_once(self) -> None:
        from booruflow.domain.image_analysis import DecisionState, ObservationSource, TagObservation
        from booruflow.presentation.pyside6.tagging_controller import TaggingController

        existing_calls = []
        observation_calls = []
        refreshes = []
        existing = {"absurdres": "keep", "solo": "keep"}
        observations = {17: "unreviewed", 23: "unreviewed"}

        def observation_rows(_item_id):
            return [
                (observation_id, TagObservation(
                    f"tag_{observation_id}", ObservationSource.WD14, 0.8,
                    DecisionState(decision), category="general",
                ))
                for observation_id, decision in observations.items()
            ]

        repository = SimpleNamespace(
            item_by_remote_source=lambda *_args: SimpleNamespace(id=91),
            observations=observation_rows,
            existing_tag_decision=lambda _item_id, tag: existing[tag],
            set_existing_tag_decision=lambda _item_id, tag, decision: (
                existing_calls.append((_item_id, tag, decision)), existing.__setitem__(tag, decision)
            ),
        )
        workflow = SimpleNamespace(
            decide=lambda observation_id, decision, _name: (
                observation_calls.append((observation_id, decision, _name)),
                observations.__setitem__(observation_id, decision.value),
            ),
        )
        fake = SimpleNamespace(
            image_analysis=SimpleNamespace(repository=repository, workflow=workflow),
            current_post_id=123,
            _log=lambda *_args, **_kwargs: None,
            refresh_local_review=lambda: refreshes.append(True),
            page=SimpleNamespace(analysis_state=SimpleNamespace(setText=lambda *_args: None)),
            _current_item_id=lambda: 91,
            _unique_targets=TaggingController._unique_targets,
            _undo_stack=[], _redo_stack=[],
        )
        fake._apply_changes = lambda item_id, changes, undo: TaggingController._apply_changes(
            fake, item_id, changes, undo=undo
        )

        TaggingController.decide(fake, ["existing:absurdres", "existing:solo", "17", 23], "rejected")

        self.assertEqual(existing_calls, [(91, "absurdres", "remove"), (91, "solo", "remove")])
        self.assertEqual(
            observation_calls,
            [(17, DecisionState.REJECTED, None), (23, DecisionState.REJECTED, None)],
        )
        self.assertEqual(refreshes, [True])

        existing_calls.clear(); observation_calls.clear(); refreshes.clear()
        TaggingController.decide(fake, ["existing:absurdres", "17"], "accepted")
        self.assertEqual(existing_calls, [(91, "absurdres", "keep")])
        self.assertEqual(observation_calls, [(17, DecisionState.ACCEPTED, None)])
        self.assertEqual(refreshes, [True])

    def test_grouped_undo_redo_restores_exact_existing_and_wd14_states(self) -> None:
        from booruflow.domain.image_analysis import DecisionState, ObservationSource, TagObservation
        from booruflow.presentation.pyside6.tagging_controller import TaggingController

        existing = {"a": "keep", "b": "remove"}
        observations = {7: "unreviewed", 8: "accepted"}
        refreshes = []
        repository = SimpleNamespace(
            item_by_remote_source=lambda *_args: SimpleNamespace(id=3),
            existing_tag_decision=lambda _item_id, tag: existing[tag],
            set_existing_tag_decision=lambda _item_id, tag, decision: existing.__setitem__(tag, decision),
            observations=lambda _item_id: [
                (oid, TagObservation(str(oid), ObservationSource.WD14, 0.8, DecisionState(decision), category="general"))
                for oid, decision in observations.items()
            ],
        )
        workflow = SimpleNamespace(
            decide=lambda oid, decision, _name: observations.__setitem__(oid, decision.value),
        )
        fake = SimpleNamespace(
            image_analysis=SimpleNamespace(repository=repository, workflow=workflow), current_post_id=1,
            _log=lambda *_args, **_kwargs: None, refresh_local_review=lambda: refreshes.append(True),
            page=SimpleNamespace(analysis_state=SimpleNamespace(setText=lambda *_args: None)),
            _current_item_id=lambda: 3, _unique_targets=TaggingController._unique_targets,
            _undo_stack=[], _redo_stack=[],
        )
        fake._apply_changes = lambda item_id, changes, undo: TaggingController._apply_changes(fake, item_id, changes, undo=undo)

        TaggingController.decide(fake, ["existing:a", "existing:b", 7, 8], "rejected")
        self.assertEqual(existing, {"a": "remove", "b": "remove"})
        self.assertEqual(observations, {7: "rejected", 8: "rejected"})
        self.assertEqual(len(fake._undo_stack), 1)
        TaggingController.undo(fake)
        self.assertEqual(existing, {"a": "keep", "b": "remove"})
        self.assertEqual(observations, {7: "unreviewed", 8: "accepted"})
        TaggingController.redo(fake)
        self.assertEqual(existing, {"a": "remove", "b": "remove"})
        self.assertEqual(observations, {7: "rejected", 8: "rejected"})
        self.assertEqual(refreshes, [True, True, True])
        TaggingController.undo(fake)
        TaggingController.decide(fake, "existing:b", "accepted")
        self.assertEqual(existing["b"], "keep")
        self.assertEqual(fake._redo_stack, [])

    def test_manual_add_persists_deduplicates_and_participates_in_undo_redo(self) -> None:
        from booruflow.domain.image_analysis import (
            AnalysisItem,
            DecisionState,
            InputKind,
            SourceReference,
        )
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.presentation.pyside6.tagging_controller import TaggingController

        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "analysis.sqlite"
            repository = ImageAnalysisRepository(database)
            item_id = repository.add_item(AnalysisItem(
                SourceReference(InputKind.LOCAL_FILE, original_path=Path("image.png"))
            ))
            refreshes = []
            workflow = SimpleNamespace(
                add_manual_tag=repository.add_manual_observation,
                decide=repository.decide_observation,
            )
            page = SimpleNamespace(
                clear_manual_entry=lambda: None,
                analysis_state=SimpleNamespace(setText=lambda *_args: None),
            )
            fake = SimpleNamespace(
                image_analysis=SimpleNamespace(repository=repository, workflow=workflow),
                current_post={"tags": "solo"},
                _current_item_id=lambda: item_id,
                _eligible_exact_name=lambda value: normalize_booru_tag(value),
                _log=lambda *_args, **_kwargs: None,
                refresh_local_review=lambda: refreshes.append(True),
                page=page, _undo_stack=[], _redo_stack=[],
            )

            TaggingController.add_manual_tag(fake, "Blue Hair")
            observations = repository.observations(item_id)
            self.assertEqual([(row.name, row.decision) for _, row in observations], [
                ("blue_hair", DecisionState.ACCEPTED)
            ])
            TaggingController.add_manual_tag(fake, "blue hair")
            self.assertEqual(len(repository.observations(item_id)), 1)
            TaggingController.add_manual_tag(fake, "solo")
            self.assertEqual(len(repository.observations(item_id)), 1)
            TaggingController.undo(fake)
            self.assertEqual(repository.observations(item_id)[0][1].decision, DecisionState.REJECTED)
            TaggingController.redo(fake)
            self.assertEqual(repository.observations(item_id)[0][1].decision, DecisionState.ACCEPTED)
            repository.close()

            with ImageAnalysisRepository(database) as reopened:
                self.assertEqual(
                    reopened.tag_review_summary(item_id, ["solo"])["final_tags"],
                    ["blue_hair", "solo"],
                )

    def test_manual_readd_of_removed_original_persistently_restores_keep(self) -> None:
        from booruflow.domain.image_analysis import AnalysisItem, InputKind, SourceReference
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.presentation.pyside6.tagging_controller import TaggingController

        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "analysis.sqlite"
            repository = ImageAnalysisRepository(database)
            item_id = repository.add_item(AnalysisItem(
                SourceReference(InputKind.LOCAL_FILE, original_path=Path("image.png"))
            ))
            repository.set_existing_tag_decision(item_id, "ball", "remove")
            page = SimpleNamespace(
                clear_manual_entry=lambda: None,
                analysis_state=SimpleNamespace(setText=lambda *_args: None),
            )
            fake = SimpleNamespace(
                image_analysis=SimpleNamespace(
                    repository=repository,
                    workflow=SimpleNamespace(
                        add_manual_tag=repository.add_manual_observation,
                        decide=repository.decide_observation,
                    ),
                ),
                current_post={"tags": "ball foo"},
                _current_item_id=lambda: item_id,
                _eligible_exact_name=lambda value: normalize_booru_tag(value),
                _apply_changes=lambda item, changes, undo: TaggingController._apply_changes(
                    fake, item, changes, undo=undo
                ),
                _log=lambda *_args, **_kwargs: None,
                refresh_local_review=lambda: None,
                page=page, _undo_stack=[], _redo_stack=[],
            )

            TaggingController.add_manual_tag(fake, "ball")

            self.assertEqual(repository.existing_tag_decision(item_id, "ball"), "keep")
            self.assertEqual(repository.observations(item_id), [])
            self.assertEqual(
                repository.tag_review_summary(item_id, ["ball", "foo"])["final_tags"],
                ["ball", "foo"],
            )
            repository.close()
            with ImageAnalysisRepository(database) as reopened:
                self.assertEqual(reopened.existing_tag_decision(item_id, "ball"), "keep")
                self.assertEqual(reopened.observations(item_id), [])
                self.assertIn(
                    "ball",
                    reopened.tag_review_summary(item_id, ["ball", "foo"])["final_tags"],
                )

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
        self.assertEqual(captured[0][5], ["solo"])
        self.assertEqual([row["tag"] for row in captured[0][3]], ["solo"])

    def test_existing_tag_and_wd14_observation_are_one_existing_review_row(self) -> None:
        from booruflow.domain.image_analysis import (
            DecisionState,
            ObservationSource,
            SourceTag,
            TagObservation,
        )
        from booruflow.presentation.pyside6.tagging_controller import TaggingController

        cyborg = TagObservation(
            "cyborg", ObservationSource.WD14, 0.80, DecisionState.UNREVIEWED,
            category="general",
        )
        robot = TagObservation(
            "robot", ObservationSource.WD14, 0.70, DecisionState.UNREVIEWED,
            category="general",
        )
        captured = []
        repository = SimpleNamespace(
            item_by_remote_source=lambda *_args: SimpleNamespace(
                id=1, state=SimpleNamespace(value="ready_for_review"),
                cached_path=None, last_error=None,
            ),
            source_tags=lambda _item_id: (
                SourceTag("cyborg", ObservationSource.GELBOORU, "general"),
                SourceTag("cyberpunk", ObservationSource.GELBOORU, "general"),
            ),
            observations=lambda _item_id: [(17, cyborg), (23, robot)],
            tag_mapping=lambda *_args: None,
            tag_review_summary=lambda _item_id, originals: {
                "removals": [], "final_tags": sorted(originals),
            },
        )
        fake = SimpleNamespace(
            image_analysis=SimpleNamespace(repository=repository), current_post_id=42,
            current_post={"tags": "cyborg cyberpunk"}, _local_names=lambda _names: set(),
            _log=lambda *_args, **_kwargs: None,
            page=SimpleNamespace(show_local_review=lambda *args: captured.append(args)),
        )

        TaggingController.refresh_local_review(fake)

        rows = captured[0][3]
        self.assertEqual([row["tag"] for row in rows], ["cyberpunk", "cyborg", "robot"])
        cyborg_row = next(row for row in rows if row["tag"] == "cyborg")
        self.assertEqual(cyborg_row["id"], "existing:cyborg")
        self.assertEqual(cyborg_row["decision"], "keep")
        self.assertEqual(cyborg_row["confidence"], "0.800")
        self.assertIn("WD14", cyborg_row["match"])
        self.assertEqual(next(row for row in rows if row["tag"] == "robot")["id"], 23)

    def test_category_6_new_suggestion_is_filtered_but_existing_tag_remains(self) -> None:
        from booruflow.domain.image_analysis import (
            DecisionState,
            ObservationSource,
            SourceTag,
            TagObservation,
        )
        from booruflow.infrastructure.tag_browser import TagRow
        from booruflow.presentation.pyside6.tagging_controller import TaggingController

        observation = TagObservation(
            "deprecated_new", ObservationSource.WD14, 0.8,
            DecisionState.UNREVIEWED, category="general",
        )
        captured = []
        repository = SimpleNamespace(
            item_by_remote_source=lambda *_args: SimpleNamespace(
                id=1, state=SimpleNamespace(value="ready_for_review"),
                cached_path=None, last_error=None,
            ),
            source_tags=lambda _item_id: (
                SourceTag("deprecated_existing", ObservationSource.GELBOORU, "general"),
            ),
            observations=lambda _item_id: [(7, observation)],
            tag_mapping=lambda *_args: None,
            tag_review_summary=lambda _item_id, originals: {
                "removals": [], "final_tags": sorted(originals),
            },
        )
        rows = {
            "deprecated_existing": TagRow(1, "deprecated_existing", 10, 6, 0),
            "deprecated_new": TagRow(2, "deprecated_new", 5, 6, 0),
        }
        fake = SimpleNamespace(
            image_analysis=SimpleNamespace(repository=repository), current_post_id=42,
            current_post={"tags": "deprecated_existing"},
            _local_tag_rows=lambda names: {
                key: value for key, value in rows.items() if key in names
            },
            _log=lambda *_args, **_kwargs: None,
            page=SimpleNamespace(show_local_review=lambda *args: captured.append(args)),
        )

        TaggingController.refresh_local_review(fake)

        rendered = captured[0][3]
        self.assertEqual([row["tag"] for row in rendered], ["deprecated_existing"])
        self.assertEqual(rendered[0]["id"], "existing:deprecated_existing")
        self.assertEqual(rendered[0]["decision"], "keep")
        self.assertEqual(rendered[0]["category"], "6")

    def test_analysis_resume_actions_cover_deduplication_states(self) -> None:
        expected = {
            "ready_for_review": "reuse", "reviewed": "reuse",
            "skipped": "restore_review", "failed": "retry",
            "pending": "restore_pending", "processing": "follow",
        }
        self.assertEqual(
            {state: analysis_resume_action(state) for state in expected}, expected
        )
