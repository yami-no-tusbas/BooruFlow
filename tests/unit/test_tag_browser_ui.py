import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None
LANGUAGES = Path(__file__).resolve().parents[2] / "resources" / "i18n"


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is not installed")
class TagBrowserUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def page(self, language: str = "en"):
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.tag_browser_page import TagBrowserPage

        return TagBrowserPage(LanguageCatalog(LANGUAGES, language))

    def test_filters_have_explicit_accessible_labels_in_both_languages(self) -> None:
        expected = {
            "en": ("Ambiguity:", "Tag type:", "Outgoing alias:", "Posts:", "Result limit:"),
            "fr": (
                "Ambiguïté :", "Type de tag :", "Alias sortant :", "Posts :",
                "Limite de résultats :",
            ),
        }
        for language, labels in expected.items():
            with self.subTest(language=language):
                page = self.page(language)
                self.assertEqual(
                    (
                        page.ambiguous_label.text(), page.state_label.text(),
                        page.alias_label.text(), page.posts_label.text(), page.limit_label.text(),
                    ),
                    labels,
                )
                self.assertIs(page.ambiguous_label.buddy(), page.ambiguous)
                self.assertIs(page.state_label.buddy(), page.state)
                self.assertIs(page.alias_label.buddy(), page.alias)
                self.assertIs(page.posts_label.buddy(), page.minimum)
                self.assertIs(page.limit_label.buddy(), page.limit)
                self.assertEqual(page.minimum.accessibleName(), page.catalog.text("tag_browser.minimum"))
                self.assertEqual(page.maximum.accessibleName(), page.catalog.text("tag_browser.maximum"))
                self.assertTrue(page.ambiguous.toolTip())
                page.close()

    def test_filter_labels_preserve_width_and_controls_stay_compact(self) -> None:
        from PySide6.QtWidgets import QComboBox, QSizePolicy

        page = self.page("fr")
        self.assertEqual(page.filters_layout.count(), 2)
        self.assertEqual(page.filter_row_layout.count(), 6)
        self.assertTrue(page.filter_row_layout.hasHeightForWidth())
        self.assertGreater(
            page.filter_row_layout.heightForWidth(700),
            page.filter_row_layout.heightForWidth(1800),
        )
        self.assertEqual(page.layout().stretch(2), 1)
        for label in (
            page.site_label, page.query_label, page.mode_label, page.category_label,
            page.ambiguous_label, page.state_label, page.alias_label, page.posts_label,
            page.limit_label,
        ):
            self.assertTrue(label.property("preserveHorizontalSize"))
            self.assertEqual(label.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Minimum)
            self.assertGreater(label.sizeHint().width(), 0)
        self.assertEqual(page.query.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Expanding)
        for combo in (page.site, page.mode, page.category, page.ambiguous, page.state, page.alias):
            self.assertEqual(combo.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Maximum)
            self.assertEqual(
                combo.sizeAdjustPolicy(), QComboBox.SizeAdjustPolicy.AdjustToContents
            )
        for spinbox in (page.minimum, page.maximum, page.limit):
            self.assertEqual(spinbox.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Maximum)
        page.close()

    def test_renamed_filters_keep_the_same_request_values(self) -> None:
        page = self.page()
        page.query.setText("  fox_ears  ")
        page.mode.setCurrentIndex(page.mode.findData("exact"))
        page.category.setCurrentIndex(page.category.findData(1))
        page.ambiguous.setCurrentIndex(page.ambiguous.findData(1))
        page.state.setCurrentIndex(page.state.findData("alias"))
        page.alias.setCurrentIndex(page.alias.findData("with"))
        page.minimum.setValue(42)
        page.maximum.setValue(500)
        page.limit.setValue(2345)

        request = page._request()
        self.assertEqual(request.text, "fox_ears")
        self.assertEqual(request.mode, "exact")
        self.assertEqual(request.category, 1)
        self.assertEqual(request.ambiguous, 1)
        self.assertEqual(request.state, "alias")
        self.assertEqual(request.alias, "with")
        self.assertEqual(request.minimum_count, 42)
        self.assertEqual(request.maximum_count, 500)
        self.assertEqual(request.limit, 2345)

        page.maximum.setValue(0)
        page.ambiguous.setCurrentIndex(page.ambiguous.findData(0))
        self.assertIsNone(page._request().maximum_count)
        self.assertEqual(page._request().ambiguous, 0)
        page.close()

    def test_site_change_rebuilds_categories_and_disables_e621_alias_filter(self) -> None:
        page = self.page()
        self.assertEqual(page.category.itemText(page.category.findData(6)), "Deprecated")
        self.assertTrue(page.alias.isEnabled())
        page.site.setCurrentIndex(page.site.findData("e621"))
        self.assertEqual(page.category.itemText(page.category.findData(5)), "Species")
        self.assertEqual(page.category.itemText(page.category.findData(6)), "Invalid")
        self.assertFalse(page.alias.isEnabled())
        page.close()

    def test_decorated_alias_copy_keeps_raw_tag(self) -> None:
        from booruflow.infrastructure.tag_browser import TagRow

        page = self.page()
        page._show_rows([TagRow(1, "old_tag", 5, 6, 0, "middle", "new_tag")])
        self.assertEqual(page.table.item(0, 1).text(), "old_tag (deprecated) → new_tag")
        page.table.selectRow(0)
        with patch.object(page, "_copy_names") as copy_names:
            page.copy_selection()
        copy_names.assert_called_once_with(["old_tag"])
        page.close()

    def test_copy_feedback_uses_status_channel_and_correct_plural(self) -> None:
        from PySide6.QtTest import QSignalSpy

        page = self.page()
        messages = QSignalSpy(page.page_status.message_changed)
        self.assertFalse(hasattr(page, "status"))

        page._copy_names(["one_tag"])
        self.assertEqual(messages.at(0)[1], "1 tag copied")
        page._copy_names(["one_tag", "two_tags"])
        self.assertEqual(messages.at(1)[1], "2 tags copied")
        self.assertTrue(messages.at(0)[3])
        page.close()

    def test_double_click_opens_raw_tag_on_selected_site(self) -> None:
        from booruflow.infrastructure.tag_browser import TagRow
        from booruflow.presentation.pyside6 import tag_browser_page

        page = self.page()
        page.site.setCurrentIndex(page.site.findData("e621"))
        page._show_rows([TagRow(1, "raw tag", 5, 4, 0, None, "canonical_tag")])
        with patch.object(tag_browser_page.QDesktopServices, "openUrl") as opened:
            page._open_row(0, canonical=False)
        self.assertEqual(opened.call_args.args[0].toString(), "https://e621.net/posts?tags=raw+tag")
        page.close()


if __name__ == "__main__":
    unittest.main()
