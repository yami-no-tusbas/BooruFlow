import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from booruflow.domain.image_analysis import (
    AnalysisState,
    DecisionState,
    InputKind,
    ModelIdentity,
)
from booruflow.infrastructure.classic_image_analysis import (
    ClassicAnalysisConfig,
    ClassicImageAnalyzer,
)
from booruflow.infrastructure.hydra import HydraResult, HydraTag
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import ImageSourceService, NormalizedPost
from booruflow.infrastructure.wd14 import WD14Result, WD14Tag, WD14UnavailableError
from booruflow.worker import image_analysis as worker_module
from booruflow.worker.image_analysis import ImageAnalysisWorker, _merge_results, _watch_parent


class WorkerLifecycleTests(unittest.TestCase):
    def test_bootstrap_records_early_startup_and_worker_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "state.sqlite"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "booruflow.worker.image_analysis_bootstrap",
                    "--database",
                    str(database),
                    "--parent-pid",
                    "0",
                    "--once",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )

            trace = (root / "worker-bootstrap.log").read_text(encoding="utf-8")
            self.assertIn("process entry", trace)
            self.assertIn("parent_pid received=0", trace)
            self.assertIn("SQLite connected", trace)
            self.assertIn("worker session created", trace)
            self.assertIn("early parent watchdog started", trace)
            self.assertIn("cooperative stop watcher started", trace)
            self.assertIn("interpreter=", trace)
            self.assertIn("[INFO] [Worker] Worker started", completed.stdout)
            self.assertIn("READY ", completed.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows parent-handle behavior")
    def test_invalid_parent_handle_disables_watchdog_without_exiting(self) -> None:
        messages: list[str] = []

        _watch_parent(2_147_483_647, threading.Event(), messages.append)

        self.assertTrue(any("watchdog disabled" in message for message in messages))

    def test_bootstrap_stops_cooperatively_and_leaves_no_worker_process(self) -> None:
        from booruflow.presentation.pyside6.image_analysis_controller import _pid_is_running

        with tempfile.TemporaryDirectory() as temporary:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    "-m",
                    "booruflow.worker.image_analysis_bootstrap",
                    "--database",
                    str(Path(temporary) / "state.sqlite"),
                    "--parent-pid",
                    str(os.getpid()),
                    "--poll-interval",
                    "0.5",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            output = []
            worker_pid = 0
            deadline = time.monotonic() + 10
            assert process.stdout is not None
            while time.monotonic() < deadline:
                line = process.stdout.readline()
                output.append(line)
                match = re.search(r"READY .* pid=(\d+)", line)
                if match:
                    worker_pid = int(match.group(1))
                    break
            self.assertGreater(worker_pid, 0, "".join(output))
            assert process.stdin is not None
            process.stdin.write("STOP\n")
            process.stdin.flush()
            process.wait(timeout=10)
            self.assertEqual(process.returncode, 0)
            deadline = time.monotonic() + 2
            while _pid_is_running(worker_pid) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(_pid_is_running(worker_pid))

    def test_stop_during_controlled_wd14_prepare_closes_backend_before_exit(self) -> None:
        stop = threading.Event()
        traces = []
        closed = []

        class ControlledWD14:
            def __init__(self, _config):
                pass

            def prepare(self, trace):
                trace("controlled WD14 prepare")
                stop.set()

            def close(self):
                closed.append(True)

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(worker_module, "WD14Backend", ControlledWD14),
        ):
            root = Path(temporary)
            code = worker_module.main(
                [
                    "--database",
                    str(root / "state.sqlite"),
                    "--wd14-enabled",
                    "--wd14-model-directory",
                    str(root / "model"),
                    "--once",
                ],
                bootstrap_log=traces.append,
                external_stop=stop,
                parent_watchdog_started=True,
            )
        self.assertEqual(code, 0)
        self.assertEqual(closed, [True])
        self.assertIn("stop observed after WD14 prepare", traces)

    def test_stdin_watcher_starts_only_after_wd14_prepare(self) -> None:
        started = []
        during_prepare = []

        class ControlledWD14:
            def __init__(self, _config):
                self.provider = "CPUExecutionProvider"
                self.device = "CPU"
                self.runtime = "test"
                self.runtime_diagnostic = type(
                    "Diagnostic",
                    (),
                    {
                        "runtime_version": "test",
                        "expected_cuda": "",
                        "expected_cudnn": "",
                        "cuda_runtime_installed": False,
                        "cudnn_installed": False,
                        "gpu_devices": (),
                        "effective_provider": "CPUExecutionProvider",
                    },
                )()

            def prepare(self, _trace):
                during_prepare.extend(started)

            def close(self):
                pass

        class DeferredThread:
            def __init__(self, target, args=(), daemon=None):
                self.target = target

            def start(self):
                started.append(self.target)

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(worker_module, "WD14Backend", ControlledWD14),
            patch.object(worker_module.threading, "Thread", DeferredThread),
        ):
            root = Path(temporary)
            code = worker_module.main(
                [
                    "--database",
                    str(root / "state.sqlite"),
                    "--wd14-enabled",
                    "--wd14-model-directory",
                    str(root / "model"),
                    "--once",
                ],
                parent_watchdog_started=True,
            )
        self.assertEqual(code, 0)
        self.assertNotIn(worker_module._watch_stdin, during_prepare)
        self.assertIn(worker_module._watch_stdin, started)

    def test_wd14_unavailable_does_not_start_a_classic_only_worker(self) -> None:
        class UnavailableWD14:
            def __init__(self, _config):
                pass

            def prepare(self, _trace):
                raise WD14UnavailableError("test model is unavailable")

            def close(self):
                pass

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(worker_module, "WD14Backend", UnavailableWD14),
            patch("sys.stdout", new_callable=StringIO) as output,
        ):
            code = worker_module.main(
                [
                    "--database",
                    str(Path(temporary) / "state.sqlite"),
                    "--wd14-enabled",
                    "--wd14-model-directory",
                    str(Path(temporary) / "model"),
                    "--once",
                ],
                parent_watchdog_started=True,
            )
        self.assertEqual(code, 2)
        self.assertIn("WD14_UNAVAILABLE test model is unavailable", output.getvalue())
        self.assertNotIn("READY ", output.getvalue())


