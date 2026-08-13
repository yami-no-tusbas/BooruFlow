import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None
LANGUAGES = Path(__file__).resolve().parents[2] / "resources" / "i18n"


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is not installed")
class PySide6ShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def window(available: bool = False):
        from booruflow.application.capabilities import ApplicationCapabilities
        from booruflow.domain import ToolAvailability
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.main_window import MainWindow

        return MainWindow(
            ApplicationCapabilities(
                ToolAvailability(available, "not configured" if not available else "")
            ),
            LanguageCatalog(LANGUAGES),
        )

    def test_main_window_exposes_top_level_navigation(self) -> None:
        from booruflow.presentation.pyside6.cleanup_controller import CleanupController
        from booruflow.presentation.pyside6.database_update_controller import (
            DatabaseUpdateController,
        )
        from booruflow.presentation.pyside6.grabber_controller import GrabberController
        from booruflow.presentation.pyside6.organization_controller import (
            OrganizationCoordinator,
        )
        from booruflow.presentation.pyside6.review_controller import ReviewCoordinator
        from booruflow.presentation.pyside6.tagging_controller import TaggingController

        window = self.window()
        self.assertEqual(window.navigation.count(), 10)
        self.assertEqual(window.pages.count(), 10)
        self.assertEqual(window.navigation.item(4).data(256), "tag_browser")
        self.assertEqual(window.navigation.item(5).data(256), "wiki")
        self.assertEqual(window.navigation.item(9).data(256), "tasks")
        self.assertEqual(window.navigation.currentRow(), 0)
        self.assertIsInstance(window.database_controller, DatabaseUpdateController)
        self.assertIsInstance(window.grabber_controller, GrabberController)
        self.assertIsInstance(window.cleanup_controller, CleanupController)
        self.assertIsInstance(window.tagging_controller, TaggingController)
        self.assertIsInstance(window.review_coordinator, ReviewCoordinator)
        self.assertIsInstance(window.organization_coordinator, OrganizationCoordinator)
        self.assertIs(window.review_controller, window.review_coordinator.process_controller)
        self.assertIs(window.database_process, window.database_controller.process)
        self.assertIs(window.grabber_process, window.grabber_controller.process)
        window.close()

    def test_log_can_be_toggled_and_cleared(self) -> None:
        window = self.window(True)
        window.show()
        self.app.processEvents()
        self.assertFalse(window.log_view.isVisible())
        window.toggle_log()
        self.app.processEvents()
        self.assertTrue(window.log_view.isVisible())
        window.clear_log_button.click()
        self.assertEqual(window.log_view.toPlainText(), "")
        window.close()

    def test_standard_window_keeps_every_page_horizontally_accessible(self) -> None:
        window = self.window()
        window.resize(1280, 820)
        window.show()
        for index in range(window.pages.count()):
            window.navigation.setCurrentRow(index)
            self.app.processEvents()
            self.assertEqual(
                window.pages.widget(index).horizontalScrollBar().maximum(),
                0,
                f"page {index} unexpectedly needs horizontal scrolling",
            )
        window.close()

    def test_task_center_refreshes_when_a_task_changes(self) -> None:
        window = self.window()
        task_id = window.task_manager.start("test", "Index local")
        self.app.processEvents()
        self.assertEqual(window.task_page.table.rowCount(), 1)
        window.task_manager.progress(task_id, 3, 10, "scan", "three")
        self.app.processEvents()
        progress = window.task_page.table.cellWidget(0, 4)
        self.assertEqual(progress.value(), 3)
        self.assertEqual(progress.maximum(), 10)
        window.task_manager.finish(task_id)
        self.assertEqual(window.task_manager.tasks[0].state, "completed")
        window.close()

    def test_language_change_retranslates_existing_widgets(self) -> None:
        window = self.window()
        window.change_language("fr")
        self.assertEqual(window.navigation.item(0).text(), "Accueil")
        self.assertEqual(window.clear_log_button.text(), "Effacer le journal")
        self.assertIn("Prêt", window.status_label.text())
        window.close()

    def test_organization_can_prepare_a_wiki_draft(self) -> None:
        window = self.window()
        window._prepare_wiki("Unit_Test_Wiki_Tag")
        self.assertEqual(window.navigation.currentRow(), window.NAVIGATION_KEYS.index("wiki"))
        self.assertEqual(window.wiki_page.tag.text(), "Unit_Test_Wiki_Tag")
        self.assertIn("[b]Description:[/b]", window.wiki_page.source.toPlainText())
        window.close()

    def test_taxonomy_preview_requires_explicit_confirmation(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        window = self.window()
        preview = {"boards": {"gelbooru": {}}}
        summary = {"total": 10, "added": 2, "removed": 1}
        with (
            patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.No,
            ),
            patch.object(window.organization_coordinator, "accept_preview") as accept,
            patch.object(window.organization_coordinator, "cancel_preview") as cancel,
        ):
            window._confirm_taxonomy_update(preview, summary)
        accept.assert_not_called()
        cancel.assert_called_once_with()

        with (
            patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch.object(window.organization_coordinator, "accept_preview") as accept,
        ):
            window._confirm_taxonomy_update(preview, summary)
        accept.assert_called_once_with(preview)
        window.close()
