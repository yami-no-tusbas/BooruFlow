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
            self.assertEqual(repository.connection.execute("PRAGMA user_version").fetchone()[0], 14)
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
            self.assertEqual(reopened.connection.execute("PRAGMA user_version").fetchone()[0], 14)

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