class FakeWD14:
    identity = ModelIdentity("wd14", "test", "v1", "config", "cpu")
    runtime = "fake"

    def analyze(self, _path):
        return WD14Result((WD14Tag("blue hair", "general", 0.8),))


class ImageAnalysisWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite"
        self.repository = ImageAnalysisRepository(self.database)
        self.sources = ImageSourceService(self.repository, self.root / "cache")

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary.cleanup()

    def image(self, name: str = "image.png") -> int:
        color = sum(name.encode("utf-8")) % 256
        path = self.root / name
        Image.new("RGB", (10, 10), (color, color, color)).save(path)
        return self.sources.add_local(path)

    def test_claim_heartbeat_analysis_and_result(self) -> None:
        item_id = self.image()
        worker = ImageAnalysisWorker(self.repository, [ClassicImageAnalyzer()])
        self.assertTrue(worker.process_one())
        self.assertEqual(self.repository.get_item(item_id).state, AnalysisState.READY_FOR_REVIEW)
        self.assertIsNotNone(self.repository.statistics(item_id))
        row = self.repository.connection.execute(
            "SELECT processing_heartbeat_at FROM analysis_items WHERE id=?", (item_id,)
        ).fetchone()
        self.assertIsNotNone(row[0])

    def test_wd14_required_without_backend_fails_item_instead_of_marking_it_ready(self) -> None:
        item_id = self.image()
        worker = ImageAnalysisWorker(
            self.repository,
            [ClassicImageAnalyzer()],
            require_wd14=True,
        )

        self.assertTrue(worker.process_one())
        self.assertEqual(self.repository.get_item(item_id).state, AnalysisState.FAILED)
        self.assertIsNone(self.repository.statistics(item_id))

    def test_error_does_not_stop_next_item(self) -> None:
        missing_id = self.image("missing.png")
        self.repository.get_item(missing_id).cached_path.unlink()
        good_id = self.image("good.png")
        worker = ImageAnalysisWorker(self.repository, [ClassicImageAnalyzer()])
        self.assertTrue(worker.process_one())
        self.assertTrue(worker.process_one())
        self.assertEqual(self.repository.get_item(missing_id).state, AnalysisState.FAILED)
        self.assertEqual(self.repository.get_item(good_id).state, AnalysisState.READY_FOR_REVIEW)

    def test_stale_crash_is_recovered_and_reprocessed(self) -> None:
        item_id = self.image()
        self.repository.claim_next()
        old = (datetime.now(UTC) - timedelta(minutes=5)).isoformat(timespec="seconds")
        self.repository.connection.execute(
            "UPDATE analysis_items SET processing_heartbeat_at=? WHERE id=?", (old, item_id)
        )
        self.repository.connection.commit()
        self.repository.close()
        self.repository = ImageAnalysisRepository(self.database)
        self.assertEqual(self.repository.recover_interrupted(datetime.now(UTC).isoformat()), 1)
        ImageAnalysisWorker(self.repository, [ClassicImageAnalyzer()]).process_one()
        self.assertEqual(self.repository.get_item(item_id).state, AnalysisState.READY_FOR_REVIEW)

    def test_same_identity_is_cached_but_new_config_creates_run(self) -> None:
        item_id = self.image()
        analyzer = ClassicImageAnalyzer()
        worker = ImageAnalysisWorker(self.repository, [analyzer])
        worker.process_one()
        count = self.repository.connection.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0]
        self.assertIsNone(
            self.repository.begin_model_run(
                item_id,
                analyzer.identity.backend,
                analyzer.identity.name,
                analyzer.identity.version,
                analyzer.identity.configuration_hash,
            )
        )
        changed = ClassicImageAnalyzer(ClassicAnalysisConfig(palette_size=5))
        self.assertIsNotNone(
            self.repository.begin_model_run(
                item_id,
                changed.identity.backend,
                changed.identity.name,
                changed.identity.version,
                changed.identity.configuration_hash,
            )
        )
        self.assertEqual(count, 1)

    def test_wd14_predictions_are_normalized_and_persisted(self) -> None:
        item_id = self.image()
        ImageAnalysisWorker(self.repository, [FakeWD14()]).process_one()
        observations = self.repository.observations(item_id)
        self.assertEqual(observations[0][1].name, "blue_hair")
        self.assertEqual(observations[0][1].raw_tag_name, "blue hair")
        self.assertEqual(observations[0][1].category, "general")

    def test_e621_remote_resolution_runs_hydra_only_and_reuses_cache(self) -> None:
        """Exercise the normal remote resolver without an e621 network call."""
        source_path = self.root / "e621-source.png"
        Image.new("RGB", (17, 11), (20, 40, 60)).save(source_path)

        class LocalE621Provider:
            def fetch_post(self, post_id: str) -> NormalizedPost:
                return NormalizedPost("e621", post_id, "https://local.invalid/e621.png", (), ())

        class FakeHydra:
            supported_sites = frozenset({"e621"})
            identity = ModelIdentity("hydra", "RedRocket/Hydra", "3.5", "test-hash", "cpu")
            runtime = "fake"

            def __init__(self) -> None:
                self.calls = 0

            def analyze(self, _path: Path) -> HydraResult:
                self.calls += 1
                return HydraResult((HydraTag("e621_tag", "general", 0.91),))

        class ExplodingWD14:
            supported_sites = frozenset({None, "gelbooru"})
            identity = ModelIdentity("wd14", "should-not-run", "test", "test", "cpu")
            runtime = "fake"

            def analyze(self, _path: Path):
                raise AssertionError("WD14 must not analyze an e621 item")

        resolver = ImageSourceService(
            self.repository,
            self.root / "remote-cache",
            bytes_fetcher=lambda _url, _headers: source_path.read_bytes(),
        )
        item_id = self.repository.add_unresolved_remote(InputKind.E621_POST, "local-e621-1")
        resolver.resolve_post(item_id, LocalE621Provider(), "local-e621-1")
        item = self.repository.get_item(item_id)
        assert item is not None
        self.assertEqual(item.source.kind, InputKind.E621_POST)
        self.assertEqual(item.source.site, "e621")
        self.assertEqual(item.source.post_id, "local-e621-1")
        self.assertEqual(item.state, AnalysisState.PENDING)
        self.assertIsNotNone(item.content_sha256)
        self.assertTrue(item.cached_path and item.cached_path.is_file())
        self.assertEqual(item.mime_type, "image/png")
        self.assertEqual((item.width, item.height), (17, 11))
        self.assertEqual(resolver.validate_item_file(item), item.cached_path)

        hydra = FakeHydra()
        worker = ImageAnalysisWorker(self.repository, [ExplodingWD14(), hydra])
        self.assertTrue(worker.process_one())
        self.assertEqual(hydra.calls, 1)
        self.assertEqual(self.repository.get_item(item_id).state, AnalysisState.READY_FOR_REVIEW)
        observation = self.repository.observations(item_id)[0][1]
        self.assertEqual(observation.source.value, "hydra")
        self.assertEqual(observation.name, "e621_tag")
        self.assertEqual(observation.raw_tag_name, "e621_tag")
        self.assertEqual(observation.confidence, 0.91)
        run = self.repository.connection.execute(
            "SELECT model_name, model_version FROM model_runs WHERE item_id=? AND backend='hydra'",
            (item_id,),
        ).fetchone()
        self.assertEqual((run["model_name"], run["model_version"]), ("RedRocket/Hydra", "3.5"))

        self.assertIsNone(
            self.repository.begin_model_run(
                item_id, "hydra", "RedRocket/Hydra", "3.5", "test-hash"
            )
        )
        self.assertEqual(hydra.calls, 1)
        self.assertEqual(
            self.repository.connection.execute(
                "SELECT COUNT(*) FROM model_runs WHERE item_id=? AND backend='hydra'", (item_id,)
            ).fetchone()[0],
            1,
        )

    def test_manual_reanalysis_reuses_item_and_replaces_old_wd14_observations(self) -> None:
        item_id = self.image()
        worker = ImageAnalysisWorker(self.repository, [FakeWD14()])
        worker.process_one()
        self.repository.add_manual_observation(item_id, "manual_tag")

        self.repository.reanalyze(item_id, priority=100)

        item = self.repository.get_item(item_id)
        self.assertEqual(item.state, AnalysisState.PENDING)
        self.assertEqual(
            self.repository.connection.execute(
                "SELECT priority FROM analysis_items WHERE id=?", (item_id,)
            ).fetchone()[0],
            100,
        )
        self.assertEqual(
            [observation.source.value for _id, observation in self.repository.observations(item_id)],
            ["manual"],
        )
        self.assertEqual(self.repository.claim_next(analysis_prefetch=1).id, item_id)
        self.repository.transition(item_id, AnalysisState.PENDING)
        worker.process_one()
        observations = self.repository.observations(item_id)
        self.assertEqual(len([row for row in observations if row[1].source.value == "wd14"]), 1)
        self.assertEqual(len([row for row in observations if row[1].source.value == "manual"]), 1)

    def test_wd14_active_alias_is_canonicalized_without_losing_raw_diagnostic(self) -> None:
        from booruflow.infrastructure.gelbooru_aliases import (
            AliasRelation,
            GelbooruAliasRepository,
            ensure_alias_schema,
        )

        aliases_path = self.root / "gelbooru-tags.sqlite"
        ensure_alias_schema(aliases_path)
        GelbooruAliasRepository(aliases_path).upsert(
            AliasRelation("china_dress", "qipao", "active")
        )
        item_id = self.image()

        class AliasWD14(FakeWD14):
            def analyze(self, _path):
                return WD14Result((WD14Tag("china_dress", "general", 0.9),))

        ImageAnalysisWorker(
            self.repository, [AliasWD14()], alias_database=aliases_path
        ).process_one()
        observation = self.repository.observations(item_id)[0][1]
        self.assertEqual(observation.name, "qipao")
        self.assertEqual(observation.raw_tag_name, "china_dress")
        self.repository.decide_observation(
            self.repository.observations(item_id)[0][0], DecisionState.ACCEPTED
        )
        self.assertEqual(
            self.repository.tag_review_summary(item_id, [])["additions"], ["qipao"]
        )
        summary = self.repository.tag_review_summary(item_id, [])
        self.repository.save_review_batch_entry(
            item_id,
            original_tags=summary["original_tags"],
            additions=summary["additions"],
            removals=summary["removals"],
            reviewed_final_tags=summary["final_tags"],
        )
        self.assertEqual(self.repository.batch_entry(item_id)["additions"], ["qipao"])

    def test_e621_wd14_context_never_builds_a_gelbooru_alias_resolver(self) -> None:
        from booruflow.worker.image_analysis import wd14_canonicalizer

        self.assertIsNone(wd14_canonicalizer("e621", self.root / "aliases.sqlite"))
        self.assertIsNotNone(wd14_canonicalizer("gelbooru", None))

    def test_wd14_multi_frame_merge_keeps_each_best_score_deterministically(self) -> None:
        first = WD14Result(
            (
                WD14Tag("character_a", "character", 0.62),
                WD14Tag("blue_hair", "general", 0.80),
                WD14Tag("safe", "rating", 0.99),
            )
        )
        second = WD14Result(
            (
                WD14Tag("character_a", "character", 0.91),
                WD14Tag("smile", "general", 0.70),
                WD14Tag("safe", "rating", 0.95),
            )
        )
        expected = _merge_results([first, second])
        reversed_result = _merge_results([second, first])
        self.assertEqual(expected, reversed_result)
        self.assertEqual(
            {(tag.raw_name, tag.category): tag.score for tag in expected.predictions},
            {
                ("blue_hair", "general"): 0.80,
                ("character_a", "character"): 0.91,
                ("safe", "rating"): 0.99,
                ("smile", "general"): 0.70,
            },
        )

    def test_interactive_item_reaches_ready_despite_two_ready_prefetch_items(self) -> None:
        for name in ("ready-one.png", "ready-two.png"):
            item_id = self.image(name)
            self.repository.transition(item_id, AnalysisState.PROCESSING)
            self.repository.transition(item_id, AnalysisState.READY_FOR_REVIEW)
        interactive = self.image("tagging-interactive.png")
        self.repository.connection.execute(
            "UPDATE analysis_items SET queue_visible=0 WHERE id=?", (interactive,)
        )
        self.repository.connection.commit()
        self.repository.request_analysis(interactive, 100)
        worker = ImageAnalysisWorker(self.repository, [ClassicImageAnalyzer()], analysis_prefetch=2)
        self.assertTrue(worker.process_one())
        self.assertEqual(
            self.repository.get_item(interactive).state, AnalysisState.READY_FOR_REVIEW
        )

    def test_worker_runs_in_a_separate_process_and_stops_cleanly(self) -> None:
        item_id = self.image()
        self.repository.close()
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            (
                str(Path(__file__).resolve().parents[2]),
                str(Path(__file__).resolve().parents[2] / "src"),
                environment.get("PYTHONPATH", ""),
            )
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "booruflow.worker.image_analysis",
                "--database",
                str(self.database),
                "--once",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("READY", result.stdout)
        self.repository = ImageAnalysisRepository(self.database)
        self.assertEqual(self.repository.get_item(item_id).state, AnalysisState.READY_FOR_REVIEW)

    def test_worker_accepts_cooperative_stop_over_stdin(self) -> None:
        self.repository.close()
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "booruflow.worker.image_analysis",
                "--database",
                str(self.database),
                "--poll-interval",
                "1",
            ],
            cwd=Path(__file__).resolve().parents[2],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 10
        output = ""
        while "READY " not in output and time.monotonic() < deadline:
            output += process.stdout.readline()
        self.assertIn("pid=", output)
        process.stdin.write("STOP\n")
        process.stdin.flush()
        self.assertEqual(process.wait(timeout=5), 0)
        output += process.stdout.read()
        self.assertIn("Exited pid=", output)
        self.repository = ImageAnalysisRepository(self.database)


if __name__ == "__main__":
    unittest.main()
