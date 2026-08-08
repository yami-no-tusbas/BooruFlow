import importlib.util
import os
import unittest


PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is not installed")
class PySide6OptionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_site_credentials_remain_independent(self) -> None:
        from booruflow.presentation.pyside6.options_page import OptionsPage

        page = OptionsPage(credentials={"gelbooru": {"user_id": "gel", "api_key": "one"}})
        self.assertEqual(page.user_id.text(), "gel")
        page.site.setCurrentIndex(1)
        page.user_id.setText("e621-user")
        page.api_key.setText("two")
        page.site.setCurrentIndex(0)
        self.assertEqual(page.user_id.text(), "gel")
        self.assertEqual(page.api_key.text(), "one")
        page.close()

    def test_navigation_icons_are_large_and_colored(self) -> None:
        from booruflow.presentation.pyside6.icons import NAVIGATION_ICONS, navigation_icon

        colors = {color for _glyph, color in NAVIGATION_ICONS.values()}
        self.assertEqual(len(colors), len(NAVIGATION_ICONS))
        pixmap = navigation_icon("Home").pixmap(28, 28)
        self.assertFalse(pixmap.isNull())
        self.assertEqual(pixmap.width(), 28)
