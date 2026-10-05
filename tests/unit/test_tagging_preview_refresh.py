import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None
LANGUAGES = Path(__file__).resolve().parents[2] / "resources" / "i18n"


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is not installed")
class TaggingPreviewRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.image_analysis_controller import (
            ImageAnalysisController,
        )
        from booruflow.presentation.pyside6.image_analysis_page import ImageAnalysisPage
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.catalog = LanguageCatalog(LANGUAGES)
        self.analysis_page = ImageAnalysisPage(self.catalog)
        self.logs = []
        self.analysis = ImageAnalysisController(
            self.root,
            sys.executable,
            self.analysis_page,
            {},
            dict,
            self.logs.append,
            auto_start_worker=False,
        )
        # The tests resolve sources deterministically below; do not let the
        # normal selection path launch a real metadata worker or ML process.
        self.analysis._start_source_preparation = lambda: None
        self.analysis.ensure_worker_available = lambda _reason: None
        self.page = TaggingPage(
            self.catalog, {"tagging_hide_queued_results": False}
        )
        self.controller = TaggingController(
            self.catalog, self.page, dict, self.logs.append
        )
        self.controller.bind_image_analysis(self.analysis)

    def tearDown(self) -> None:
        self.controller.shutdown()
        self.analysis.shutdown()
        self.page.close()
        self.analysis_page.close()
        self.temporary.cleanup()

    @staticmethod
    def _png(color: str) -> bytes:
        output = io.BytesIO()
        Image.new("RGB", (4, 3), color).save(output, format="PNG")
        return output.getvalue()

    def _add_unresolved(self, post_id: int) -> int:
        from booruflow.domain.image_analysis import InputKind

        return self.analysis.repository.add_unresolved_remote(
            InputKind.GELBOORU_POST, str(post_id), priority=100
        )

    def _resolve(self, item_id: int, post_id: int, color: str) -> None:
        from booruflow.infrastructure.image_sources import GelbooruPostProvider

        data = self._png(color)
        self.analysis.sources.bytes_fetcher = lambda _url, _headers: data
        self.analysis.sources.resolve_post_data(
            item_id,
            GelbooruPostProvider(json_fetcher=lambda *_args: []),
            {
                "id": post_id,
                "file_url": f"https://images.example/{post_id}.png",
                "tags": "solo",
            },
            str(post_id),
        )

    def _finish_source_preparation(self) -> None:
        self.analysis._sources_finished([], [], {}, [], [])
        self.app.processEvents()

    def _show_posts(self, *post_ids: int) -> None:
        posts = [
            {"id": post_id, "tags": "solo", "tag_count": 1, "priority": "low"}
            for post_id in post_ids
        ]
        self.page.show_results(posts)

    def _open(self, post_id: int) -> None:
        index = next(
            index
            for index, post in enumerate(self.page.result_posts)
            if int(post["id"]) == post_id
        )
        self.page._open_result(index)
        self.app.processEvents()

    def _preview_color(self) -> str | None:
        image = self.page.preview._source.toImage()
        return None if image.isNull() else image.pixelColor(0, 0).name()

    def test_new_source_appears_while_analysis_state_remains_pending(self) -> None:
        from booruflow.domain.image_analysis import AnalysisState

        item_id = self._add_unresolved(101)
        self._show_posts(101)
        self._open(101)
        self.assertIsNone(self._preview_color())
        self.assertEqual(
            self.analysis.repository.connection.execute(
                "SELECT source_state FROM analysis_items WHERE id=?", (item_id,)
            ).fetchone()[0],
            "unresolved",
        )

        self._resolve(item_id, 101, "red")
        self.assertEqual(
            self.analysis.repository.get_item(item_id).state, AnalysisState.PENDING
        )
        self._finish_source_preparation()

        self.assertEqual(self._preview_color(), "#ff0000")
        self.assertEqual(self.controller.current_post_id, 101)

    def test_already_analyzed_source_displays_immediately(self) -> None:
        from booruflow.domain.image_analysis import AnalysisState

        item_id = self._add_unresolved(202)
        self._resolve(item_id, 202, "blue")
        self.analysis.repository.transition(item_id, AnalysisState.PROCESSING)
        self.analysis.repository.transition(item_id, AnalysisState.READY_FOR_REVIEW)

        self._show_posts(202)
        self._open(202)

        self.assertEqual(self._preview_color(), "#0000ff")

    def test_late_source_callback_for_a_never_replaces_selected_b(self) -> None:
        item_a = self._add_unresolved(301)
        item_b = self._add_unresolved(302)
        self._show_posts(301, 302)
        self._open(301)
        self._open(302)

        self._resolve(item_a, 301, "red")
        self._finish_source_preparation()

        self.assertEqual(self.controller.current_post_id, 302)
        self.assertEqual(self.page._displayed_post_id, 302)
        self.assertIsNone(self._preview_color())

        self._resolve(item_b, 302, "blue")
        self._finish_source_preparation()

        self.assertEqual(self.controller.current_post_id, 302)
        self.assertEqual(self._preview_color(), "#0000ff")


if __name__ == "__main__":
    unittest.main()
