import importlib.util
import os
import unittest
from pathlib import Path


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
            ApplicationCapabilities(ToolAvailability(available, "not configured" if not available else "")),
            LanguageCatalog(LANGUAGES),
        )

    def test_main_window_exposes_top_level_navigation(self) -> None:
        window = self.window()
        self.assertEqual(window.navigation.count(), 8)
        self.assertEqual(window.pages.count(), 8)
        self.assertEqual(window.navigation.item(4).data(256), "wiki")
        self.assertEqual(window.navigation.currentRow(), 0)
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

    def test_language_change_retranslates_existing_widgets(self) -> None:
        window = self.window()
        window.change_language("fr")
        self.assertEqual(window.navigation.item(0).text(), "Accueil")
        self.assertEqual(window.clear_log_button.text(), "Effacer le journal")
        self.assertIn("Prêt", window.status_label.text())
        window.close()

    def test_organization_can_prepare_a_wiki_draft(self) -> None:
        window = self.window()
        window._prepare_wiki("Aulick_(Azur_Lane)")
        self.assertEqual(window.navigation.currentRow(), window.NAVIGATION_KEYS.index("wiki"))
        self.assertEqual(window.wiki_page.tag.text(), "Aulick_(Azur_Lane)")
        self.assertIn("[b]Description:[/b]", window.wiki_page.source.toPlainText())
        window.close()
