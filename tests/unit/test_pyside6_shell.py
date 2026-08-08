import importlib.util
import os
import unittest


PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is not installed")
class PySide6ShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_exposes_top_level_navigation(self) -> None:
        from booruflow.application.capabilities import ApplicationCapabilities
        from booruflow.domain import ToolAvailability
        from booruflow.presentation.pyside6.main_window import MainWindow

        window = MainWindow(ApplicationCapabilities(ToolAvailability(False, "not configured")))
        self.assertEqual(window.navigation.count(), 7)
        self.assertEqual(window.pages.count(), 7)
        self.assertEqual(window.navigation.currentRow(), 0)
        window.close()

    def test_log_can_be_toggled_and_cleared(self) -> None:
        from booruflow.application.capabilities import ApplicationCapabilities
        from booruflow.domain import ToolAvailability
        from booruflow.presentation.pyside6.main_window import MainWindow

        window = MainWindow(ApplicationCapabilities(ToolAvailability(True)))
        window.show()
        self.app.processEvents()
        self.assertFalse(window.log_view.isVisible())
        window.toggle_log()
        self.app.processEvents()
        self.assertTrue(window.log_view.isVisible())
        window.clear_log_button.click()
        self.assertEqual(window.log_view.toPlainText(), "")
        window.close()
