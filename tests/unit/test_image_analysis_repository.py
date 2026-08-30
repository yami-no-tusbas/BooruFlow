import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from booruflow.domain.image_analysis import (
    AnalysisItem,
    AnalysisState,
    InputKind,
    ObservationSource,
    SourceReference,
    SourceTag,
)
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository


class ImageAnalysisRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "image_analysis.sqlite"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def item(name: str = "image.png") -> AnalysisItem:
        return AnalysisItem(
            SourceReference(InputKind.LOCAL_FILE, original_path=Path(name)),
            cached_path=Path(name), content_sha256="a" * 64,
            mime_type="image/png", width=10, height=20,
        )

    def test_empty_database_migrates_and_reopens_with_required_pragmas(self) -> None:
        with ImageAnalysisRepository(self.database) as repository:
            tables = {
                row[0] for row in repository.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertEqual(repository.connection.execute("PRAGMA user_version").fetchone()[0], 19)
            self.assertEqual(repository.connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(repository.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertTrue({
                "analysis_items", "source_tags", "model_runs", "tag_observations",
                "embeddings", "object_detections", "image_statistics", "item_artists",
                "local_source_links", "post_metadata_cache",
                "image_provenances",
                "tag_mappings",
                "artist_profiles",
                "local_filename_metadata",
                "library_index_jobs", "remote_artist_state",
                "library_index_paths",
            }.issubset(tables))
        with ImageAnalysisRepository(self.database) as reopened:
            self.assertEqual(reopened.connection.execute("PRAGMA user_version").fetchone()[0], 19)

    def test_legacy_unverified_publications_are_requeued_without_losing_history(self) -> None:
        from booruflow.domain.image_analysis import PublishState

        with ImageAnalysisRepository(self.database) as repository:
            item_id = repository.add_item(AnalysisItem(
                SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="42")
            ))
            repository.save_review_batch_entry(
                item_id, original_tags=["a"], additions=["b"], removals=[],
                reviewed_final_tags=["a", "b"],
            )
            repository.update_publish_state(item_id, PublishState.PUBLISHED)
            changed_item_id = repository.add_item(AnalysisItem(
                SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="43")
            ))
            repository.save_review_batch_entry(
                changed_item_id, original_tags=["x"], additions=["y"], removals=[],
                reviewed_final_tags=["x", "y"],
            )
            repository.update_publish_state(changed_item_id, PublishState.PUBLISHED)
            previous_success = repository.batch_entry(changed_item_id)
            repository.connection.execute(
                """UPDATE tagging_review_batch_entries
                   SET additions_json=?,reviewed_final_tags_json=?,reviewed_at=?
                   WHERE item_id=?""",
                ('["y","z"]', '["x","y","z"]', "2999-01-01T00:00:00+00:00", changed_item_id),
            )
            repository.connection.commit()
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "ALTER TABLE tagging_review_batch_entries "
                "DROP COLUMN published_verified_at"
            )
            connection.execute(
                "ALTER TABLE tagging_review_batch_entries "
                "DROP COLUMN published_final_tags_json"
            )
            connection.execute("PRAGMA user_version=16")
            connection.commit()
        finally:
            connection.close()

        with ImageAnalysisRepository(self.database) as reopened:
            entry = reopened.batch_entry(item_id)
            self.assertEqual(entry["published_final_tags"], ["a", "b"])
            self.assertIs(entry["publish_state"], PublishState.PENDING_PUBLISH)
            self.assertIsNone(entry["published_verified_at"])
            changed = reopened.batch_entry(changed_item_id)
            self.assertIs(changed["publish_state"], PublishState.PENDING_PUBLISH)
            self.assertIsNone(changed["published_final_tags"])
            self.assertEqual(changed["published_at"], previous_success["published_at"])
            self.assertEqual(
                changed["publish_attempts"], previous_success["publish_attempts"]
            )

    def test_queue_cleanup_archives_only_listing_and_preserves_analysis(self) -> None:
        with ImageAnalysisRepository(self.database) as repository:
            item_id = repository.add_item(self.item())
            repository.transition(item_id, AnalysisState.PROCESSING)
            repository.transition(item_id, AnalysisState.READY_FOR_REVIEW)
            run_id = repository.begin_model_run(item_id, "onnx", "wd14", "1", "cfg")
            repository.complete_model_run(run_id)
            repository.add_manual_observation(item_id, "kept_decision")
            repository.transition(item_id, AnalysisState.REVIEWED)
            changed, retained = repository.clean_queue("reviewed")
            self.assertEqual((changed, retained), (1, 0))
            self.assertEqual(repository.list_items(), [])
            self.assertIsNotNone(repository.get_item(item_id))
            self.assertEqual(len(repository.observations(item_id)), 1)
            self.assertEqual(repository.connection.execute(
                "SELECT COUNT(*) FROM model_runs WHERE item_id=?", (item_id,)
            ).fetchone()[0], 1)

    def test_persistent_full_tag_review_builds_final_tags(self) -> None:
        with ImageAnalysisRepository(self.database) as repository:
            item_id = repository.add_item(self.item())
            repository.set_existing_tag_decision(item_id, "wrong_tag", "remove")
            repository.add_manual_observation(item_id, "manual_tag")
            summary = repository.tag_review_summary(item_id, ["kept_tag", "wrong_tag"])
            self.assertEqual(summary["original_tags"], ["kept_tag", "wrong_tag"])
            self.assertEqual(summary["removals"], ["wrong_tag"])
            self.assertEqual(summary["additions"], ["manual_tag"])
            self.assertEqual(summary["final_tags"], ["kept_tag", "manual_tag"])

    def test_review_batch_snapshot_is_deterministic_unique_and_persistent(self) -> None:
        from booruflow.domain.image_analysis import PublishState

        with ImageAnalysisRepository(self.database) as repository:
            item_id = repository.add_item(AnalysisItem(
                SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="42")
            ))
            state = repository.save_review_batch_entry(
                item_id, original_tags=["c", "a", "a"], additions=["d", "d"],
                removals=["b", "b"], reviewed_final_tags=["d", "a", "c", "a"],
            )
            self.assertIs(state, PublishState.PENDING_PUBLISH)
            self.assertEqual(repository.batch_entry(item_id), {
                "item_id": item_id, "site": "gelbooru", "post_id": "42",
                "original_tags": ["a", "c"], "additions": ["d"], "removals": ["b"],
                "reviewed_final_tags": ["a", "c", "d"],
                "reviewed_at": repository.batch_entry(item_id)["reviewed_at"],
                "publish_state": PublishState.PENDING_PUBLISH,
                "publish_attempts": 0, "last_error": None,
                "last_attempt_at": None, "published_at": None,
                "published_verified_at": None,
                "published_final_tags": None,
            })
            repository.save_review_batch_entry(
                item_id, original_tags=["a", "c"], additions=["e"], removals=[],
                reviewed_final_tags=["a", "c", "e"],
            )
            self.assertEqual(len(repository.list_batch_entries()), 1)
            self.assertEqual(repository.batch_entry(item_id)["additions"], ["e"])
        with ImageAnalysisRepository(self.database) as reopened:
            self.assertEqual(reopened.batch_entry(item_id)["reviewed_final_tags"], ["a", "c", "e"])

    def test_local_review_batch_is_not_given_a_remote_publish_state(self) -> None:
        from booruflow.domain.image_analysis import PublishState

        with ImageAnalysisRepository(self.database) as repository:
            item_id = repository.add_item(self.item())
            state = repository.save_review_batch_entry(
                item_id, original_tags=["solo"], additions=["chair"], removals=[],
                reviewed_final_tags=["chair", "solo"],
            )
            entry = repository.batch_entry(item_id)
            self.assertIs(state, PublishState.REVIEWED)
            self.assertEqual((entry["site"], entry["post_id"]), (None, None))
            self.assertIs(entry["publish_state"], PublishState.REVIEWED)

    def _published_remote(
        self,
        repository: ImageAnalysisRepository,
        *,
        post_id: str,
        original: list[str],
        additions: list[str],
        removals: list[str],
        final_tags: list[str],
    ) -> int:
        item_id = repository.add_item(AnalysisItem(
            SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id=post_id)
        ))
        repository.save_review_batch_entry(
            item_id, original_tags=original, additions=additions, removals=removals,
            reviewed_final_tags=final_tags,
        )
        repository.begin_publish_attempt(item_id)
        repository.publish_succeeded(item_id)
        return item_id

    def test_published_identical_review_stays_published(self) -> None:
        from booruflow.domain.image_analysis import PublishState

        with ImageAnalysisRepository(self.database) as repository:
            item_id = self._published_remote(
                repository, post_id="84", original=["a"], additions=["b"],
                removals=[], final_tags=["a", "b"],
            )
            state = repository.save_review_batch_entry(
                item_id, original_tags=["a"], additions=["b"], removals=[],
                reviewed_final_tags=["a", "b"],
            )
            self.assertIs(state, PublishState.PUBLISHED)
            self.assertEqual(repository.batch_entry(item_id)["additions"], ["b"])

    def test_published_new_addition_returns_to_pending_publish(self) -> None:
        from booruflow.domain.image_analysis import PublishState

        with ImageAnalysisRepository(self.database) as repository:
            item_id = self._published_remote(
                repository, post_id="85", original=["a"], additions=[], removals=[],
                final_tags=["a"],
            )
            state = repository.save_review_batch_entry(
                item_id, original_tags=["a"], additions=["b"], removals=[],
                reviewed_final_tags=["a", "b"],
            )
            self.assertIs(state, PublishState.PENDING_PUBLISH)

    def test_published_new_removal_returns_to_pending_publish(self) -> None:
        from booruflow.domain.image_analysis import PublishState

        with ImageAnalysisRepository(self.database) as repository:
            item_id = self._published_remote(
                repository, post_id="86", original=["a", "b"], additions=[], removals=[],
                final_tags=["a", "b"],
            )
            state = repository.save_review_batch_entry(
                item_id, original_tags=["a", "b"], additions=[], removals=["b"],
                reviewed_final_tags=["a"],
            )
            self.assertIs(state, PublishState.PENDING_PUBLISH)

    def test_cancelling_a_previously_published_removal_returns_to_pending(self) -> None:
        from booruflow.domain.image_analysis import PublishState

        with ImageAnalysisRepository(self.database) as repository:
            item_id = self._published_remote(
                repository, post_id="87", original=["a", "b"], additions=[], removals=["b"],
                final_tags=["a"],
            )
            state = repository.save_review_batch_entry(
                item_id, original_tags=["a", "b"], additions=[], removals=[],
                reviewed_final_tags=["a", "b"],
            )
            self.assertIs(state, PublishState.PENDING_PUBLISH)

    def test_return_to_exact_published_snapshot_restores_published_and_keeps_audit(self) -> None:
        from booruflow.domain.image_analysis import PublishState

        with ImageAnalysisRepository(self.database) as repository:
            item_id = self._published_remote(
                repository, post_id="88", original=["a"], additions=["b"], removals=[],
                final_tags=["a", "b"],
            )
            published = repository.batch_entry(item_id)
            repository.save_review_batch_entry(
                item_id, original_tags=["a"], additions=["b", "c"], removals=[],
                reviewed_final_tags=["a", "b", "c"],
            )
            state = repository.save_review_batch_entry(
                item_id, original_tags=["a"], additions=["b"], removals=[],
                reviewed_final_tags=["b", "a", "a"],
            )
            restored = repository.batch_entry(item_id)

            self.assertIs(state, PublishState.PUBLISHED)
            self.assertEqual(restored["published_final_tags"], ["a", "b"])
            self.assertEqual(restored["published_at"], published["published_at"])
            self.assertEqual(
                restored["published_verified_at"], published["published_verified_at"]
            )
            self.assertEqual(restored["publish_attempts"], published["publish_attempts"])
            self.assertEqual(restored["last_attempt_at"], published["last_attempt_at"])

    def test_publish_attempt_audit_and_crash_recovery_are_persistent(self) -> None:
        from booruflow.domain.image_analysis import PublishState

        with ImageAnalysisRepository(self.database) as repository:
            item_id = repository.add_item(AnalysisItem(
                SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="85")
            ))
            repository.save_review_batch_entry(
                item_id, original_tags=["a"], additions=["b"], removals=[],
                reviewed_final_tags=["a", "b"],
            )
            self.assertEqual(repository.begin_publish_attempt(item_id), 1)
            repository.publish_failed(item_id, "temporary network failure")
            entry = repository.batch_entry(item_id)
            self.assertIs(entry["publish_state"], PublishState.FAILED)
            self.assertEqual(entry["publish_attempts"], 1)
            self.assertEqual(entry["last_error"], "temporary network failure")
            self.assertIsNotNone(entry["last_attempt_at"])
            self.assertEqual(repository.retry_failed_publishes([item_id]), 1)
            repository.begin_publish_attempt(item_id)
        with ImageAnalysisRepository(self.database) as reopened:
            self.assertEqual(reopened.recover_interrupted_publishes(), 1)
            entry = reopened.batch_entry(item_id)
            self.assertIs(entry["publish_state"], PublishState.PENDING_PUBLISH)
            self.assertEqual(entry["publish_attempts"], 2)
            self.assertIn("interrompue", entry["last_error"])

    def test_pre_submit_session_error_returns_entry_to_pending_with_diagnostic(self) -> None:
        from booruflow.domain.image_analysis import PublishState

        with ImageAnalysisRepository(self.database) as repository:
            item_id = repository.add_item(AnalysisItem(
                SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="86")
            ))
            repository.save_review_batch_entry(
                item_id, original_tags=["a"], additions=["b"], removals=[],
                reviewed_final_tags=["a", "b"],
            )
            repository.begin_publish_attempt(item_id)
            repository.publish_deferred(item_id, "session unknown")
            entry = repository.batch_entry(item_id)
            self.assertIs(entry["publish_state"], PublishState.PENDING_PUBLISH)
            self.assertEqual(entry["last_error"], "session unknown")

    def test_batch_listing_has_deterministic_state_priority_and_removal_keeps_review_data(self) -> None:
        from booruflow.domain.image_analysis import PublishState

        with ImageAnalysisRepository(self.database) as repository:
            remote_ids = [repository.add_item(AnalysisItem(
                SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id=str(post_id))
            )) for post_id in (10, 20, 30)]
            local_id = repository.add_item(self.item("local.png"))
            for item_id in (*remote_ids, local_id):
                repository.save_review_batch_entry(
                    item_id, original_tags=["a"], additions=["b"], removals=[],
                    reviewed_final_tags=["a", "b"],
                )
            repository.update_publish_state(remote_ids[1], PublishState.PUBLISHED)
            repository.update_publish_state(remote_ids[2], PublishState.FAILED)
            self.assertEqual(
                [entry["item_id"] for entry in repository.list_batch_entries()],
                [remote_ids[0], remote_ids[2], local_id, remote_ids[1]],
            )
            repository.set_existing_tag_decision(remote_ids[0], "a", "remove")
            self.assertTrue(repository.remove_batch_entry(remote_ids[0]))
            self.assertIsNone(repository.batch_entry(remote_ids[0]))
            self.assertIsNotNone(repository.get_item(remote_ids[0]))
            self.assertEqual(repository.existing_tag_decision(remote_ids[0], "a"), "remove")

    def test_existing_keep_remove_and_wd14_decisions_survive_reload(self) -> None:
        from booruflow.domain.image_analysis import DecisionState
        with ImageAnalysisRepository(self.database) as repository:
            item_id = repository.add_item(self.item())
            run_id = repository.begin_model_run(item_id, "wd14", "WD14", "1", "cfg")
            repository.save_tag_predictions(item_id, run_id, [("long_hair", "general", 0.9)])
            observation_id = repository.observations(item_id)[0][0]
            repository.set_existing_tag_decision(item_id, "blue_hair", "remove")
            repository.decide_observation(observation_id, DecisionState.ACCEPTED)
            self.assertEqual(repository.tag_review_summary(item_id, ["1girl", "blue_hair"]), {"original_tags": ["1girl", "blue_hair"], "additions": ["long_hair"], "removals": ["blue_hair"], "final_tags": ["1girl", "long_hair"]})
        with ImageAnalysisRepository(self.database) as reopened:
            self.assertEqual(reopened.tag_review_summary(item_id, ["1girl", "blue_hair"])["final_tags"], ["1girl", "long_hair"])
            reopened.set_existing_tag_decision(item_id, "blue_hair", "keep")
            self.assertIn("blue_hair", reopened.tag_review_summary(item_id, ["1girl", "blue_hair"])["final_tags"])

    def test_existing_wd14_duplicate_removal_controls_the_final_tag(self) -> None:
        from booruflow.domain.image_analysis import DecisionState

        with ImageAnalysisRepository(self.database) as repository:
            item_id = repository.add_item(self.item())
            run_id = repository.begin_model_run(item_id, "wd14", "WD14", "1", "cfg")
            repository.save_tag_predictions(
                item_id, run_id, [("cyborg", "general", 0.80), ("robot", "general", 0.70)]
            )
            observations = dict(repository.observations(item_id))
            repository.set_existing_tag_decision(item_id, "cyborg", "remove")
            self.assertEqual(
                repository.tag_review_summary(item_id, ["cyborg", "cyberpunk"])["final_tags"],
                ["cyberpunk"],
            )
            repository.set_existing_tag_decision(item_id, "cyborg", "keep")
            robot_id = next(key for key, value in observations.items() if value.name == "robot")
            repository.decide_observation(robot_id, DecisionState.REJECTED)
            self.assertEqual(
                repository.tag_review_summary(item_id, ["cyborg", "cyberpunk"])["final_tags"],
                ["cyberpunk", "cyborg"],
            )

    def test_remote_lookup_reads_hidden_item_and_mapping_crud(self) -> None:
        remote = AnalysisItem(
            SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="123"),
            cached_path=Path("cached.png"), content_sha256="c" * 64,
            mime_type="image/png", width=10, height=20,
        )
        with ImageAnalysisRepository(self.database) as repository:
            item_id = repository.add_item(remote)
            repository.clean_queue("active")
            found = repository.item_by_remote_source("gelbooru", "123")
            self.assertEqual(found.id, item_id)
            self.assertFalse(repository.item_queue_visible(item_id))
            repository.set_tag_mapping("wd14", "grey hair", "gelbooru", "gray_hair")
            self.assertEqual(
                repository.tag_mapping("wd14", "GREY HAIR", "gelbooru"), "gray_hair"
            )
            repository.delete_tag_mapping("wd14", "grey hair", "gelbooru")
            self.assertIsNone(repository.tag_mapping("wd14", "grey hair", "gelbooru"))

    def test_secondary_provenance_pending_item_can_be_made_queue_visible(self) -> None:
        primary = AnalysisItem(
            SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="100"),
            cached_path=Path("cached.png"), content_sha256="d" * 64,
            mime_type="image/png", width=10, height=20,
        )
        secondary = SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="200")
        with ImageAnalysisRepository(self.database) as repository:
            item_id = repository.add_item(primary)
            repository.reuse_item(item_id, secondary)
            repository.clean_queue("active")
            found = repository.item_by_remote_source("gelbooru", "200")
            self.assertEqual(found.id, item_id)
            self.assertFalse(repository.item_queue_visible(item_id))
            repository.make_queue_visible(item_id)
            self.assertTrue(repository.item_queue_visible(item_id))

    def test_embedding_only_import_can_avoid_full_analysis_scheduler(self) -> None:
        with ImageAnalysisRepository(self.database) as repository:
            item_id = repository.add_item(self.item("embedding-only.png"))
            repository.suppress_analysis_request(item_id)
            self.assertEqual(repository.connection.execute(
                "SELECT analysis_requested FROM analysis_items WHERE id=?", (item_id,)
            ).fetchone()[0], 0)

    def test_empty_active_queue_leaves_processing_and_active_review_untouched(self) -> None:
        with ImageAnalysisRepository(self.database) as repository:
            pending = repository.add_item(self.item("pending.png"))
            processing = repository.add_item(self.item("processing.png"))
            repository.transition(processing, AnalysisState.PROCESSING)
            ready = repository.add_item(self.item("ready.png"))
            repository.transition(ready, AnalysisState.PROCESSING)
            repository.transition(ready, AnalysisState.READY_FOR_REVIEW)
            repository.activate_review(ready)
            changed, retained = repository.clean_queue("active")
            self.assertEqual((changed, retained), (1, 2))
            self.assertFalse(repository.item_queue_visible(pending))
            self.assertTrue(repository.item_queue_visible(processing))
            self.assertTrue(repository.item_queue_visible(ready))

    def test_insert_tags_artists_and_foreign_keys(self) -> None:
        remote = AnalysisItem(
            SourceReference(InputKind.E621_POST, site="e621", post_id="42"),
            cached_path=Path("cached.png"), content_sha256="b" * 64,
            mime_type="image/png", width=10, height=20,
        )
        with ImageAnalysisRepository(self.database) as repository:
            item_id = repository.add_item(
                remote,
                (SourceTag("artist_name", ObservationSource.E621, "artist"),),
                ("artist_name",),
            )
            self.assertEqual(repository.source_tags(item_id)[0].source, "e621")
            self.assertEqual(repository.artist_tags(item_id), ("artist_name",))
            with self.assertRaises(sqlite3.IntegrityError):
                repository.connection.execute(
                    "INSERT INTO source_tags VALUES(999,'e621','missing',NULL,'now')"
                )

    def test_same_remote_post_reuses_existing_queue_item(self) -> None:
        with ImageAnalysisRepository(self.database) as repository:
            first = repository.add_unresolved_remote(InputKind.GELBOORU_POST, "123456")
            second = repository.add_unresolved_remote(InputKind.GELBOORU_POST, "123456")
            self.assertEqual(first, second)
            self.assertEqual(repository.connection.execute(
                "SELECT COUNT(*) FROM analysis_items"
            ).fetchone()[0], 1)

    def test_transitions_claim_and_recovery(self) -> None:
        with ImageAnalysisRepository(self.database) as repository:
            item_id = repository.add_item(self.item())
            claimed = repository.claim_next()
            self.assertEqual(claimed.id, item_id)
            self.assertEqual(claimed.state, AnalysisState.PROCESSING)
            self.assertIsNone(repository.claim_next())
            self.assertEqual(repository.recover_interrupted(), 1)
            self.assertEqual(repository.get_item(item_id).state, AnalysisState.PENDING)
            repository.transition(item_id, AnalysisState.PROCESSING)
            repository.transition(item_id, AnalysisState.READY_FOR_REVIEW)
            repository.transition(item_id, AnalysisState.REVIEWED)
            with self.assertRaises(ValueError):
                repository.transition(item_id, AnalysisState.PENDING)

    def test_active_review_can_switch_and_skipped_item_can_be_requeued_without_inference(self) -> None:
        with ImageAnalysisRepository(self.database) as repository:
            ready_ids = []
            for name in ("first.png", "second.png"):
                item_id = repository.add_item(self.item(name))
                repository.transition(item_id, AnalysisState.PROCESSING)
                repository.transition(item_id, AnalysisState.READY_FOR_REVIEW)
                ready_ids.append(item_id)
            first = repository.activate_next_review()
            self.assertEqual(first.id, ready_ids[0])
            second = repository.activate_review(ready_ids[1])
            self.assertEqual(second.id, ready_ids[1])
            active = repository.connection.execute(
                "SELECT id FROM analysis_items WHERE review_active=1"
            ).fetchone()[0]
            self.assertEqual(active, ready_ids[1])
            repository.finish_review(ready_ids[1], AnalysisState.SKIPPED)
            repository.requeue_skipped(ready_ids[1])
            self.assertEqual(repository.get_item(ready_ids[1]).state, AnalysisState.READY_FOR_REVIEW)
            self.assertEqual(repository.connection.execute(
                "SELECT COUNT(*) FROM model_runs"
            ).fetchone()[0], 0)

    def test_bulk_reopen_skipped_preserves_existing_observations(self) -> None:
        with ImageAnalysisRepository(self.database) as repository:
            ids = [repository.add_item(self.item(f"skipped-{index}.png")) for index in range(2)]
            for item_id in ids:
                repository.transition(item_id, AnalysisState.PROCESSING)
                repository.transition(item_id, AnalysisState.READY_FOR_REVIEW)
                repository.finish_review(item_id, AnalysisState.SKIPPED)
            self.assertEqual(repository.requeue_skipped_many(ids), 2)
            self.assertTrue(all(repository.get_item(item_id).state is AnalysisState.READY_FOR_REVIEW for item_id in ids))
            self.assertEqual(repository.connection.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0], 0)

    def test_two_connections_claim_different_jobs_atomically(self) -> None:
        with ImageAnalysisRepository(self.database) as repository:
            first = repository.add_item(self.item("first.png"))
            second = repository.add_item(self.item("second.png"))

        def claim() -> int | None:
            with ImageAnalysisRepository(self.database) as repository:
                item = repository.claim_next()
                return item.id if item else None

        with ThreadPoolExecutor(max_workers=2) as executor:
            claimed = {result for result in executor.map(lambda _value: claim(), range(2))}
        self.assertEqual(claimed, {first, second})

    def test_interactive_claim_bypasses_prefetch_and_ui_visibility(self) -> None:
        with ImageAnalysisRepository(self.database) as repository:
            for name in ("ready-one.png", "ready-two.png"):
                ready = repository.add_item(self.item(name))
                repository.transition(ready, AnalysisState.PROCESSING)
                repository.transition(ready, AnalysisState.READY_FOR_REVIEW)
            interactive = repository.add_item(self.item("interactive.png"))
            repository.connection.execute(
                "UPDATE analysis_items SET queue_visible=0 WHERE id=?", (interactive,)
            )
            repository.connection.commit()
            repository.request_analysis(interactive, 100)
            claimed = repository.claim_next(analysis_prefetch=2)
            self.assertEqual(claimed.id, interactive)
            self.assertEqual(claimed.state, AnalysisState.PROCESSING)

    def test_background_claim_respects_prefetch_limit_and_reports_reason(self) -> None:
        with ImageAnalysisRepository(self.database) as repository:
            for name in ("ready-one.png", "ready-two.png"):
                ready = repository.add_item(self.item(name))
                repository.transition(ready, AnalysisState.PROCESSING)
                repository.transition(ready, AnalysisState.READY_FOR_REVIEW)
            repository.add_item(self.item("background.png"))
            self.assertIsNone(repository.claim_next(analysis_prefetch=2))
            diagnostic = repository.scheduler_diagnostic(2)
            self.assertEqual(diagnostic["reason"], "prefetch_limit")

    def test_same_hash_does_not_merge_distinct_sources(self) -> None:
        with ImageAnalysisRepository(self.database) as repository:
            first = repository.add_item(self.item("first.png"))
            second = repository.add_item(self.item("second.png"))
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
