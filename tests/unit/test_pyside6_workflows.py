import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None
LANGUAGES = Path(__file__).resolve().parents[2] / "resources" / "i18n"


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is not installed")
class PySide6WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def catalog():
        from booruflow.infrastructure.localization import LanguageCatalog

        return LanguageCatalog(LANGUAGES, "en")

    def test_tagging_results_are_grouped_into_collapsible_grids(self) -> None:
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.show_results([
            {"id": 10, "tag_count": 4, "priority": "critical"},
            {"id": 20, "tag_count": 7, "priority": "high"},
        ])
        self.assertEqual(page.results_layout.count(), 3)
        critical = page.results_layout.itemAt(0).widget()
        self.assertEqual(critical.grid.count(), 1)
        self.assertIn("#10", critical.grid.itemAt(0).widget().text())
        self.assertFalse(critical.content.isHidden())
        critical.toggle.setChecked(False)
        self.assertTrue(critical.content.isHidden())
        page.close()

    def test_checked_taxonomy_branch_is_sent_to_review(self) -> None:
        from PySide6.QtCore import Qt
        from booruflow.presentation.pyside6.organization_page import OrganizationPage

        document = {"boards": {"gelbooru": {"Animals": {"cat": {}, "dog": {}}}}}
        page = OrganizationPage(self.catalog(), document)
        captured = []
        page.review_tags_requested.connect(captured.append)
        page.tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
        page._send_to_review()
        self.assertEqual(set(captured[0]), {"cat", "dog"})
        page.close()

    def test_legacy_empty_leaf_is_marked_but_keeps_its_real_tag(self) -> None:
        from booruflow.presentation.pyside6.organization_page import OrganizationPage, ROLE_TAG

        document = {"boards": {"gelbooru": {"Characters": {"battlecruiser": {}}}}}
        page = OrganizationPage(self.catalog(), document)
        branch = page.tree.topLevelItem(0)
        page.tree.expandItem(branch)
        self.app.processEvents()
        leaf = branch.child(0)
        self.assertEqual(leaf.text(0), "battlecruiser *")
        self.assertEqual(leaf.data(0, ROLE_TAG), "battlecruiser")
        page.close()

    def test_review_engine_summary_is_captured_for_visible_status(self) -> None:
        from booruflow.application.capabilities import ApplicationCapabilities
        from booruflow.domain import ToolAvailability
        from booruflow.presentation.pyside6.main_window import MainWindow

        window = MainWindow(ApplicationCapabilities(ToolAvailability(False)), self.catalog())
        window._review_output("768 artistes e621 retenus.\n")
        self.assertEqual(window.review_retained, 768)
        self.assertIn("768", window.review_summary[0])
        window.close()

    def test_e621_candidate_file_is_exposed_as_review_results(self) -> None:
        from booruflow.application.capabilities import ApplicationCapabilities
        from booruflow.application.review import ReviewRequest
        from booruflow.domain import ToolAvailability
        from booruflow.presentation.pyside6.main_window import MainWindow

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "artists" / "e621"
            target.mkdir(parents=True)
            (target / "artistes_candidats_uniques.txt").write_text("artist_one\nartist_two\n", encoding="utf-8")
            request = ReviewRequest(
                ("rating:safe",), ("e621",), "artists", 10, 1, 0, 0, 1,
                False, True, root / "gel.db", root / "e621.db", root, None,
            )
            window = MainWindow(ApplicationCapabilities(ToolAvailability(False)), self.catalog())
            self.assertEqual(
                window._review_result_entries(request),
                [("e621", "artist_one"), ("e621", "artist_two")],
            )
            window.close()
