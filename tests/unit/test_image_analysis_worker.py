import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from booruflow.domain.image_analysis import AnalysisState, ModelIdentity
from booruflow.infrastructure.classic_image_analysis import (
    ClassicAnalysisConfig,
    ClassicImageAnalyzer,
)
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import ImageSourceService
from booruflow.infrastructure.wd14 import WD14Result, WD14Tag
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
            process=subprocess.Popen([
                sys.executable,"-u","-m","booruflow.worker.image_analysis_bootstrap",
                "--database",str(Path(temporary)/"state.sqlite"),"--parent-pid",str(os.getpid()),
                "--poll-interval","0.5",
            ],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            output=[]; worker_pid=0; deadline=time.monotonic()+10
            assert process.stdout is not None
            while time.monotonic()<deadline:
                line=process.stdout.readline(); output.append(line)
                match=re.search(r"READY .* pid=(\d+)",line)
                if match:
                    worker_pid=int(match.group(1)); break
            self.assertGreater(worker_pid,0,"".join(output))
            assert process.stdin is not None
            process.stdin.write("STOP\n"); process.stdin.flush(); process.wait(timeout=10)
            self.assertEqual(process.returncode,0)
            deadline=time.monotonic()+2
            while _pid_is_running(worker_pid) and time.monotonic()<deadline: time.sleep(0.02)
            self.assertFalse(_pid_is_running(worker_pid))

    def test_stop_during_controlled_wd14_prepare_closes_backend_before_exit(self) -> None:
        stop=threading.Event(); traces=[]; closed=[]
        class ControlledWD14:
            def __init__(self,_config): pass
            def prepare(self,trace): trace("controlled WD14 prepare"); stop.set()
            def close(self): closed.append(True)
        with tempfile.TemporaryDirectory() as temporary, patch.object(worker_module,"WD14Backend",ControlledWD14):
            root=Path(temporary)
            code=worker_module.main([
                "--database",str(root/"state.sqlite"),"--wd14-enabled",
                "--wd14-model-directory",str(root/"model"),"--once",
            ],bootstrap_log=traces.append,external_stop=stop,parent_watchdog_started=True)
        self.assertEqual(code,0); self.assertEqual(closed,[True])
        self.assertIn("stop observed after WD14 prepare",traces)

    def test_stdin_watcher_starts_only_after_wd14_prepare(self) -> None:
        started=[]; during_prepare=[]
        class ControlledWD14:
            def __init__(self,_config):
                self.provider="CPUExecutionProvider";self.device="CPU";self.runtime="test"
                self.runtime_diagnostic=type("Diagnostic",(),{
                    "runtime_version":"test","expected_cuda":"","expected_cudnn":"",
                    "cuda_runtime_installed":False,"cudnn_installed":False,
                    "gpu_devices":(),"effective_provider":"CPUExecutionProvider",
                })()
            def prepare(self,_trace): during_prepare.extend(started)
            def close(self): pass
        class DeferredThread:
            def __init__(self,target,args=(),daemon=None): self.target=target
            def start(self): started.append(self.target)
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(worker_module,"WD14Backend",ControlledWD14), \
             patch.object(worker_module.threading,"Thread",DeferredThread):
            root=Path(temporary)
            code=worker_module.main([
                "--database",str(root/"state.sqlite"),"--wd14-enabled",
                "--wd14-model-directory",str(root/"model"),"--once",
            ],parent_watchdog_started=True)
        self.assertEqual(code,0)
        self.assertNotIn(worker_module._watch_stdin,during_prepare)
        self.assertIn(worker_module._watch_stdin,started)


class FakeWD14:
    identity = ModelIdentity("wd14", "test", "v1", "config", "cpu")
    runtime = "fake"

    def analyze(self, _path):
        return WD14Result((WD14Tag("blue hair", "general", 0.8),))


class ImageAnalysisWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(); self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite"
        self.repository = ImageAnalysisRepository(self.database)
        self.sources = ImageSourceService(self.repository, self.root / "cache")

    def tearDown(self) -> None:
        self.repository.close(); self.temporary.cleanup()

    def image(self, name: str = "image.png") -> int:
        color = sum(name.encode("utf-8")) % 256
        path = self.root / name; Image.new("RGB", (10, 10), (color, color, color)).save(path)
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

    def test_error_does_not_stop_next_item(self) -> None:
        missing_id = self.image("missing.png")
        self.repository.get_item(missing_id).cached_path.unlink()
        good_id = self.image("good.png")
        worker = ImageAnalysisWorker(self.repository, [ClassicImageAnalyzer()])
        self.assertTrue(worker.process_one()); self.assertTrue(worker.process_one())
        self.assertEqual(self.repository.get_item(missing_id).state, AnalysisState.FAILED)
        self.assertEqual(self.repository.get_item(good_id).state, AnalysisState.READY_FOR_REVIEW)

    def test_stale_crash_is_recovered_and_reprocessed(self) -> None:
        item_id = self.image()
        self.repository.claim_next()
        old = (datetime.now(UTC) - timedelta(minutes=5)).isoformat(timespec="seconds")
        self.repository.connection.execute(
            "UPDATE analysis_items SET processing_heartbeat_at=? WHERE id=?", (old, item_id)
        ); self.repository.connection.commit(); self.repository.close()
        self.repository = ImageAnalysisRepository(self.database)
        self.assertEqual(self.repository.recover_interrupted(datetime.now(UTC).isoformat()), 1)
        ImageAnalysisWorker(self.repository, [ClassicImageAnalyzer()]).process_one()
        self.assertEqual(self.repository.get_item(item_id).state, AnalysisState.READY_FOR_REVIEW)

    def test_same_identity_is_cached_but_new_config_creates_run(self) -> None:
        item_id = self.image()
        analyzer = ClassicImageAnalyzer()
        worker = ImageAnalysisWorker(self.repository, [analyzer]); worker.process_one()
        count = self.repository.connection.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0]
        self.assertIsNone(self.repository.begin_model_run(
            item_id, analyzer.identity.backend, analyzer.identity.name,
            analyzer.identity.version, analyzer.identity.configuration_hash,
        ))
        changed = ClassicImageAnalyzer(ClassicAnalysisConfig(palette_size=5))
        self.assertIsNotNone(self.repository.begin_model_run(
            item_id, changed.identity.backend, changed.identity.name,
            changed.identity.version, changed.identity.configuration_hash,
        ))
        self.assertEqual(count, 1)

    def test_wd14_predictions_are_normalized_and_persisted(self) -> None:
        item_id = self.image()
        ImageAnalysisWorker(self.repository, [FakeWD14()]).process_one()
        observations = self.repository.observations(item_id)
        self.assertEqual(observations[0][1].name, "blue_hair")
        self.assertEqual(observations[0][1].raw_tag_name, "blue hair")
        self.assertEqual(observations[0][1].category, "general")

    def test_wd14_multi_frame_merge_keeps_each_best_score_deterministically(self) -> None:
        first = WD14Result((
            WD14Tag("character_a", "character", 0.62),
            WD14Tag("blue_hair", "general", 0.80),
            WD14Tag("safe", "rating", 0.99),
        ))
        second = WD14Result((
            WD14Tag("character_a", "character", 0.91),
            WD14Tag("smile", "general", 0.70),
            WD14Tag("safe", "rating", 0.95),
        ))
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
        worker = ImageAnalysisWorker(
            self.repository, [ClassicImageAnalyzer()], analysis_prefetch=2
        )
        self.assertTrue(worker.process_one())
        self.assertEqual(
            self.repository.get_item(interactive).state, AnalysisState.READY_FOR_REVIEW
        )

    def test_worker_runs_in_a_separate_process_and_stops_cleanly(self) -> None:
        item_id = self.image()
        self.repository.close()
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join((
            str(Path(__file__).resolve().parents[2]),
            str(Path(__file__).resolve().parents[2] / "src"),
            environment.get("PYTHONPATH", ""),
        ))
        result = subprocess.run(
            [sys.executable, "-m", "booruflow.worker.image_analysis",
             "--database", str(self.database), "--once"],
            cwd=Path(__file__).resolve().parents[2], env=environment,
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("READY", result.stdout)
        self.repository = ImageAnalysisRepository(self.database)
        self.assertEqual(self.repository.get_item(item_id).state, AnalysisState.READY_FOR_REVIEW)

    def test_worker_accepts_cooperative_stop_over_stdin(self) -> None:
        self.repository.close()
        process = subprocess.Popen(
            [sys.executable, "-m", "booruflow.worker.image_analysis",
             "--database", str(self.database), "--poll-interval", "1"],
            cwd=Path(__file__).resolve().parents[2], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        deadline = time.monotonic() + 10
        output = ""
        while "READY " not in output and time.monotonic() < deadline:
            output += process.stdout.readline()
        self.assertIn("pid=", output)
        process.stdin.write("STOP\n"); process.stdin.flush()
        self.assertEqual(process.wait(timeout=5), 0)
        output += process.stdout.read()
        self.assertIn("Exited pid=", output)
        self.repository = ImageAnalysisRepository(self.database)


if __name__ == "__main__":
    unittest.main()
