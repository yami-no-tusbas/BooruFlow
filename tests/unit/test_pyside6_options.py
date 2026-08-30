import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_blacklist_file_and_other_paths_load_and_save(self) -> None:
        from PySide6.QtTest import QSignalSpy

        from booruflow.presentation.pyside6.options_page import OptionsPage

        settings = {
            "gelbooru_database": "gel.db",
            "e621_database": "e621.db",
            "blacklist_file": "blacklist.txt",
            "output_root": "results",
        }
        page = OptionsPage(self.catalog(), settings)
        saved = QSignalSpy(page.save_requested)

        self.assertEqual(page.gelbooru_database.edit.text(), "gel.db")
        self.assertEqual(page.e621_database.edit.text(), "e621.db")
        self.assertEqual(page.blacklist_file.edit.text(), "blacklist.txt")
        self.assertEqual(page.output_root.edit.text(), "results")
        page.save_button.click()

        emitted = saved.at(0)[0]
        self.assertEqual(emitted["blacklist_file"], "blacklist.txt")
        self.assertNotIn("grabber_directory", emitted)
        self.assertEqual(emitted["gelbooru_database"], "gel.db")
        self.assertEqual(emitted["e621_database"], "e621.db")
        self.assertEqual(emitted["output_root"], "results")
        page.close()

    def test_blacklist_browse_uses_text_file_dialog(self) -> None:
        from booruflow.presentation.pyside6.options_page import OptionsPage

        page = OptionsPage(self.catalog(), {})
        with patch(
            "booruflow.presentation.pyside6.options_page.QFileDialog.getOpenFileName",
            return_value=("D:/lists/blacklist.txt", "Text files (*.txt)"),
        ) as choose:
            page.blacklist_file.button.click()

        self.assertEqual(page.blacklist_file.edit.text(), "D:/lists/blacklist.txt")
        self.assertEqual(choose.call_args.args[1], "Choose a blacklist file")
        self.assertEqual(choose.call_args.args[3], "Text files (*.txt);;All files (*)")
        page.close()

    def test_path_labels_are_complete_in_french(self) -> None:
        from booruflow.presentation.pyside6.options_page import OptionsPage
        from booruflow.presentation.pyside6.pages import ScrollablePageHost

        catalog = self.catalog()
        catalog.set_language("fr")
        page = OptionsPage(catalog, {})
        host = ScrollablePageHost(page)
        host.resize(820, 720)
        host.show()
        self.app.processEvents()

        expected = (
            (page.gelbooru_database_label, "Base de tags Gelbooru"),
            (page.e621_database_label, "Base de tags e621"),
            (page.blacklist_file_label, "Fichier blacklist"),
            (page.output_root_label, "Dossier de sortie"),
        )
        for label, text in expected:
            self.assertEqual(label.text(), text)
            self.assertTrue(label.isVisibleTo(host))
            self.assertGreaterEqual(label.geometry().width(), 190)
            self.assertGreater(label.geometry().height(), 0)
        host.close()

    def test_embedded_publisher_is_default_and_independent_from_open_browser(self) -> None:
        from booruflow.presentation.pyside6.options_page import OptionsPage

        page = OptionsPage(self.catalog(), {"gelbooru_browser_mode": "system"})
        self.assertEqual(page.publish_backend.currentData(), "embedded")
        self.assertEqual(page.browser_mode.currentData(), "system")
        self.assertTrue(page.open_embedded_session.isEnabled())
        page.publish_backend.setCurrentIndex(page.publish_backend.findData("cdp"))
        self.assertTrue(page.open_embedded_session.isEnabled())
        self.assertTrue(page.test_embedded_session.isEnabled())
        self.assertIn("dédié", page.open_embedded_session.text())
        self.assertIn("127.0.0.1", page.publisher_explanation.text())
        page.publish_backend.setCurrentIndex(page.publish_backend.findData("disabled"))
        self.assertFalse(page.open_embedded_session.isEnabled())
        self.assertFalse(page.test_embedded_session.isEnabled())
        page.close()

    def test_publication_backend_selection_is_emitted_immediately_and_saved(self) -> None:
        from PySide6.QtTest import QSignalSpy

        from booruflow.presentation.pyside6.options_page import OptionsPage

        page = OptionsPage(self.catalog(), {})
        changed = QSignalSpy(page.publication_backend_changed)
        saved = QSignalSpy(page.save_requested)

        page.publish_backend.setCurrentIndex(page.publish_backend.findData("cdp"))
        page.save_button.click()

        self.assertEqual(changed.count(), 1)
        self.assertEqual(changed.at(0), ["cdp"])
        self.assertEqual(saved.at(0)[0]["gelbooru_publish_backend"], "cdp")
        page.close()

    def test_embedded_session_test_has_persistent_inline_feedback(self) -> None:
        from PySide6.QtTest import QSignalSpy

        from booruflow.presentation.pyside6.options_page import OptionsPage

        page = OptionsPage(self.catalog(), {})
        spy = QSignalSpy(page.embedded_session_test_requested)
        page.embedded_session_test_requested.connect(
            lambda: page.set_embedded_session_test_running(True)
        )
        self.assertEqual(page.embedded_session_status.text(), "État : Non testé")
        page.test_embedded_session.click(); page.test_embedded_session.click()
        self.assertEqual(spy.count(), 1)
        self.assertFalse(page.test_embedded_session.isEnabled())
        self.assertIn("Test en cours", page.embedded_session_status.text())
        page.show_embedded_session_test_result("Session Gelbooru valide.")
        self.assertTrue(page.test_embedded_session.isEnabled())
        self.assertEqual(
            page.embedded_session_status.text(), "État : Session Gelbooru valide."
        )
        page.close()
