import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None
LANGUAGES = Path(__file__).resolve().parents[2] / "resources" / "i18n"


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is not installed")
class LiveThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def palette(dark: bool):
        from PySide6.QtGui import QColor, QPalette

        palette = QPalette()
        colors = (
            {
                QPalette.ColorRole.Window: "#202124",
                QPalette.ColorRole.WindowText: "#f1f3f4",
                QPalette.ColorRole.Base: "#303134",
                QPalette.ColorRole.AlternateBase: "#292a2d",
                QPalette.ColorRole.Text: "#f1f3f4",
                QPalette.ColorRole.Button: "#35363a",
                QPalette.ColorRole.ButtonText: "#f1f3f4",
                QPalette.ColorRole.Mid: "#80868b",
                QPalette.ColorRole.PlaceholderText: "#9aa0a6",
                QPalette.ColorRole.Highlight: "#8ab4f8",
                QPalette.ColorRole.HighlightedText: "#202124",
            }
            if dark
            else {
                QPalette.ColorRole.Window: "#f4f5f7",
                QPalette.ColorRole.WindowText: "#202124",
                QPalette.ColorRole.Base: "#ffffff",
                QPalette.ColorRole.AlternateBase: "#eef0f3",
                QPalette.ColorRole.Text: "#202124",
                QPalette.ColorRole.Button: "#f1f3f4",
                QPalette.ColorRole.ButtonText: "#202124",
                QPalette.ColorRole.Mid: "#9aa0a6",
                QPalette.ColorRole.PlaceholderText: "#5f6368",
                QPalette.ColorRole.Highlight: "#1a73e8",
                QPalette.ColorRole.HighlightedText: "#ffffff",
            }
        )
        for role, color in colors.items():
            palette.setColor(role, QColor(color))
        return palette

    def make_window(self, root: Path):
        from booruflow.application.capabilities import ApplicationCapabilities
        from booruflow.domain import ToolAvailability
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.main_window import MainWindow

        return MainWindow(
            ApplicationCapabilities(ToolAvailability(False)),
            LanguageCatalog(LANGUAGES),
            project_root=root,
            resource_root=LANGUAGES.parents[1],
            start_image_worker=False,
        )

    def settle(self) -> None:
        for _ in range(4):
            self.app.processEvents()

    def test_existing_widgets_match_cold_theme_without_losing_state(self) -> None:
        from PySide6.QtGui import QPalette

        original = QPalette(self.app.palette())
        with tempfile.TemporaryDirectory() as directory:
            window = None
            cold_window = None
            try:
                light = self.palette(False)
                dark = self.palette(True)
                self.app.setPalette(light)
                window = self.make_window(Path(directory))
                window.navigate_to_key("tagging")
                self.settle()
                tagging = window.tagging_page
                tagging.show_results([
                    {"id": 42, "tag_count": 3, "tags": "alpha beta", "priority": "low"}
                ])
                card = tagging.result_buttons[42]
                card.setChecked(True)
                card_identity = id(card)
                tagging_identity = id(tagging)

                window.navigate_to_key("wiki")
                self.settle()
                wiki = window.wiki_page
                self.assertIsNotNone(wiki)
                wiki_identity = id(wiki)
                icon_key = wiki.tool_buttons["bold"].icon().cacheKey()

                for palette in (dark, light, dark, light):
                    self.app.setPalette(palette)
                    self.settle()
                    self.assertEqual(
                        card.selection_checkbox.palette().color(QPalette.ColorRole.Text),
                        palette.color(QPalette.ColorRole.Text),
                    )
                    self.assertEqual(
                        wiki.title.palette().color(QPalette.ColorRole.WindowText),
                        palette.color(QPalette.ColorRole.WindowText),
                    )
                    for row in (1, 5, 10, 15):
                        item = window.navigation.item(row)
                        self.assertEqual(
                            item.foreground().color(),
                            palette.color(QPalette.ColorRole.PlaceholderText),
                        )
                        self.assertEqual(
                            item.background().color(),
                            palette.color(QPalette.ColorRole.Window),
                        )
                    self.assertEqual(id(card), card_identity)
                    self.assertEqual(id(tagging), tagging_identity)
                    self.assertEqual(id(window.wiki_page), wiki_identity)
                    self.assertTrue(card.isChecked())
                    self.assertEqual([post["id"] for post in tagging.result_posts], [42])
                    self.assertEqual(window.navigation.currentRow(), 9)

                self.assertNotEqual(wiki.tool_buttons["bold"].icon().cacheKey(), icon_key)

                # A page/card created after the transition must start in the
                # same palette state as the already-instantiated card.
                cold_window = self.make_window(Path(directory))
                cold_window.navigate_to_key("tagging")
                self.settle()
                cold_window.tagging_page.show_results(
                    [{
                        "id": 43,
                        "tag_count": 2,
                        "tags": "gamma delta",
                        "priority": "low",
                    }]
                )
                cold_card = cold_window.tagging_page.result_buttons[43]
                self.assertEqual(
                    card.selection_checkbox.palette().color(QPalette.ColorRole.Text),
                    cold_card.selection_checkbox.palette().color(QPalette.ColorRole.Text),
                )
                cold_window.navigate_to_key("wiki")
                self.settle()
                self.assertEqual(
                    cold_window.wiki_page.title.palette().color(QPalette.ColorRole.WindowText),
                    light.color(QPalette.ColorRole.WindowText),
                )
            finally:
                if cold_window is not None:
                    cold_window.close()
                if window is not None:
                    window.close()
                self.app.setPalette(original)
                self.settle()


if __name__ == "__main__":
    unittest.main()
