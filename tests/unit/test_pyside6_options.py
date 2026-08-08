import importlib.util
import os
import unittest
from pathlib import Path


PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None
LANGUAGES = Path(__file__).resolve().parents[2] / "resources" / "i18n"


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is not installed")
class PySide6OptionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def catalog():
        from booruflow.infrastructure.localization import LanguageCatalog

        return LanguageCatalog(LANGUAGES)

    def test_site_credentials_remain_independent(self) -> None:
        from booruflow.presentation.pyside6.options_page import OptionsPage

        page = OptionsPage(
            self.catalog(),
            credentials={"gelbooru": {"user_id": "gel", "api_key": "one"}},
        )
        self.assertEqual(page.user_id.text(), "gel")
        page.site.setCurrentIndex(1)
        page.user_id.setText("e621-user")
        page.api_key.setText("two")
        page.site.setCurrentIndex(0)
        self.assertEqual(page.user_id.text(), "gel")
        self.assertEqual(page.api_key.text(), "one")
        page.close()

    def test_navigation_icons_are_large_colored_pictograms(self) -> None:
        from booruflow.presentation.pyside6.icons import NAVIGATION_COLORS, navigation_icon

        self.assertEqual(len(set(NAVIGATION_COLORS.values())), len(NAVIGATION_COLORS))
        pixmap = navigation_icon("home").pixmap(30, 30)
        self.assertFalse(pixmap.isNull())
        self.assertEqual(pixmap.width(), 30)
