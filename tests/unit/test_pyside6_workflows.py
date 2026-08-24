import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        page.show_results(
            [
                {"id": 10, "tag_count": 4, "priority": "critical"},
                {"id": 20, "tag_count": 7, "priority": "high"},
            ]
        )
        self.assertEqual(page.results_layout.count(), 3)
        critical = page.results_layout.itemAt(0).widget()
        self.assertEqual(critical.grid.count(), 1)
        self.assertIn("#10", critical.grid.itemAt(0).widget().text())
        self.assertFalse(critical.content.isHidden())
        critical.toggle.setChecked(False)
        self.assertTrue(critical.content.isHidden())
        page.close()

    def test_tagging_local_review_selection_shortcuts_and_copy_lists(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.show()
        QApplication.processEvents()
        page.show_local_review(
            "Analyse disponible", None, ["solo"],
            [{"id": 17, "tag": "blue_hair", "confidence": "0.900",
              "decision": "unreviewed", "match": "exact"}],
            ["blue_hair"], ["blue_hair", "unknown_tag"],
        )
        captured = []
        page.decision_requested.connect(lambda oid, state: captured.append((oid, state)))
        page.suggestions.selectRow(0); page.suggestions.setFocus()
        QTest.keyClick(page.suggestions, Qt.Key.Key_A)
        self.assertEqual(captured, [(17, "accepted")])
        QTest.keyClick(page.suggestions, Qt.Key.Key_R)
        self.assertEqual(captured[-1], (17, "rejected"))
        page.copy_button.click()
        self.assertEqual(QApplication.clipboard().text(), " blue_hair")
        page.copy_all_button.click()
        self.assertEqual(QApplication.clipboard().text(), " blue_hair unknown_tag")
        self.assertEqual(page.zoom.itemData(3), 400)
        page.close()

    def test_tagging_selects_first_then_advances_in_visible_order(self) -> None:
        from PySide6.QtWidgets import QApplication

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.show(); QApplication.processEvents()
        rows = [
            {"id": oid, "tag": tag, "confidence": confidence,
             "decision": "unreviewed", "match": "exact"}
            for oid, tag, confidence in ((30, "c", "0.3"), (10, "a", "0.9"), (20, "b", "0.6"))
        ]
        page.show_local_review("Analyse disponible", None, [], rows, [], [])
        self.assertEqual(page._selected_observation_id(), 10)
        page._emit_decision("accepted")
        page.show_local_review("Analyse disponible", None, [], [rows[0], rows[2]], [], [])
        self.assertEqual(page._selected_observation_id(), 20)
        page._emit_decision("rejected")
        page.show_local_review("Analyse disponible", None, [], [rows[0]], [], [])
        self.assertEqual(page._selected_observation_id(), 30)
        page._emit_decision("accepted")
        page.show_local_review("Analyse disponible", None, [], [], [], [])
        self.assertIsNone(page._selected_observation_id())
        page.close()

    def test_tagging_suggestion_sorting_and_decision_filters(self) -> None:
        from PySide6.QtCore import Qt

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        rows = [
            {"id": 1, "tag": "zeta", "confidence": "0.100", "decision": "accepted", "match": "mapping → z"},
            {"id": 2, "tag": "Alpha", "confidence": "0.950", "decision": "unreviewed", "match": "introuvable localement"},
            {"id": 3, "tag": "beta", "confidence": "0.800", "decision": "rejected", "match": "déjà présent"},
            {"id": 4, "tag": "gamma", "confidence": "0.700", "decision": "unreviewed", "match": "exact"},
        ]
        page.show_local_review("Analyse disponible", None, [], rows, [], [])
        self.assertEqual(page.decision_filter.currentData(), "unreviewed")
        self.assertEqual([page.suggestions.item(r, 4).text() for r in range(2)], ["2", "4"])

        page.decision_filter.setCurrentIndex(0)
        cases = (
            (0, ["Alpha", "beta", "gamma", "zeta"]),
            (1, ["0.100", "0.700", "0.800", "0.950"]),
            (2, ["unreviewed", "unreviewed", "accepted", "rejected"]),
            (3, ["exact", "mapping → z", "déjà présent", "introuvable localement"]),
        )
        for column, expected in cases:
            page.suggestions.sortItems(column, Qt.SortOrder.AscendingOrder)
            self.assertEqual(
                [page.suggestions.item(r, column).text() for r in range(4)], expected,
            )
        for index, expected_count in ((1, 2), (2, 1), (3, 1)):
            page.decision_filter.setCurrentIndex(index)
            self.assertEqual(page.suggestions.rowCount(), expected_count)
        page.close()

    def test_tagging_existing_pending_and_failed_are_not_automatically_requeued(self) -> None:
        from types import SimpleNamespace

        from booruflow.domain.image_analysis import AnalysisState
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        controller = TaggingController(self.catalog(), page, dict, lambda *_args, **_kwargs: None)
        items = {
            "8": SimpleNamespace(id=8, state=AnalysisState.PENDING, cached_path=None, last_error=None),
            "9": SimpleNamespace(id=9, state=AnalysisState.FAILED, cached_path=None, last_error="boom"),
        }
        added = []
        repository = SimpleNamespace(
            item_by_remote_source=lambda _site, post_id: items.get(post_id),
            source_tags=lambda _item_id: (), observations=lambda _item_id: [],
        )
        controller.image_analysis = SimpleNamespace(
            repository=repository, add_remote_ids=lambda *_args, **_kwargs: added.append(True),
            settings={},
        )
        page._select_post({"id": 8, "tags": "solo"})
        self.assertIn("attente", page.analysis_state.text())
        self.assertFalse(page.analyze_button.isEnabled())
        page._select_post({"id": 9, "tags": "solo"})
        self.assertIn("boom", page.analysis_state.text())
        self.assertEqual(page.analyze_button.text(), "Réessayer")
        self.assertTrue(page.analyze_button.isEnabled())
        self.assertEqual(added, [])
        page.close()

    def test_tagging_pool_selected_skipped_reopens_without_model_run(self) -> None:
        from types import SimpleNamespace
        from booruflow.domain.image_analysis import AnalysisItem, AnalysisState, InputKind, SourceReference
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage
        with tempfile.TemporaryDirectory() as temporary:
            repository=ImageAnalysisRepository(Path(temporary)/"state.sqlite");item_id=repository.add_item(AnalysisItem(SourceReference(InputKind.LOCAL_FILE,original_path=Path("x.png")),cached_path=Path("x.png"),content_sha256="f"*64,mime_type="image/png",width=1,height=1));repository.transition(item_id,AnalysisState.PROCESSING);repository.transition(item_id,AnalysisState.READY_FOR_REVIEW);repository.finish_review(item_id,AnalysisState.SKIPPED);repository.add_to_tagging_pool([item_id],"test")
            page=TaggingPage(self.catalog(), {});controller=TaggingController(self.catalog(),page,dict,lambda *_args,**_kwargs:None);controller.image_analysis=SimpleNamespace(repository=repository);controller.refresh_pool();page.pool_table.selectRow(0);page.pool_reopen.click();self.app.processEvents()
            self.assertEqual(repository.get_item(item_id).state,AnalysisState.READY_FOR_REVIEW);self.assertEqual(repository.connection.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0],0);page.close();repository.close()

    def test_tagging_space_opens_and_empty_copy_does_not_replace_clipboard(self) -> None:
        from PySide6.QtCore import QCoreApplication, QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.current_post_id = 42
        page.show(); QApplication.processEvents()
        page.show_local_review("Analyse disponible", None, [], [], [], [])
        QApplication.clipboard().setText("unchanged")
        with patch("booruflow.presentation.pyside6.tagging_page.QDesktopServices.openUrl") as opened:
            page.suggestions.setFocus()
            QTest.keyClick(page.suggestions, Qt.Key.Key_Space)
            self.assertEqual(opened.call_count, 1)
            repeated = QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier,
                " ", True, 2,
            )
            QCoreApplication.sendEvent(page.suggestions, repeated)
            self.assertEqual(opened.call_count, 1)
        self.assertEqual(QApplication.clipboard().text(), "unchanged")
        page.query.setFocus(); page.query.clear()
        with patch("booruflow.presentation.pyside6.tagging_page.QDesktopServices.openUrl") as opened:
            QTest.keyClick(page.query, Qt.Key.Key_Space)
            self.assertEqual(page.query.text(), " ")
            opened.assert_not_called()
        page.close()

    def test_second_tagging_result_enters_pending_then_ready_review(self) -> None:
        from types import SimpleNamespace

        from booruflow.domain.image_analysis import AnalysisState
        from booruflow.presentation.pyside6.tagging_controller import TaggingController
        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        logs = []
        controller = TaggingController(self.catalog(), page, dict, logs.append)
        items = {
            "1": SimpleNamespace(
                id=1, state=AnalysisState.REVIEWED, cached_path=None, last_error=None,
            )
        }
        repository = SimpleNamespace(
            item_by_remote_source=lambda _site, post_id: items.get(post_id),
            item_queue_visible=lambda _item_id: False,
            source_tags=lambda _item_id: (), observations=lambda _item_id: [],
        )
        def add_remote(_site, post_ids, **_kwargs):
            items[post_ids[0]] = SimpleNamespace(
                id=2, state=AnalysisState.PENDING, cached_path=None, last_error=None,
            )
            return [2]
        controller.image_analysis = SimpleNamespace(
            repository=repository, add_remote_ids=add_remote, settings={},
        )
        page._select_post({"id": 1, "tags": "solo"})
        self.assertIn("cache réutilisé", page.analysis_state.text())
        with patch("booruflow.presentation.pyside6.tagging_page.QDesktopServices.openUrl"):
            page._copy_and_open()
        page._select_post({"id": 2, "tags": "solo"})
        self.assertIn("Analyse en attente", page.analysis_state.text())
        items["2"].state = AnalysisState.READY_FOR_REVIEW
        controller._poll_current()
        self.assertEqual(page.analysis_state.text(), "Analyse disponible")
        joined = "\n".join(logs)
        self.assertIn("Local analysis requested", joined)
        self.assertIn("No existing item found", joined)
        self.assertIn("ready_for_review", joined)
        page.close()

    def test_tagging_search_and_review_are_mutually_exclusive_and_restore_scroll(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        page.resize(1280, 720); page.show(); QApplication.processEvents()
        posts = [
            {"id": value, "tag_count": 4, "priority": "critical", "tags": "solo"}
            for value in range(1, 14)
        ]
        page.show_results(posts); QApplication.processEvents()
        self.assertIs(page.mode_stack.currentWidget(), page.search_view)
        self.assertTrue(page.search_view.isVisible()); self.assertFalse(page.review.isVisible())
        page.results_scroll.verticalScrollBar().setValue(80)
        page._open_result(3); QApplication.processEvents()
        self.assertIs(page.mode_stack.currentWidget(), page.review)
        self.assertFalse(page.search_view.isVisible()); self.assertTrue(page.review.isVisible())
        self.assertIn("Post 4 / 13", page.result_counter.text())
        page.next_button.click(); self.assertEqual(page.current_post_id, 5)
        page.previous_button.click(); self.assertEqual(page.current_post_id, 4)
        page.suggestions.setFocus(); QTest.keyClick(page.suggestions, Qt.Key.Key_Escape)
        self.assertIs(page.mode_stack.currentWidget(), page.search_view)
        page._open_result(3)
        page.show_search(); QApplication.processEvents()
        self.assertEqual(page.results_scroll.verticalScrollBar().value(), page._search_scroll_value)
        self.assertEqual(len(page.result_posts), 13)
        page.close()

    def test_tagging_review_navigation_and_space_advance_then_finish(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {})
        posts = [
            {"id": value, "tag_count": 4, "priority": "critical", "tags": "solo"}
            for value in (10, 20)
        ]
        page.show_results(posts); page._open_result(0); page.show_local_review(
            "Analyse disponible", None, [], [], ["chair"], ["chair"]
        )
        with patch("booruflow.presentation.pyside6.tagging_page.QDesktopServices.openUrl") as opened:
            page.suggestions.setFocus(); QTest.keyClick(page.suggestions, Qt.Key.Key_Space)
            self.assertEqual(opened.call_count, 1)
            self.assertEqual(page.current_post_id, 20)
            self.assertIs(page.mode_stack.currentWidget(), page.review)
            page.show_local_review("Analyse disponible", None, [], [], ["indoors"], ["indoors"])
            QTest.keyClick(page.suggestions, Qt.Key.Key_Space)
            self.assertEqual(opened.call_count, 2)
        self.assertIs(page.mode_stack.currentWidget(), page.search_view)
        self.assertIn("Tous les résultats", page.state.text())
        self.assertEqual(page.processed_in_session, {10, 20})
        page.close()

    def test_tagging_review_action_bar_stays_inside_1280_by_720(self) -> None:
        from PySide6.QtWidgets import QApplication

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {}); page.show()
        page._select_post({"id": 42, "tags": "solo blue_hair"})
        page.show_local_review("Analyse disponible", None, ["solo"], [], [], [])
        for width, height in ((1280, 720), (1600, 900), (1920, 1080)):
            page.resize(width, height); QApplication.processEvents()
            bottom = page.action_bar.mapTo(page, page.action_bar.rect().bottomLeft()).y()
            self.assertTrue(page.action_bar.isVisible())
            self.assertGreater(page.action_bar.height(), 0)
            self.assertLessEqual(bottom, page.rect().bottom())
            self.assertEqual(page.mode_stack.currentWidget(), page.review)
            for button in (
                page.analyze_button, page.accept_button, page.reject_button,
                page.copy_open_button, page.open_button,
            ):
                self.assertGreater(button.width(), 0, button.text())
                self.assertGreater(button.height(), 0, button.text())
        page.close()

    def test_tagging_non_analyzed_action_becomes_ready_with_first_suggestion_selected(self) -> None:
        from PySide6.QtWidgets import QApplication

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        page = TaggingPage(self.catalog(), {}); page.show(); QApplication.processEvents()
        requested = []; page.analyze_requested.connect(requested.append)
        page._select_post({"id": 147, "tags": "solo"})
        page.show_local_review("Non analysée", None, ["solo"], [], [], [])
        self.assertTrue(page.analyze_button.isVisible()); self.assertTrue(page.analyze_button.isEnabled())
        self.assertFalse(page.accept_button.isEnabled()); self.assertFalse(page.reject_button.isEnabled())
        page.analyze_button.click(); self.assertEqual(requested, [147])
        page.set_analysis_request_state("Analyse en attente…", True)
        self.assertFalse(page.analyze_button.isEnabled())
        page.show_local_review(
            "Analyse disponible", None, ["solo"],
            [{"id": 9, "tag": "chair", "confidence": "0.9",
              "decision": "unreviewed", "match": "exact"}],
            ["chair"], ["chair"],
        )
        self.assertEqual(page._selected_observation_id(), 9)
        self.assertTrue(page.accept_button.isEnabled()); self.assertTrue(page.reject_button.isEnabled())
        self.assertTrue(page.map_button.isEnabled()); self.assertTrue(page.copy_button.isEnabled())
        self.assertTrue(page.copy_open_button.isEnabled()); self.assertTrue(page.open_button.isEnabled())
        page.close()

    def test_tag_browser_sorts_post_counts_as_numbers(self) -> None:
        from PySide6.QtCore import Qt

        from booruflow.infrastructure.tag_browser import TagRow
        from booruflow.presentation.pyside6.tag_browser_page import TagBrowserPage

        page = TagBrowserPage(self.catalog())
        page._show_rows(
            [
                TagRow(1, "small", 91, 0, 0),
                TagRow(2, "large", 7359, 0, 0),
                TagRow(3, "middle", 842, 0, 0),
            ]
        )
        page.table.sortItems(2, Qt.SortOrder.DescendingOrder)
        self.assertEqual(
            [page.table.item(row, 2).data(Qt.ItemDataRole.DisplayRole) for row in range(3)],
            [7359, 842, 91],
        )
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
        from booruflow.presentation.pyside6.organization_page import ROLE_TAG, OrganizationPage

        document = {"boards": {"gelbooru": {"Characters": {"battlecruiser": {}}}}}
        page = OrganizationPage(self.catalog(), document)
        branch = page.tree.topLevelItem(0)
        page.tree.expandItem(branch)
        self.app.processEvents()
        leaf = branch.child(0)
        self.assertEqual(leaf.text(0), "battlecruiser *")
        self.assertEqual(leaf.data(0, ROLE_TAG), "battlecruiser")
        page.close()

    def test_search_result_opens_a_tag_stored_in_legacy_tags_list(self) -> None:
        from booruflow.presentation.pyside6.organization_page import OrganizationPage

        document = {"boards": {"gelbooru": {"Animals": {"__tags__": ["cat"]}}}}
        page = OrganizationPage(self.catalog(), document)
        page.search.setText("cat")
        page._search()
        self.assertEqual(page.results.count(), 1)
        page._open_result(page.results.item(0))
        self.assertEqual(page.details_title.text(), "cat")
        page.close()

    def test_tag_list_adds_real_children_to_selected_node(self) -> None:
        from booruflow.presentation.pyside6.organization_page import OrganizationPage

        document = {"boards": {"gelbooru": {"Medical": {"injury": {}}}}}
        page = OrganizationPage(self.catalog(), document)
        medical = page.tree.topLevelItem(0)
        page.tree.expandItem(medical)
        self.app.processEvents()
        injury = medical.child(0)
        with patch(
            "booruflow.presentation.pyside6.organization_page.QInputDialog.getMultiLineText",
            return_value=("wound\nbruise, scar", True),
        ):
            page._add_tags_to_item(injury)
        node = document["boards"]["gelbooru"]["Medical"]["injury"]
        self.assertEqual(node["__tag__"], "injury")
        self.assertEqual(set(node) - {"__tag__"}, {"wound", "bruise", "scar"})
        self.assertEqual(node["wound"]["__tag__"], "wound")
        page.close()

    def test_reload_preserves_expanded_branches_and_parent_selection_after_delete(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from booruflow.presentation.pyside6.organization_page import OrganizationPage

        document = {"boards": {"gelbooru": {"A": {"B": {"leaf": {}}}}}}
        page = OrganizationPage(self.catalog(), document)
        branch_a = page.tree.topLevelItem(0)
        page._populate(branch_a)
        branch_a.setExpanded(True)
        branch_b = branch_a.child(0)
        page._populate(branch_b)
        branch_b.setExpanded(True)
        page.tree.setCurrentItem(branch_b.child(0))
        with patch(
            "booruflow.presentation.pyside6.organization_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            page._delete()
        restored_a = page.tree.topLevelItem(0)
        restored_b = restored_a.child(0)
        self.assertTrue(restored_a.isExpanded())
        self.assertTrue(restored_b.isExpanded())
        self.assertEqual(page.tree.currentItem().text(0).removesuffix(" *"), "B")
        page.close()

    def test_rename_preserves_expansion_under_the_renamed_branch(self) -> None:
        from booruflow.presentation.pyside6.organization_page import OrganizationPage

        document = {"boards": {"gelbooru": {"A": {"B": {"C": {"leaf": {}}}}}}}
        page = OrganizationPage(self.catalog(), document)
        branch_a = page.tree.topLevelItem(0)
        page._populate(branch_a)
        branch_a.setExpanded(True)
        branch_b = branch_a.child(0)
        page._populate(branch_b)
        branch_b.setExpanded(True)
        page.tree.setCurrentItem(branch_b)
        with patch(
            "booruflow.presentation.pyside6.organization_page.QInputDialog.getText",
            return_value=("Renamed", True),
        ):
            page._rename()
        restored_a = page.tree.topLevelItem(0)
        restored = restored_a.child(0)
        self.assertEqual(restored.text(0), "Renamed")
        self.assertTrue(restored_a.isExpanded())
        self.assertTrue(restored.isExpanded())
        page.close()

    def test_numeric_wiki_id_is_attached_to_any_node_and_used_for_details(self) -> None:
        from booruflow.presentation.pyside6.organization_page import OrganizationPage

        document = {
            "boards": {
                "gelbooru": {"Characters": {"List_of_Azur_Lane_characters": {"Azur_Lane": {}}}}
            }
        }
        page = OrganizationPage(self.catalog(), document)
        characters = page.tree.topLevelItem(0)
        page._populate(characters)
        node = characters.child(0)
        with patch(
            "booruflow.presentation.pyside6.organization_page.QInputDialog.getText",
            return_value=("26107", True),
        ):
            page._set_wiki(node)
        expected = "https://gelbooru.com/index.php?page=wiki&s=view&id=26107"
        self.assertEqual(
            document["metadata"]["gelbooru"]["List_of_Azur_Lane_characters"]["wiki_url"], expected
        )
        captured = []
        page.tag_details_requested.connect(lambda *values: captured.append(values))
        restored = page._select_path(("Characters", "List_of_Azur_Lane_characters"))
        page._inspect_item(restored)
        self.assertEqual(captured[-1], ("gelbooru", "List_of_Azur_Lane_characters", expected))
        page.close()

    def test_recurring_tags_can_be_checked_for_review_and_opened(self) -> None:
        from PySide6.QtCore import Qt, QUrl

        from booruflow.presentation.pyside6.organization_page import OrganizationPage

        document = {"boards": {"gelbooru": {"Medical": {"injury": {}, "wound": {}}}}}
        page = OrganizationPage(self.catalog(), document)
        page.details_title.setText("injury")
        page.show_tag_details(
            {
                "tag": "injury",
                "definition": "Related injuries",
                "online": True,
                "sample_size": 100,
                "recurring": [{"tag": "wound", "count": 42}],
                "wiki_tags": ["wound"],
                "samples": [],
            }
        )
        captured = []
        page.review_tags_requested.connect(captured.append)
        page.recurring.item(0).setCheckState(Qt.CheckState.Checked)
        page._send_recurring_to_review()
        self.assertEqual(captured, [("wound",)])
        page._open_recurring(page.recurring.item(0))
        self.assertEqual(page.details_title.text(), "wound")
        page.details_title.setText("injury")
        page._definition_link_clicked(QUrl("booruflow-tag:wound"))
        self.assertEqual(page.details_title.text(), "wound")
        page.close()

    def test_review_engine_summary_is_captured_for_visible_status(self) -> None:
        from booruflow.application.capabilities import ApplicationCapabilities
        from booruflow.domain import ToolAvailability
        from booruflow.presentation.pyside6.main_window import MainWindow

        window = MainWindow(ApplicationCapabilities(ToolAvailability(False)), self.catalog())
        window.review_coordinator.output("768 artistes e621 retenus.\n")
        self.assertEqual(window.review_coordinator.output_state.retained, 768)
        self.assertIn("768", window.review_coordinator.output_state.summary[0])
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
            (target / "artistes_candidats_uniques.txt").write_text(
                "artist_one\nartist_two\n", encoding="utf-8"
            )
            request = ReviewRequest(
                ("rating:safe",),
                ("e621",),
                "artists",
                10,
                1,
                0,
                0,
                1,
                False,
                True,
                root / "gel.db",
                root / "e621.db",
                root,
                None,
            )
            window = MainWindow(ApplicationCapabilities(ToolAvailability(False)), self.catalog())
            self.assertEqual(
                window.review_coordinator.result_entries(request),
                [("e621", "artist_one"), ("e621", "artist_two")],
            )
            window.close()
