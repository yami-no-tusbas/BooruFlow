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

    def test_api_key_is_masked_and_eye_button_reveals_without_changing_value(self) -> None:
        from PySide6.QtWidgets import QLineEdit

        from booruflow.presentation.pyside6.options_page import OptionsPage

        page = OptionsPage(self.catalog(), credentials={"gelbooru": {"api_key": "secret"}})
        self.assertEqual(page.api_key.echoMode(), QLineEdit.EchoMode.Password)
        page.show_api_key.click()
        self.assertEqual(page.api_key.echoMode(), QLineEdit.EchoMode.Normal)
        self.assertEqual(page.api_key.text(), "secret")
        page.show_api_key.click()
        self.assertEqual(page.api_key.echoMode(), QLineEdit.EchoMode.Password)
        page.close()

    def test_credential_validation_logs_only_sanitized_site_and_result(self) -> None:
        from booruflow.application.credential_validation import (
            CredentialStatus,
            CredentialValidationResult,
        )
        from booruflow.presentation.pyside6.credential_validation_controller import (
            CredentialValidationController,
        )

        logs = []
        page = type("Page", (), {"show_credential_test_result": lambda *_args: None})()
        controller = CredentialValidationController(page, log=logs.append)
        controller._completed(CredentialValidationResult("e621", CredentialStatus.INVALID))
        self.assertIn("[WARNING] [Credentials] e621 validation result=invalid_credentials", logs[0])
        self.assertNotIn("Authorization", logs[0])
        self.assertNotIn("api_key", logs[0])

    def test_e621_uses_username_label_and_preserves_historical_persisted_key(self) -> None:
        from PySide6.QtTest import QSignalSpy

        from booruflow.presentation.pyside6.options_page import OptionsPage

        page = OptionsPage(self.catalog(), credentials={"e621": {"user_id": "wolf", "api_key": "key"}})
        page.site.setCurrentIndex(page.site.findData("e621"))
        self.assertEqual(page.user_id_label.text(), "Username:")
        self.assertEqual(page.user_id.text(), "wolf")
        requested = QSignalSpy(page.credentials_test_requested)
        page.test_credentials.click()
        self.assertEqual(requested.at(0), ["e621", {"user_id": "wolf", "api_key": "key"}])
        page.close()

    def test_credential_states_are_site_scoped_and_retranslate_without_reset(self) -> None:
        from PySide6.QtWidgets import QLineEdit

        from booruflow.presentation.pyside6.options_page import OptionsPage

        catalog = self.catalog()
        page = OptionsPage(catalog, credentials={"e621": {"user_id": "wolf", "api_key": "key"}})
        page.set_credential_test_running("gelbooru")
        page.site.setCurrentIndex(page.site.findData("e621"))
        page.show_credential_test_result("gelbooru", "invalid")
        self.assertEqual(page.credentials_status.text(), "Not tested")
        page.show_credential_test_result("e621", "valid")
        self.assertEqual(page.credentials_status.text(), "Valid")
        page.show_api_key.click()
        catalog.set_language("fr")
        page.retranslate()
        self.assertEqual(page.credentials_status.text(), "Valides")
        self.assertEqual(page.user_id.text(), "wolf")
        self.assertEqual(page.api_key.text(), "key")
        self.assertEqual(page.site.currentData(), "e621")
        self.assertEqual(page.api_key.echoMode(), QLineEdit.EchoMode.Normal)
        page.show_api_key.click()
        self.assertEqual(page.api_key.echoMode(), QLineEdit.EchoMode.Password)
        page.close()

    def test_database_selector_preserves_site_specific_paths_and_routes_updates(self) -> None:
        from PySide6.QtTest import QSignalSpy

        from booruflow.presentation.pyside6.options_page import OptionsPage

        page = OptionsPage(self.catalog(), {"gelbooru_database": "gel.db", "e621_database": "e.db"})
        requested = QSignalSpy(page.database_update_requested)
        self.assertEqual(page.database_path.edit.text(), "gel.db")
        page.database_site.setCurrentIndex(page.database_site.findData("e621"))
        self.assertEqual(page.database_path.edit.text(), "e.db")
        page.database_path.edit.setText("new-e.db")
        page.database_path.action.click()
        self.assertEqual(requested.at(0), ["e621", "new-e.db"])
        page.database_site.setCurrentIndex(page.database_site.findData("gelbooru"))
        self.assertEqual(page.database_path.edit.text(), "gel.db")
        page.close()

    def test_image_analysis_primary_and_advanced_values_use_runtime_keys(self) -> None:
        from PySide6.QtTest import QSignalSpy

        from booruflow.presentation.pyside6.options_page import OptionsPage

        page = OptionsPage(self.catalog(), {
            "image_analysis_wd14_display_threshold": 0.30,
            "image_analysis_wd14_store_threshold": 0.10,
            "image_analysis_worker_heartbeat_interval": 3,
            "image_analysis_worker_stale_timeout": 21,
            "image_analysis_worker_recycle_after": 500,
        })
        self.assertEqual(page.wd14_threshold.value(), 30)
        self.assertEqual(page.store_threshold.value(), 10)
        saved = QSignalSpy(page.save_requested)
        page.save_button.click()
        values = saved.at(0)[0]
        self.assertEqual(values["image_analysis_wd14_display_threshold"], 0.30)
        self.assertEqual(values["image_analysis_wd14_store_threshold"], 0.10)
        self.assertEqual(values["image_analysis_worker_recycle_after"], 500)
        page.close()

    def test_image_analysis_labels_tooltips_and_percentages_survive_retranslation(self) -> None:
        from PySide6.QtTest import QSignalSpy

        from booruflow.presentation.pyside6.options_page import OptionsPage

        catalog = self.catalog()
        page = OptionsPage(catalog, {
            "image_analysis_wd14_enabled": True,
            "image_analysis_wd14_display_threshold": 0.10,
            "image_analysis_wd14_store_threshold": 0.30,
            "image_analysis_download_prefetch": 10,
            "image_analysis_analysis_prefetch": 2,
            "image_analysis_worker_heartbeat_interval": 2,
            "image_analysis_worker_stale_timeout": 15,
            "image_analysis_worker_recycle_after": 0,
        })
        labels = (
            page.wd14_enabled.text(), page.wd14_threshold_label.text(),
            page.download_prefetch_label.text(), page.analysis_prefetch_label.text(),
            page.store_threshold_label.text(), page.heartbeat_label.text(),
            page.stale_timeout_label.text(), page.recycle_count_label.text(),
        )
        self.assertTrue(all(labels))
        self.assertTrue(all(widget.toolTip() for widget in (
            page.wd14_enabled, page.wd14_threshold, page.store_threshold,
            page.download_prefetch, page.analysis_prefetch, page.heartbeat,
            page.stale_timeout, page.recycle_count,
        )))
        self.assertEqual((page.wd14_threshold.value(), page.store_threshold.value()), (10, 30))
        page.image_advanced.setChecked(True)
        catalog.set_language("fr"); page.retranslate()
        self.assertTrue(page.image_advanced.isChecked())
        self.assertEqual((page.wd14_threshold.value(), page.store_threshold.value()), (10, 30))
        saved = QSignalSpy(page.save_requested); page.save_button.click()
        values = saved.at(0)[0]
        self.assertEqual(values["image_analysis_wd14_display_threshold"], 0.10)
        self.assertEqual(values["image_analysis_wd14_store_threshold"], 0.30)
        page.close()

    def test_malformed_percent_compatibility_is_shown_as_percent_then_normalized_on_save(self) -> None:
        from PySide6.QtTest import QSignalSpy

        from booruflow.presentation.pyside6.options_page import OptionsPage

        page = OptionsPage(self.catalog(), {"image_analysis_wd14_display_threshold": 10})
        self.assertEqual(page.wd14_threshold.value(), 10)
        saved = QSignalSpy(page.save_requested); page.save_button.click()
        self.assertEqual(saved.at(0)[0]["image_analysis_wd14_display_threshold"], 0.10)
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
            (page.database_site_label, "Site :"),
            (page.database_path_label, "Chemin de la base :"),
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
        self.assertEqual(page.open_embedded_session.text(), "Login / Reconnect")
        self.assertIn("localhost", page.publisher_explanation.text())
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
        self.assertEqual(page.embedded_session_status.text(), "Gelbooru session: Not tested")
        page.test_embedded_session.click(); page.test_embedded_session.click()
        self.assertEqual(spy.count(), 1)
        self.assertFalse(page.test_embedded_session.isEnabled())
        self.assertIn("Checking", page.embedded_session_status.text())
        page.show_embedded_session_test_result("Session Gelbooru valide.")
        self.assertTrue(page.test_embedded_session.isEnabled())
        self.assertEqual(
            page.embedded_session_status.text(), "Gelbooru session: Session Gelbooru valide."
        )
        page.close()

    def test_alias_actions_are_localized_on_tagging_and_preserve_database_path(self) -> None:
        from PySide6.QtTest import QSignalSpy

        from booruflow.presentation.pyside6.tagging_page import TaggingPage

        catalog = self.catalog()
        catalog.set_language("fr")
        page = TaggingPage(
            catalog,
            {
                "gelbooru_tag_database": "D:/tags.db",
                "gelbooru_alias_database": "D:/aliases.db",
            },
        )
        requested = QSignalSpy(page.alias_update_requested)
        stopped = QSignalSpy(page.alias_stop_requested)

        self.assertEqual(page.alias_update.text(), "Mettre à jour")
        self.assertEqual(page.alias_pending.text(), "Vérifier pending")
        self.assertEqual(page.alias_reconcile.text(), "Réconcilier")
        page.alias_reconcile.click()
        self.assertEqual(requested.at(0), ["full", str(Path("D:/aliases.db"))])

        page.set_alias_running(True)
        self.assertEqual(page.alias_update.text(), "Arrêter")
        self.assertTrue(page.alias_update.isEnabled())
        self.assertFalse(page.alias_pending.isEnabled())
        self.assertFalse(page.alias_reconcile.isEnabled())
        page.alias_update.click()
        self.assertEqual(stopped.count(), 1)

        page.set_alias_summary({
            "active": "12", "pending": "2", "missing": "1", "new": "3",
            "modified": "4", "checkpoint": "20", "state": "completed",
        })
        self.assertIn("actifs : 12", page.alias_status.text())
        self.assertIn("checkpoint : 20", page.alias_status.text())
        page.close()
