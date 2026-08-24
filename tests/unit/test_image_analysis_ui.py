import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None
LANGUAGES = Path(__file__).resolve().parents[2] / "resources" / "i18n"


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is not installed")
class ImageAnalysisUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(); self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_page_add_sources_filter_and_resize(self) -> None:
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        page = ImageAnalysisPage(LanguageCatalog(LANGUAGES, "fr"))
        captured = []
        page.remote_ids_requested.connect(lambda site, ids: captured.append((site, ids)))
        page.gelbooru_ids.setText("12, 13 14"); page.gelbooru_button.click()
        self.assertEqual(captured, [("gelbooru", ["12", "13", "14"])])
        page.resize(1050, 720); page.show(); self.app.processEvents()
        self.assertGreaterEqual(page.image.width(), 320)
        page.close()

    def test_persistent_action_bar_and_internal_zoom_at_supported_window_sizes(self) -> None:
        from types import SimpleNamespace

        from PySide6.QtCore import Qt

        from booruflow.domain.image_analysis import AnalysisState, ObservationSource, TagObservation
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        from booruflow.presentation.pyside6.pages import ScrollablePageHost
        image_path = self.root / "large.png"; Image.new("RGB", (2400, 1600)).save(image_path)
        page = ImageAnalysisPage(LanguageCatalog(LANGUAGES, "en"))
        host = ScrollablePageHost(page); host.show()
        for width, height in ((1280, 720), (1600, 900), (1920, 1080)):
            host.resize(width, height); self.app.processEvents()
            self.assertTrue(page.action_bar.isVisible())
            self.assertLessEqual(page.action_bar.geometry().bottom(), page.rect().bottom())
            self.assertEqual(host.verticalScrollBar().maximum(), 0)
            self.assertEqual(host.horizontalScrollBar().maximum(), 0)
        self.assertEqual(page.central_splitter.orientation(), Qt.Orientation.Horizontal)
        self.assertEqual(page.right_splitter.orientation(), Qt.Orientation.Vertical)
        item = SimpleNamespace(id=1, cached_path=image_path, state=AnalysisState.READY_FOR_REVIEW)
        observation = TagObservation("tag", ObservationSource.WD14, .9)
        page.show_review(item, (), [(1, observation)], None); self.app.processEvents()
        preview_size = page.image.size()
        page.image.set_zoom(400); self.app.processEvents()
        self.assertEqual(page.image.size(), preview_size)
        self.assertGreater(page.image.horizontalScrollBar().maximum(), 0)
        self.assertTrue(page.complete_button.isEnabled())
        self.assertFalse(page.accept.isEnabled())
        page.observations.selectRow(0); self.app.processEvents()
        self.assertTrue(page.accept.isEnabled()); self.assertTrue(page.reject.isEnabled())
        failed = SimpleNamespace(id=2, cached_path=image_path, state=AnalysisState.FAILED)
        page.show_review(failed, (), [], None)
        self.assertTrue(page.retry_button.isEnabled())
        self.assertFalse(page.complete_button.isEnabled())
        host.close()

    def test_drop_single_multiple_unicode_invalid_and_duplicates(self) -> None:
        from PySide6.QtCore import QMimeData, QUrl

        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        class Event:
            def __init__(self, mime): self._mime = mime; self.accepted = False; self.ignored = False
            def mimeData(self): return self._mime
            def acceptProposedAction(self): self.accepted = True
            def ignore(self): self.ignored = True
        page = ImageAnalysisPage(LanguageCatalog(LANGUAGES, "fr")); captured = []
        page.local_sources_dropped.connect(captured.append)
        image = self.root / "image été (test) #1.png"; Image.new("RGB", (4, 4)).save(image)
        invalid = self.root / "not image.txt"; invalid.write_text("no", encoding="utf-8")
        mime = QMimeData(); mime.setUrls([
            QUrl.fromLocalFile(str(image)), QUrl.fromLocalFile(str(invalid)),
            QUrl.fromLocalFile(str(image)),
        ])
        enter = Event(mime); page.dragEnterEvent(enter)
        self.assertTrue(enter.accepted); self.assertFalse(page.drop_banner.isHidden())
        dropped = Event(mime); page.dropEvent(dropped)
        self.assertTrue(dropped.accepted)
        self.assertEqual(
            [[Path(value) for value in batch] for batch in captured],
            [[image, invalid, image]],
        )
        page.close()

    def test_drag_without_local_file_is_refused(self) -> None:
        from PySide6.QtCore import QMimeData, QUrl

        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        class Event:
            def __init__(self, mime): self._mime = mime; self.ignored = False
            def mimeData(self): return self._mime
            def acceptProposedAction(self): raise AssertionError("must not accept")
            def ignore(self): self.ignored = True
        page = ImageAnalysisPage(LanguageCatalog(LANGUAGES, "en"))
        mime = QMimeData(); mime.setUrls([QUrl("https://example.com/image.png")])
        event = Event(mime); page.dragEnterEvent(event); self.assertTrue(event.ignored)
        page.close()

    def test_dropped_folder_scan_is_recursive_and_deduplicated(self) -> None:
        from booruflow.presentation.pyside6.image_analysis_controller import DroppedSourceScanWorker
        folder = self.root / "folder"; nested = folder / "nested"; nested.mkdir(parents=True)
        first = folder / "one.png"; second = nested / "deux été.webp"
        Image.new("RGB", (3, 3)).save(first); Image.new("RGB", (3, 3)).save(second)
        (nested / "ignore.txt").write_text("x", encoding="utf-8")
        captured = []
        worker = DroppedSourceScanWorker([folder, first]); worker.completed.connect(
            lambda paths, ignored: captured.append((paths, ignored))
        )
        worker.run()
        self.assertEqual(captured[0][0], [str(first), str(second)])
        self.assertEqual(captured[0][1], 2)

    def test_source_batch_deduplicates_same_hash_and_reports_outcomes(self) -> None:
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.presentation.pyside6.image_analysis_controller import SourcePreparationWorker
        first = self.root / "A.png"; Image.new("RGB", (6, 6), "red").save(first)
        copy = self.root / "copy of A.png"; copy.write_bytes(first.read_bytes())
        second = self.root / "B.png"; Image.new("RGB", (6, 6), "blue").save(second)
        database = self.root / "state.sqlite"; captured = []
        worker = SourcePreparationWorker(
            database, self.root / "cache", [first, first, second, copy], 10, {}, None,
        )
        worker.completed.connect(lambda *values: captured.append(values)); worker.run()
        self.assertEqual(captured[0][2], {"new": 2, "already_queued": 2})
        with ImageAnalysisRepository(database) as repository:
            self.assertEqual(len(repository.list_items()), 2)
            item_id = repository.item_by_sha256(repository.get_item(1).content_sha256).id
            self.assertEqual(len(repository.provenances(item_id)), 2)

    def test_drop_and_button_converge_on_controller_import_service(self) -> None:
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_controller import ImageAnalysisController
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        page = ImageAnalysisPage(LanguageCatalog(LANGUAGES, "en"))
        controller = ImageAnalysisController(
            self.root, "python", page, {}, dict, lambda _message: None,
            auto_start_worker=False,
        )
        calls = []; controller.add_local_files = calls.append
        controller._drop_scan_finished(["one.png", "two.png"], 1)
        self.assertEqual(calls, [["one.png", "two.png"]])
        controller.shutdown(); page.close()

    def test_mixed_drop_is_validated_in_background_with_summary(self) -> None:
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_controller import ImageAnalysisController
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        valid = self.root / "valid image.png"; Image.new("RGB", (8, 8)).save(valid)
        invalid = self.root / "invalid.bin"; invalid.write_bytes(b"not an image")
        page = ImageAnalysisPage(LanguageCatalog(LANGUAGES, "en"))
        controller = ImageAnalysisController(
            self.root, sys.executable, page, {}, dict, lambda _message: None,
            auto_start_worker=False,
        )
        try:
            controller.scan_dropped_sources([str(valid), str(invalid), str(valid)])
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                self.app.processEvents(); time.sleep(0.02)
                if "ajoutée(s)" in page.drop_status.text():
                    break
            self.assertEqual(len(controller.repository.list_items()), 1)
            self.assertIn("1 nouvelle(s)", page.drop_status.text())
            self.assertIn("1 doublon(s) de chemin", page.drop_status.text())
            self.assertIn("1 invalide(s)", page.drop_status.text())
        finally:
            controller.shutdown(); page.close()

    def test_review_a_to_b_and_manual_decision_persist_immediately(self) -> None:
        from booruflow.domain.image_analysis import DecisionState
        from booruflow.infrastructure.classic_image_analysis import ClassicImageAnalyzer
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.infrastructure.image_sources import ImageSourceService
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_controller import ImageAnalysisController
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        from booruflow.worker.image_analysis import ImageAnalysisWorker
        database = self.root / "var" / "state" / "image_analysis.sqlite"
        with ImageAnalysisRepository(database) as repository:
            sources = ImageSourceService(repository, self.root / "var" / "cache")
            ids = []
            for index in range(2):
                path = self.root / f"{index}.png"
                Image.new("RGB", (12, 8), (index, index, index)).save(path)
                ids.append(sources.add_local(path))
            worker = ImageAnalysisWorker(repository, [ClassicImageAnalyzer()], analysis_prefetch=2)
            worker.process_one(); worker.process_one()
        page = ImageAnalysisPage(LanguageCatalog(LANGUAGES, "en"))
        controller = ImageAnalysisController(
            self.root, "python", page, {}, dict, lambda _message: None,
            auto_start_worker=False,
        )
        self.assertEqual(controller.current.id, ids[0])
        page.manual_tag.setText("manual_tag"); page.manual_add.click()
        with ImageAnalysisRepository(database) as check:
            observations = check.observations(ids[0])
            self.assertEqual(observations[0][1].name, "manual_tag")
            observation_id = observations[0][0]
        controller.decide(observation_id, DecisionState.REJECTED, "corrected_tag")
        with ImageAnalysisRepository(database) as check:
            rejected = check.observations(ids[0])[0][1]
            self.assertEqual(rejected.decision, DecisionState.REJECTED)
            self.assertEqual(rejected.reviewed_name, "corrected_tag")
        page.complete_button.click(); self.app.processEvents()
        self.assertEqual(controller.current.id, ids[1])
        controller.shutdown(); page.close()
        reopened_page = ImageAnalysisPage(LanguageCatalog(LANGUAGES, "en"))
        reopened = ImageAnalysisController(
            self.root, "python", reopened_page, {}, dict, lambda _message: None,
            auto_start_worker=False,
        )
        self.assertEqual(reopened.current.id, ids[1])
        reopened.shutdown(); reopened_page.close()

    def test_failed_item_error_is_visible(self) -> None:
        from booruflow.domain.image_analysis import (
            AnalysisItem,
            AnalysisState,
            InputKind,
            SourceReference,
        )
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        database = self.root / "state.sqlite"
        with ImageAnalysisRepository(database) as repository:
            item_id = repository.add_item(AnalysisItem(
                SourceReference(InputKind.LOCAL_FILE, original_path=self.root / "gone.png"),
                cached_path=self.root / "gone.png", content_sha256="a" * 64,
                mime_type="image/png", width=1, height=1,
            ))
            repository.transition(item_id, AnalysisState.FAILED, "source image is missing")
            rows = repository.list_items()
        page = ImageAnalysisPage(LanguageCatalog(LANGUAGES, "en")); page.show_sources(rows)
        self.assertIn("source image is missing", page.source_table.item(0, 4).text())
        page.close()

    def test_review_queue_filters_double_click_navigation_and_empty_state(self) -> None:
        from booruflow.domain.image_analysis import (
            AnalysisItem,
            AnalysisState,
            InputKind,
            SourceReference,
        )
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        database = self.root / "queue.sqlite"
        with ImageAnalysisRepository(database) as repository:
            ids = {}
            for state in ("pending", "ready", "reviewed", "skipped", "failed"):
                path = self.root / f"{state}.png"
                item_id = repository.add_item(AnalysisItem(
                    SourceReference(InputKind.LOCAL_FILE, original_path=path),
                    cached_path=path, content_sha256="a" * 64,
                    mime_type="image/png", width=1, height=1,
                ))
                ids[state] = item_id
                if state in {"ready", "reviewed"}:
                    repository.transition(item_id, AnalysisState.PROCESSING)
                    repository.transition(item_id, AnalysisState.READY_FOR_REVIEW)
                if state == "reviewed": repository.transition(item_id, AnalysisState.REVIEWED)
                if state == "skipped": repository.transition(item_id, AnalysisState.SKIPPED)
                if state == "failed": repository.transition(item_id, AnalysisState.FAILED, "broken")
            rows = repository.list_items()
        page = ImageAnalysisPage(LanguageCatalog(LANGUAGES, "en")); opened = []
        page.item_open_requested.connect(opened.append); page.show_sources(rows)
        visible = lambda: [int(page.source_table.item(row, 0).text())
                           for row in range(page.source_table.rowCount())]
        self.assertEqual(visible(), [ids["pending"], ids["ready"], ids["failed"]])
        for mode, expected in (
            ("reviewed", [ids["reviewed"]]), ("skipped", [ids["skipped"]]),
            ("failed", [ids["failed"]]), ("all", list(ids.values())),
            ("ready", [ids["ready"]]),
        ):
            page.queue_filter.setCurrentIndex(page.queue_filter.findData(mode))
            self.assertEqual(visible(), expected)
            if expected:
                page.source_table.cellDoubleClicked.emit(0, 0)
                self.assertEqual(opened[-1], expected[0])
        page.current_item_id = ids["ready"]; page._filter_source_rows()
        self.assertEqual(page.source_table.currentRow(), 0)
        navigation = []; page.queue_navigation_requested.connect(navigation.append)
        page.queue_filter.setCurrentIndex(page.queue_filter.findData("all"))
        page.current_item_id = ids["ready"]; page._filter_source_rows(); page.next_item.click()
        self.assertEqual(navigation[-1], ids["reviewed"])
        page.show_sources([row for row in rows if row["state"] == "reviewed"])
        page.queue_filter.setCurrentIndex(page.queue_filter.findData("active"))
        self.assertEqual(page.source_table.rowCount(), 0)
        self.assertIn("Aucun élément", page.queue_empty.text())
        page.close()

    def test_decision_navigation_follows_filtered_sorted_view_and_handles_last_row(self) -> None:
        from dataclasses import replace
        from types import SimpleNamespace

        from booruflow.domain.image_analysis import ObservationSource, TagObservation
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        page = ImageAnalysisPage(LanguageCatalog(LANGUAGES, "en"))
        item = SimpleNamespace(id=1, cached_path=None)
        rows = [
            (1, TagObservation("alpha", ObservationSource.WD14, 0.9)),
            (2, TagObservation("beta", ObservationSource.WD14, 0.8)),
            (3, TagObservation("gamma", ObservationSource.WD14, 0.7)),
        ]
        def decide(ids, decision):
            nonlocal rows
            rows = [(row_id, replace(observation, decision=decision))
                    if row_id in ids else (row_id, observation)
                    for row_id, observation in rows]
            page.show_review(item, (), rows, None)
        page.bulk_observation_decision_requested.connect(decide)
        page.show_review(item, (), rows, None)
        page.observations.selectRow(1); page.accept.click()
        self.assertEqual(page.observation_model.rows[1][0], 3)
        self.assertEqual(page.observations.currentIndex().row(), 1)
        page.observations.selectRow(1); page.reject.click()
        self.assertEqual(page.observation_model.rowCount(), 1)
        self.assertEqual(page.observations.currentIndex().row(), 0)
        page.observations.selectRow(0); page.accept.click()
        self.assertEqual(page.observation_model.rowCount(), 0)
        self.assertFalse(page.observations.currentIndex().isValid())
        page.close()

    def test_space_shortcut_scope_text_protection_disabled_state_and_a_r(self) -> None:
        from types import SimpleNamespace

        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from booruflow.domain.image_analysis import AnalysisState, ObservationSource, TagObservation
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        page = ImageAnalysisPage(LanguageCatalog(LANGUAGES, "fr")); page.show()
        item = SimpleNamespace(id=1, cached_path=None, state=AnalysisState.READY_FOR_REVIEW)
        rows = [(1, TagObservation("first", ObservationSource.WD14, .9))]
        completed = []; decisions = []
        page.complete_requested.connect(lambda: completed.append(True))
        page.bulk_observation_decision_requested.connect(
            lambda ids, decision: decisions.append((ids, decision))
        )
        page.show_review(item, (), rows, None); page.observations.selectRow(0)
        page.observations.setFocus(); self.app.processEvents()
        QTest.keyClick(page.observations.viewport(), Qt.Key.Key_Space)
        self.assertEqual(completed, [True])
        page.manual_tag.setText("two"); page.manual_tag.setFocus()
        QTest.keyClick(page.manual_tag, Qt.Key.Key_Space)
        self.assertEqual(page.manual_tag.text(), "two "); self.assertEqual(len(completed), 1)
        page.tags_filter.setText("blue"); page.tags_filter.setFocus()
        QTest.keyClick(page.tags_filter, Qt.Key.Key_Space)
        self.assertEqual(page.tags_filter.text(), "blue "); self.assertEqual(len(completed), 1)
        page.observations.setFocus(); page.observations.selectRow(0)
        QTest.keyClick(page.observations.viewport(), Qt.Key.Key_A)
        QTest.keyClick(page.observations.viewport(), Qt.Key.Key_R)
        self.assertEqual(len(decisions), 2); self.assertEqual(len(completed), 1)
        page.show_review(None, (), [], None)
        QTest.keyClick(page.observations.viewport(), Qt.Key.Key_Space)
        self.assertEqual(len(completed), 1)
        self.assertIn("[Espace]", page.complete_button.text())
        self.assertIn("Espace", page.complete_button.toolTip())
        page.close()

    def test_space_validates_current_item_and_opens_next(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from booruflow.domain.image_analysis import AnalysisState
        from booruflow.infrastructure.classic_image_analysis import ClassicImageAnalyzer
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.infrastructure.image_sources import ImageSourceService
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_controller import ImageAnalysisController
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        from booruflow.worker.image_analysis import ImageAnalysisWorker
        database = self.root / "var" / "state" / "image_analysis.sqlite"; ids = []
        with ImageAnalysisRepository(database) as repository:
            sources = ImageSourceService(repository, self.root / "cache")
            for index in range(2):
                path = self.root / f"space-{index}.png"
                Image.new("RGB", (8, 8), (index, index, index)).save(path)
                ids.append(sources.add_local(path))
            worker = ImageAnalysisWorker(repository, [ClassicImageAnalyzer()], analysis_prefetch=2)
            worker.process_one(); worker.process_one()
        page = ImageAnalysisPage(LanguageCatalog(LANGUAGES, "en"))
        controller = ImageAnalysisController(
            self.root, "python", page, {}, dict, lambda _message: None,
            auto_start_worker=False,
        )
        try:
            self.assertEqual(controller.current.id, ids[0])
            page.show(); page.observations.setFocus(); self.app.processEvents()
            QTest.keyClick(page.observations.viewport(), Qt.Key.Key_Space)
            self.app.processEvents()
            self.assertEqual(controller.repository.get_item(ids[0]).state, AnalysisState.REVIEWED)
            self.assertEqual(controller.current.id, ids[1])
        finally:
            controller.shutdown(); page.close()

    def test_controller_historical_browsing_preserves_active_review_and_model_runs(self) -> None:
        from booruflow.domain.image_analysis import (
            AnalysisItem,
            AnalysisState,
            InputKind,
            SourceReference,
        )
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_controller import ImageAnalysisController
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        database = self.root / "var" / "state" / "image_analysis.sqlite"
        ids = {}
        with ImageAnalysisRepository(database) as repository:
            for state in ("ready", "reviewed", "skipped", "failed", "pending"):
                path = self.root / f"{state}.png"; Image.new("RGB", (4, 4)).save(path)
                item_id = repository.add_item(AnalysisItem(
                    SourceReference(InputKind.LOCAL_FILE, original_path=path), cached_path=path,
                    content_sha256="a" * 64, mime_type="image/png", width=4, height=4,
                )); ids[state] = item_id
                if state in {"ready", "reviewed"}:
                    repository.transition(item_id, AnalysisState.PROCESSING)
                    repository.transition(item_id, AnalysisState.READY_FOR_REVIEW)
                if state == "reviewed":
                    run_id = repository.begin_model_run(item_id, "onnx", "wd14", "1", "cfg")
                    repository.complete_model_run(run_id)
                    repository.transition(item_id, AnalysisState.REVIEWED)
                elif state == "skipped": repository.transition(item_id, AnalysisState.SKIPPED)
                elif state == "failed": repository.transition(item_id, AnalysisState.FAILED, "broken")
        page = ImageAnalysisPage(LanguageCatalog(LANGUAGES, "en"))
        controller = ImageAnalysisController(
            self.root, "python", page, {}, dict, lambda _message: None,
            auto_start_worker=False,
        )
        try:
            self.assertEqual(controller.current.id, ids["ready"])
            for state in ("reviewed", "skipped", "failed", "pending"):
                controller.open_item(ids[state]); self.assertEqual(controller.current.id, ids[state])
            self.assertEqual(controller.repository.connection.execute(
                "SELECT COUNT(*) FROM model_runs"
            ).fetchone()[0], 1)
            active = controller.repository.connection.execute(
                "SELECT id FROM analysis_items WHERE review_active=1"
            ).fetchone()[0]
            self.assertEqual(active, ids["ready"])
            controller.queue_filter_changed("active")
            self.assertEqual(controller.current.id, ids["ready"])
            controller.clean_queue("reviewed")
            self.assertFalse(controller.repository.item_queue_visible(ids["reviewed"]))
            self.assertIsNotNone(controller.repository.get_item(ids["reviewed"]))
            self.assertEqual(controller.repository.connection.execute(
                "SELECT COUNT(*) FROM model_runs WHERE item_id=?", (ids["reviewed"],)
            ).fetchone()[0], 1)
        finally:
            controller.shutdown(); page.close()

    def test_multiple_decision_selects_first_row_after_treated_zone(self) -> None:
        from dataclasses import replace
        from types import SimpleNamespace

        from PySide6.QtCore import Qt

        from booruflow.domain.image_analysis import ObservationSource, TagObservation
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        page = ImageAnalysisPage(LanguageCatalog(LANGUAGES, "en"))
        item = SimpleNamespace(id=1, cached_path=None)
        rows = [(index, TagObservation(name, ObservationSource.WD14, confidence))
                for index, (name, confidence) in enumerate(
                    (("delta", .9), ("charlie", .8), ("bravo", .7), ("alpha", .6)), 1
                )]
        def decide(ids, decision):
            nonlocal rows
            rows = [(row_id, replace(observation, decision=decision))
                    if row_id in ids else (row_id, observation)
                    for row_id, observation in rows]
            page.show_review(item, (), rows, None)
        page.bulk_observation_decision_requested.connect(decide)
        page.show_review(item, (), rows, None)
        page.observations.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        selection = page.observations.selectionModel()
        page.observations.selectRow(1)
        selection.select(page.observation_model.index(2, 0), selection.SelectionFlag.Select
                         | selection.SelectionFlag.Rows)
        page.accept.click()
        self.assertEqual([row[0] for row in page.observation_model.rows], [4, 1])
        self.assertEqual(page.observations.currentIndex().row(), 1)
        page.close()

    def test_ten_image_prefetch_scenario_runs_end_to_end(self) -> None:
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_controller import ImageAnalysisController
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        paths = []
        for index in range(10):
            path = self.root / f"batch-{index}.png"
            Image.new("RGB", (16, 12), (200 + index, 210, 220)).save(path)
            paths.append(str(path))
        page = ImageAnalysisPage(LanguageCatalog(LANGUAGES, "en"))
        controller = ImageAnalysisController(
            self.root, sys.executable, page,
            {"image_analysis_analysis_prefetch": 2}, dict, lambda _message: None,
        )
        controller.add_local_files(paths)
        deadline = time.monotonic() + 10
        counts = {}
        while time.monotonic() < deadline:
            self.app.processEvents(); time.sleep(0.03)
            counts = controller.repository.queue_counts()
            if controller.current is not None and counts.get("ready_ahead") == 2:
                break
        self.assertIsNotNone(controller.current)
        self.assertEqual(counts.get("ready_ahead"), 2)
        first_id = controller.current.id
        controller.complete()
        self.assertIsNotNone(controller.current)
        self.assertNotEqual(controller.current.id, first_id)
        controller.add_manual_tag("persisted_after_prefetch")
        current_id = controller.current.id
        controller.shutdown(); page.close()
        with ImageAnalysisRepository(self.root / "var" / "state" / "image_analysis.sqlite") as check:
            self.assertEqual(check.observations(current_id)[0][1].name, "persisted_after_prefetch")

    def test_hidden_page_defers_widget_rebuild_and_slow_logs_are_throttled(self)->None:
        from unittest.mock import MagicMock

        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_controller import ImageAnalysisController
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        page=ImageAnalysisPage(LanguageCatalog(LANGUAGES,"en"));logs=[];controller=ImageAnalysisController(self.root,"python",page,{},dict,logs.append,auto_start_worker=False);page.show_sources=MagicMock();controller.set_page_active(False);controller.refresh();self.assertFalse(page.show_sources.called);self.assertTrue(controller._page_dirty);controller.set_page_active(True);self.app.processEvents();self.assertTrue(page.show_sources.called);logs.clear();controller._log_slow_refresh(250,"model=240ms");controller._log_slow_refresh(260,"model=250ms");controller._log_slow_refresh(270,"model=260ms");self.assertEqual(len(logs),1);controller.shutdown();page.close()


if __name__ == "__main__":
    unittest.main()
