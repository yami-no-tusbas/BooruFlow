import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
    def window(available: bool = False, **kwargs):
        from booruflow.application.capabilities import ApplicationCapabilities
        from booruflow.domain import ToolAvailability
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.main_window import MainWindow

        return MainWindow(
            ApplicationCapabilities(
                ToolAvailability(available, "not configured" if not available else "")
            ),
            LanguageCatalog(LANGUAGES),
            start_image_worker=False,
            **kwargs,
        )

    def open_feature(self, window, key: str) -> None:
        window.navigate_to_key(key)
        for _ in range(8):
            self.app.processEvents()

    @staticmethod
    def build_image_and_similar(window) -> None:
        image_page = window._build_image_analysis()
        window._install_feature_page("image_analysis", image_page)
        similar_page = window._build_similar_artists()
        window._install_feature_page("similar_artists", similar_page)

    def test_main_window_exposes_top_level_navigation(self) -> None:
        window = self.window()
        self.assertEqual(window.navigation.count(), 17)
        self.assertEqual(window.pages.count(), 16)
        self.assertNotIn("tagging_legacy", window.NAVIGATION_KEYS)
        visible_keys = [
            window.navigation.item(row).data(256)
            for row in range(window.navigation.count())
            if window.navigation.item(row).data(256)
        ]
        self.assertEqual(
            visible_keys,
            [
                "home", "tagging", "image_finder", "similar_artists", "organization",
                "tag_browser", "wiki_audit", "wiki", "grabber", "auto_organize",
                "folder_artists", "cleanup", "options",
            ],
        )
        self.assertNotIn("review", visible_keys)
        self.assertNotIn("image_analysis", visible_keys)
        self.assertNotIn("tasks", visible_keys)
        group_rows = [1, 5, 10, 15]
        self.assertTrue(all(not window.navigation.item(row).flags() for row in group_rows))
        self.assertTrue(all(window.navigation.item(row).icon().isNull() for row in group_rows))
        self.assertEqual(window.navigation.currentRow(), 0)
        self.assertEqual(window.constructed_page_keys, ("home",))
        self.assertIsNone(window.database_controller)
        self.assertIsNone(window.tagging_controller)
        self.assertIsNone(window.image_analysis_controller)
        self.assertIsNone(window.embedded_gelbooru_profile)
        window.close()

    def test_lazily_created_database_controller_updates_open_wizard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = self.window(project_root=Path(directory))
            window.open_first_run_wizard()
            wizard = window._setup_wizard
            self.assertIsNone(window.database_controller)
            self.open_feature(window, "options")
            self.assertIsNotNone(window.database_controller)
            window.database_controller.activity_changed.emit(
                "aliases", "running", "Aliases pages 2/5", 2, 5
            )
            self.assertIn("Aliases pages 2/5", wizard.page(2).activity_message.text())
            window.database_controller.activity_changed.emit(
                "aliases", "failed", "Helper exited with code 1", -1, -1
            )
            self.assertTrue(wizard.button(wizard.WizardButton.FinishButton).isEnabled())
            wizard.close()
            window.close()

    def test_wizard_finish_refreshes_open_options_and_persists_credentials(self) -> None:
        from booruflow.infrastructure.settings.json_repository import JsonSettingsRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = JsonSettingsRepository(root / "config" / "booruflow_credentials.json")
            window = self.window(project_root=root, credentials_repository=repository)
            self.open_feature(window, "options")
            window.open_first_run_wizard()
            wizard = window._setup_wizard
            wizard.user_id.setText("12345")
            wizard.api_key.setText("test-private-key")
            with patch(
                "booruflow.presentation.pyside6.credential_validation_controller.CredentialValidationController.start"
            ) as validate:
                wizard.test_credentials()
                validate.assert_called_once_with(
                    "gelbooru", {"user_id": "12345", "api_key": "test-private-key"}
                )
            wizard.show_credential_test_result("gelbooru", "valid")
            wizard.accept()
            self.assertEqual(window.options_page.user_id.text(), "12345")
            self.assertEqual(window.options_page.api_key.text(), "test-private-key")
            self.assertEqual(repository.load()["gelbooru"]["api_key"], "test-private-key")
            self.assertEqual(JsonSettingsRepository(repository.path).load()["gelbooru"]["user_id"], "12345")
            self.assertEqual(window.options_page._credentials["gelbooru"]["user_id"], "12345")
            self.assertNotIn("test-private-key", "\n".join(window._log_history))
            window.close()
            reopened = self.window(project_root=root, credentials_repository=JsonSettingsRepository(repository.path))
            self.open_feature(reopened, "options")
            self.assertEqual(reopened.options_page.user_id.text(), "12345")
            self.assertEqual(reopened.options_page.api_key.text(), "test-private-key")
            reopened.close()

    def test_wizard_configure_later_keeps_existing_credentials(self) -> None:
        from booruflow.infrastructure.settings.json_repository import JsonSettingsRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = JsonSettingsRepository(root / "config" / "booruflow_credentials.json")
            existing = {"gelbooru": {"user_id": "old", "api_key": "old-secret"}}
            repository.save(existing)
            window = self.window(project_root=root, credentials_repository=repository)
            self.open_feature(window, "options")
            window.open_first_run_wizard()
            window._setup_wizard.reject()
            self.assertEqual(repository.load(), existing)
            self.assertEqual(window.options_page.api_key.text(), "old-secret")
            window.close()

    def test_options_wd14_install_does_not_wait_for_worker_readiness(self) -> None:
        window = self.window()
        self.open_feature(window, "options")
        with patch.object(window, "_install_image_analysis_from_options") as install, \
                patch.object(window, "_run_when_ready") as wait_ready:
            window.options_page.wd14_install.click()
            install.assert_called_once_with("model")
            wait_ready.assert_not_called()
        window.close()

    def test_missing_wd14_options_click_reaches_installer_without_worker(self) -> None:
        from PySide6.QtCore import QProcess
        from PySide6.QtWidgets import QMessageBox

        with tempfile.TemporaryDirectory() as directory:
            window = self.window(project_root=Path(directory))
            self.open_feature(window, "options")
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as confirm:
                window.options_page.wd14_install.click()
            confirm.assert_called_once()
            controller = window.image_analysis_controller
            self.assertIsNotNone(controller)
            self.assertEqual(controller.process.state(), QProcess.ProcessState.NotRunning)
            self.assertEqual(controller.model_process.state(), QProcess.ProcessState.NotRunning)
            window.close()

    def test_sidebar_rows_map_to_pages_by_key_and_width_stays_fixed(self) -> None:
        from PySide6.QtWidgets import QHBoxLayout, QSplitter

        window = self.window()
        window.show()
        self.app.processEvents()
        workspace = window.centralWidget().layout().itemAt(0).widget()
        workspace_layout = workspace.layout()
        initial_width = window.navigation.width()
        self.assertGreaterEqual(initial_width, 176)
        self.assertLessEqual(initial_width, 320)
        self.assertNotIsInstance(workspace, QSplitter)
        self.assertIsInstance(workspace_layout, QHBoxLayout)
        margins = workspace_layout.contentsMargins()
        self.assertEqual(
            (margins.left(), margins.top(), margins.right(), margins.bottom()),
            (0, 0, 0, 0),
        )
        self.assertEqual(workspace_layout.spacing(), 0)
        self.assertIs(workspace_layout.itemAt(0).widget(), window.navigation)
        self.assertIs(workspace_layout.itemAt(1).widget(), window.pages)
        self.assertEqual(
            window.pages.geometry().left(), window.navigation.geometry().right() + 1
        )
        with patch.object(window.feature_lifecycle, "request"):
            for key in (
                "home", "tagging", "image_finder", "similar_artists", "organization",
                "tag_browser", "wiki_audit", "wiki", "grabber", "auto_organize",
                "folder_artists", "cleanup", "options",
            ):
                window.navigation.setCurrentRow(window._visible_navigation_row(key))
                self.assertEqual(window.pages.currentIndex(), window.NAVIGATION_KEYS.index(key))
                self.assertEqual(window.navigation.width(), initial_width)
        window.resize(1280, 760)
        self.app.processEvents()
        self.assertEqual(window.navigation.width(), initial_width)
        self.assertEqual(
            window.pages.geometry().left(), window.navigation.geometry().right() + 1
        )
        window.close()

    def test_status_bar_replaces_messages_and_manages_scoped_progress(self) -> None:
        window = self.window()
        status = window.status_controller
        self.assertIn("Home", window.status_label.text())

        status.show_message("home", "First message", 0)
        self.assertEqual(window.status_message_label.text(), "First message")
        status.show_message("home", "Replacement message", 0)
        self.assertEqual(window.status_message_label.text(), "Replacement message")
        self.assertEqual(window.status_message_label.toolTip(), "Replacement message")

        status.set_progress("home", 2, 5, "Home operation")
        self.assertFalse(window.global_progress.isHidden())
        self.assertEqual(window.global_progress.value(), 2)
        self.assertEqual(window.global_progress.maximum(), 5)
        status.set_progress("home", 4, 5, "Home operation")
        self.assertEqual(window.global_progress.value(), 4)

        window.navigate_to_key("image_finder")
        self.assertIn("Image Finder", window.status_label.text())
        self.assertEqual(window.status_message_label.text(), "")
        self.assertFalse(window.global_progress.isVisible())

        status.set_progress("image_finder", 0, 0, "Searching")
        self.assertFalse(window.global_progress.isHidden())
        self.assertEqual(window.global_progress.minimum(), 0)
        self.assertEqual(window.global_progress.maximum(), 0)
        status.clear_progress("image_finder")
        self.assertFalse(window.global_progress.isVisible())

        status.set_progress("image_finder", 1, 10, "Background task", True)
        window.navigate_to_key("wiki")
        self.assertFalse(window.global_progress.isHidden())
        self.assertEqual(window.global_progress.value(), 1)
        status.clear_progress("image_finder", True)
        self.assertTrue(window.global_progress.isHidden())
        window.close()

    def test_status_priority_keeps_errors_until_explicit_clear(self) -> None:
        window = self.window()
        status = window.status_controller
        status.show_message("home", "Database update failed — see log", 0, level="ERROR")
        status.show_message("home", "Ready", 5_000, level="NORMAL")
        self.assertEqual(window.status_message_label.text(), "Database update failed — see log")
        status.show_message("home", "Operation complete", 0, level="NORMAL")
        self.assertEqual(window.status_message_label.text(), "Database update failed — see log")
        status.clear_message()
        status.show_message("home", "Ready", 0, level="NORMAL")
        self.assertEqual(window.status_message_label.text(), "Ready")
        window.close()

    def test_status_progress_can_be_replaced_by_success(self) -> None:
        window = self.window()
        status = window.status_controller
        status.show_message("home", "Updating aliases", 0, level="PROGRESS")
        status.show_message("home", "Aliases updated", 0, level="NORMAL")
        self.assertEqual(window.status_message_label.text(), "Aliases updated")
        window.close()

    def test_page_activation_uses_central_debug_log_convention(self) -> None:
        window = self.window()
        window.navigate_to_key("options")
        self.assertTrue(
            any("[DEBUG] [UI] Page activated: Options" in line for line in window._log_history)
        )
        self.assertFalse(any("Page activated: Options" in line for line in window.log_view.toPlainText()))
        window.close()

    def test_tagging_and_tag_browser_publish_to_global_status_bar(self) -> None:
        from booruflow.application.targeted_wd14 import (
            TargetedWD14Progress,
            TargetedWD14Result,
        )

        window = self.window()
        self.open_feature(window, "tagging")
        window.tagging_page._confidence_tag = "1girl"
        window.tagging_page.show_targeted_wd14_result(
            TargetedWD14Result(
                "1girl", {}, frozenset(), TargetedWD14Progress(154, 154, 149, 5, 0), 0.1
            )
        )
        self.assertEqual(
            window.status_message_label.text(),
            "154 images · 149 cached · 5 new · 0 errors",
        )
        self.assertFalse(hasattr(window.tagging_page, "wd14_status"))

        self.open_feature(window, "tag_browser")
        window.tag_browser_page._copy_names(["one_tag"])
        self.assertEqual(window.status_message_label.text(), "1 tag copied")
        window.tag_browser_page._copy_names(["one_tag", "two_tags"])
        self.assertEqual(window.status_message_label.text(), "2 tags copied")
        window.navigate_to_key("wiki")
        self.assertEqual(window.status_message_label.text(), "")
        window.close()

    def test_image_finder_publishes_results_to_global_status_bar(self) -> None:
        window = self.window()
        self.open_feature(window, "image_finder")

        window.image_finder_controller._results(
            SimpleNamespace(artworks=(), next_cursor=None), append=False
        )

        self.assertEqual(window.status_message_label.text(), "0 results found")
        self.assertFalse(hasattr(window.image_finder_page, "status"))
        window.close()

    def test_status_bar_stays_responsive_with_long_messages(self) -> None:
        from PySide6.QtWidgets import QSizePolicy

        window = self.window()
        window.resize(860, 600)
        window.show()
        window.status_controller.show_message("home", "Long status message " * 80, 0)
        self.app.processEvents()
        self.assertEqual(window.width(), 860)
        self.assertEqual(
            window.status_message_label.sizePolicy().horizontalPolicy(),
            QSizePolicy.Policy.Ignored,
        )
        self.assertTrue(window.debug_log_toggle.isVisible())
        self.assertTrue(window.log_button.isVisible())
        self.assertTrue(window.clear_log_button.isVisible())
        window.close()

    def test_deferred_startup_does_not_run_similar_maintenance(self) -> None:
        from booruflow.domain.image_analysis import AnalysisItem, InputKind, SourceReference

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = self.window(project_root=root)
            self.build_image_and_similar(window)
            repository = window.image_analysis_controller.repository
            repository.add_item(
                AnalysisItem(
                    SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="42"),
                    cached_path=root / "42.png",
                    content_sha256="a" * 64,
                    mime_type="image/png",
                    width=1,
                    height=1,
                )
            )
            repository.cache_post_metadata(
                "gelbooru", "42", "https://example.invalid/42.png", (), ("artist_a",)
            )
            maintenance = MagicMock(wraps=window.similar_artists_controller.run_feature_maintenance)
            window.similar_artists_controller.run_feature_maintenance = maintenance
            window.image_analysis_controller.start_worker = MagicMock()
            window.complete_deferred_startup()
            self.app.processEvents()

            maintenance.assert_not_called()
            window.complete_deferred_startup()
            maintenance.assert_not_called()
            window.close()

    def test_similar_maintenance_waits_for_worker_exit_before_writing(self) -> None:
        from PySide6.QtCore import QProcess

        from booruflow.domain.image_analysis import AnalysisItem, InputKind, SourceReference
        from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = self.window(project_root=root)
            self.build_image_and_similar(window)
            repository = window.image_analysis_controller.repository
            item_id = repository.add_item(
                AnalysisItem(
                    SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="42"),
                    cached_path=root / "42.png",
                    content_sha256="b" * 64,
                    mime_type="image/png",
                    width=1,
                    height=1,
                )
            )
            repository.cache_post_metadata(
                "gelbooru", "42", "https://example.invalid/42.png", (), ("artist_b",)
            )
            competing = ImageAnalysisRepository(repository.path, timeout_seconds=0.05)
            competing.connection.execute("BEGIN IMMEDIATE")
            competing.connection.execute(
                "UPDATE analysis_items SET updated_at=updated_at WHERE id=?", (item_id,)
            )
            controller = window.image_analysis_controller
            controller.process.state = MagicMock(return_value=QProcess.ProcessState.Running)
            controller.process.write = MagicMock()
            controller.start_worker = MagicMock()
            completed = MagicMock()
            failed = MagicMock()

            controller.run_exclusive_database_operation(
                window.similar_artists_controller.prepare_feature, completed, failed
            )
            self.assertEqual(repository.artist_tags(item_id), ())
            controller.process.write.assert_called_once_with(b"STOP\n")

            competing.connection.rollback()
            competing.close()
            controller.process.state = MagicMock(return_value=QProcess.ProcessState.NotRunning)
            controller._worker_finished(0, None)
            self.app.processEvents()

            self.assertEqual(repository.artist_tags(item_id), ("artist_b",))
            completed.assert_called_once_with()
            failed.assert_not_called()
            controller.start_worker.assert_called_once_with()
            window.close()

    def test_similar_activation_does_not_stop_worker_after_maintenance_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = self.window(project_root=Path(directory))
            self.build_image_and_similar(window)
            similar = window.similar_artists_controller
            window.image_analysis_controller.repository.record_maintenance(
                similar.MAINTENANCE_KEY, {"rows": 0}
            )
            window.image_analysis_controller.run_exclusive_database_operation = MagicMock()

            completed = MagicMock()
            failed = MagicMock()
            similar.activate_async(completed, failed)
            similar.catalog_worker.wait(5_000)
            self.app.processEvents()

            window.image_analysis_controller.run_exclusive_database_operation.assert_not_called()
            self.assertTrue(similar._activated)
            window.close()

    def test_taxonomy_and_options_inventory_load_only_on_first_navigation(self) -> None:
        window = self.window()
        taxonomy = {
            "version": 1,
            "boards": {"gelbooru": {"People": {"__tags__": ["1girl"]}}, "e621": {}},
            "metadata": {},
            "sources": [],
            "excluded_imported_tags": {},
        }
        window.taxonomy_repository.load = MagicMock(return_value=taxonomy)
        with patch(
            "booruflow.presentation.pyside6.options_maintenance_controller."
            "OptionsMaintenanceController.refresh_storage"
        ) as refresh_storage:

            self.assertFalse(window._organization_loaded)
            self.assertEqual(window.taxonomy_repository.load.call_count, 0)
            self.open_feature(window, "organization")
            self.open_feature(window, "organization")
            self.assertEqual(window.taxonomy_repository.load.call_count, 1)
            self.assertIs(window.organization_page.document, taxonomy)
            self.open_feature(window, "options")
            self.open_feature(window, "options")
            self.assertEqual(refresh_storage.call_count, 1)
        window.close()

    def test_embedded_webengine_profile_is_lazy_and_created_once_on_gui_navigation(self) -> None:
        window = self.window()
        profile = object()
        bridge = SimpleNamespace(cancel=lambda: None)
        factory = object()
        with (
            patch(
                "booruflow.infrastructure.embedded_gelbooru.EmbeddedGelbooruProfile",
                return_value=profile,
            ) as profile_type,
            patch(
                "booruflow.infrastructure.embedded_gelbooru.EmbeddedGelbooruBridge",
                return_value=bridge,
            ),
            patch(
                "booruflow.infrastructure.embedded_gelbooru.EmbeddedGelbooruSessionFactory",
                return_value=factory,
            ),
        ):
            self.assertIsNone(window.embedded_gelbooru_profile)
            self.open_feature(window, "tagging")
            self.assertIsNone(window.embedded_gelbooru_profile)
            window._prepare_publication_backend()
            window._prepare_publication_backend()
            self.assertIs(window.embedded_gelbooru_profile, profile)
            self.assertIs(window.embedded_gelbooru_session_factory, factory)
            self.assertEqual(profile_type.call_count, 1)
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

    def test_log_ingestion_strips_ansi_without_damaging_unicode(self) -> None:
        from PySide6.QtWidgets import QApplication

        window = self.window()
        window.log("\x1b[31mERROR\x1b[0m Échec français 🚀")
        text = window.log_view.toPlainText()
        self.assertIn("ERROR Échec français 🚀", text)
        self.assertNotIn("\x1b", text)
        window.log("\x1b[1;34mBOLD BLUE\x1b[0m normal")
        self.assertIn("BOLD BLUE normal", window.log_view.toPlainText())
        window.log_view.selectAll()
        window.log_view.copy()
        copied = QApplication.clipboard().text()
        self.assertNotIn("\x1b", copied)
        self.assertIn("Échec français 🚀", copied)
        window.close()

    def test_debug_logs_are_hidden_by_default_but_remain_accessible(self) -> None:
        window = self.window()
        window.log("[DEBUG] [Worker] internal detail")
        window.log("[ERROR] [AutoOrganize] visible failure")
        self.assertNotIn("internal detail", window.log_view.toPlainText())
        self.assertIn("visible failure", window.log_view.toPlainText())
        window.debug_log_toggle.setChecked(True)
        self.assertIn("internal detail", window.log_view.toPlainText())
        self.assertIsNotNone(window.disk_log_path)
        disk=window.disk_log_path.read_text(encoding="utf-8")
        self.assertIn("internal detail",disk)
        window.close()

    def test_dashboard_cards_navigate_by_stable_key(self) -> None:
        window = self.window()
        dashboard = window.content_pages[0]
        card_keys = [card.navigation_key for card, *_rest in dashboard.card_widgets]
        self.assertEqual(
            card_keys,
            [
                "tagging", "image_finder", "similar_artists", "organization",
                "tag_browser", "wiki_audit", "wiki", "grabber", "auto_organize",
                "folder_artists", "cleanup", "options",
            ],
        )
        grabber_card = next(value for value in dashboard.card_widgets if value[0].navigation_key == "grabber")
        grabber_card[3].click()
        self.assertEqual(window.navigation.currentRow(), window._visible_navigation_row("grabber"))
        window.close()

    def test_grabber_tools_reuses_review_and_launcher_pages(self) -> None:
        window = self.window()
        self.open_feature(window, "grabber")
        self.assertIs(window.grabber_tools_page.tabs.widget(0), window.review_page)
        self.assertIs(window.grabber_tools_page.tabs.widget(1), window.grabber_page)
        self.assertEqual(window.grabber_tools_page.tabs.tabText(0), "Tag List Builder")
        self.assertEqual(window.grabber_tools_page.tabs.tabText(1), "Grabber Launcher")

        window.navigate_to_key("review")
        for _ in range(8):
            self.app.processEvents()
        self.assertEqual(window.navigation.currentRow(), window._visible_navigation_row("grabber"))
        self.assertIs(window.grabber_tools_page.tabs.currentWidget(), window.review_page)
        window.close()

    def test_saved_grabber_path_updates_home_and_loaded_grabber_tools(self) -> None:
        from booruflow.infrastructure.settings import JsonSettingsRepository

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "Grabber.exe"
            executable.touch()
            repository = JsonSettingsRepository(root / "settings.json")
            window = self.window(
                settings_repository=repository,
                project_root=root,
            )
            self.open_feature(window, "options")
            self.open_feature(window, "grabber")
            self.assertFalse(window.grabber_page.available)

            settings = dict(window._settings)
            settings["grabber_executable"] = str(executable)
            window._save_options(settings, {})

            self.assertTrue(window.capabilities.grabber.available)
            self.assertTrue(window.dashboard_page.grabber.available)
            self.assertFalse(hasattr(window.dashboard_page, "availability"))
            self.assertTrue(window.grabber_page.available)
            self.assertEqual(repository.load()["grabber_executable"], str(executable))
            window.close()

    def test_options_install_actions_route_to_existing_analysis_controller(self) -> None:
        window = self.window()
        window.options_page = MagicMock()
        window.image_analysis_controller = MagicMock()

        window._install_image_analysis_from_options("runtime")
        window._install_image_analysis_from_options("model")

        window.image_analysis_controller.bind_installation_page.assert_called_with(
            window.options_page
        )
        window.image_analysis_controller.install_gpu_runtime.assert_called_once_with(
            parent=window.options_page
        )
        window.image_analysis_controller.install_wd14.assert_called_once_with(
            parent=window.options_page
        )
        window.close()

    def test_lazy_page_is_constructed_once_then_reused(self) -> None:
        window = self.window()
        self.assertIsNone(window.auto_organize_page)
        self.assertIsNone(window.options_page)
        self.open_feature(window, "auto_organize")
        page = window.auto_organize_page
        controller = window.auto_organize_controller
        self.assertIsNotNone(page)
        self.open_feature(window, "home")
        self.open_feature(window, "auto_organize")
        self.assertIs(window.auto_organize_page, page)
        self.assertIs(window.auto_organize_controller, controller)
        window.close()

    def test_lazy_navigation_shows_existing_overlay_before_prepare_and_hides_it_once(self) -> None:
        from PySide6.QtWidgets import QLabel

        from booruflow.presentation.pyside6.feature_lifecycle import FeatureState

        window = self.window()
        registration = window.feature_lifecycle._features["auto_organize"]
        completions = []
        prepare_calls = []

        def delayed_prepare(done, _failed):
            prepare_calls.append(True)
            window._install_feature_page("auto_organize", QLabel("Loaded content"))
            completions.append(done)

        registration.prepare = delayed_prepare
        host = window.page_hosts["auto_organize"]
        window.navigate_to_key("auto_organize")

        self.assertIs(window.pages.currentWidget(), host)
        self.assertIsNotNone(host.loading)
        self.assertFalse(host.loading.isHidden())
        self.assertIn("Loading Auto organize", host.loading_label.text())
        self.assertIn("Auto organize — Loading", window.status_label.text())

        for _ in range(4):
            self.app.processEvents()
        self.assertIs(window.feature_lifecycle.state("auto_organize"), FeatureState.LOADING)
        self.assertEqual(len(prepare_calls), 1)
        self.assertFalse(host.loading.isHidden())

        completions[0]()
        self.app.processEvents()
        self.assertIs(window.feature_lifecycle.state("auto_organize"), FeatureState.READY)
        self.assertTrue(host.loading.isHidden())
        self.assertFalse(host.content.isHidden())
        self.assertIn("Auto organize — Ready", window.status_label.text())

        window.navigate_to_key("home")
        window.navigate_to_key("auto_organize")
        self.app.processEvents()
        self.assertEqual(len(prepare_calls), 1)
        self.assertTrue(host.loading.isHidden())
        window.close()

    def test_failed_lazy_page_is_isolated_and_retry_rebuilds_it(self) -> None:
        from booruflow.presentation.pyside6.feature_lifecycle import FeatureState

        window = self.window()
        registration = window.feature_lifecycle._features["grabber"]
        prepare = registration.prepare
        registration.prepare = lambda _done, failed: failed("synthetic failure")
        self.open_feature(window, "grabber")
        self.assertIs(window.feature_lifecycle.state("grabber"), FeatureState.FAILED)
        self.assertIsNone(window.review_page)
        host = window.page_hosts["grabber"]
        self.assertTrue(host.loading.isHidden())
        self.assertFalse(host.failure.isHidden())
        self.assertIn("Grabber Tools (Beta) — Failed", window.status_label.text())

        self.open_feature(window, "tasks")
        self.assertIs(window.feature_lifecycle.state("tasks"), FeatureState.READY)
        registration.prepare = prepare
        window.feature_lifecycle.retry("grabber")
        for _ in range(8):
            self.app.processEvents()
        self.assertIs(window.feature_lifecycle.state("grabber"), FeatureState.READY)
        self.assertIsNotNone(window.review_page)
        window.close()

    def test_dashboard_and_sidebar_share_the_same_lifecycle_entry(self) -> None:
        window = self.window()
        original = window.feature_lifecycle.request
        requested = []
        window.feature_lifecycle.request = lambda key: (requested.append(key), original(key))[1]
        card = next(
            value for value in window.dashboard_page.card_widgets
            if value[0].navigation_key == "folder_artists"
        )
        card[3].click()
        for _ in range(8):
            self.app.processEvents()
        window.navigation.setCurrentRow(window._visible_navigation_row("tag_browser"))
        for _ in range(8):
            self.app.processEvents()
        self.assertIn("folder_artists", requested)
        self.assertIn("tag_browser", requested)
        window.close()

    def test_close_with_unloaded_and_ready_lazy_features(self) -> None:
        window = self.window()
        self.open_feature(window, "auto_organize")
        window.auto_organize_controller.shutdown = MagicMock(return_value=True)
        self.assertIsNone(window.cleanup_controller)
        window.close()
        window.auto_organize_controller.shutdown.assert_called_once_with()

    def test_standard_window_keeps_every_page_horizontally_accessible(self) -> None:
        window = self.window()
        window.resize(1280, 820)
        window.show()
        for index in range(window.pages.count()):
            window.navigate_to(index)
            self.app.processEvents()
            self.assertEqual(
                window.pages.widget(index).horizontalScrollBar().maximum(),
                0,
                f"page {index} unexpectedly needs horizontal scrolling",
            )
        window.close()

    def test_image_analysis_action_bar_stays_inside_main_viewport(self) -> None:
        window = self.window()
        page = window._build_image_analysis()
        window._install_feature_page("image_analysis", page)
        image_analysis_index = window.NAVIGATION_KEYS.index("image_analysis")
        window.navigate_to_key("image_analysis"); window.show()
        buttons = (
            page.manual_add, page.accept, page.reject, page.accept_above,
            page.retry_button, page.skip_button, page.complete_button,
        )
        for width, height in ((1280, 720), (1600, 900), (1920, 1080)):
            window.resize(width, height); self.app.processEvents()
            host = window.pages.widget(image_analysis_index)
            self.assertEqual(host.verticalScrollBar().maximum(), 0)
            self.assertEqual(host.horizontalScrollBar().maximum(), 0)
            self.assertTrue(page.action_bar.isVisible())
            self.assertGreaterEqual(page.action_bar.height(), page.action_bar.minimumHeight())
            self.assertLessEqual(page.action_bar.geometry().bottom(), page.rect().bottom())
            for button in buttons:
                self.assertTrue(button.isVisible())
                self.assertGreater(button.width(), 0)
                self.assertGreater(button.height(), 0)
                self.assertTrue(page.action_bar.rect().contains(button.geometry()))
        page.image._source = page.image.label.grab().scaled(2400, 1600)
        preview_size = page.image.size(); page.image.set_zoom(400); self.app.processEvents()
        self.assertEqual(page.image.size(), preview_size)
        self.assertLessEqual(page.action_bar.geometry().bottom(), page.rect().bottom())
        window.close()

    def test_tagging_review_actions_have_nonzero_geometry_in_main_viewport(self) -> None:
        window = self.window()
        self.open_feature(window, "tagging")
        page = window.tagging_page
        window.show(); window.toggle_log()
        page._select_post({"id": 42, "tags": "solo"})
        page.show_local_review("Non analysée", None, ["solo"], [], [], [])
        buttons = (
            page.analyze_button, page.accept_button, page.reject_button, page.map_button,
            page.refresh_button, page.copy_button, page.copy_all_button,
            page.copy_open_button, page.open_button,
        )
        for width, height in ((1280, 720), (1600, 900), (1920, 1080)):
            window.resize(width, height); self.app.processEvents()
            self.assertTrue(page.action_bar.isVisible())
            self.assertGreater(page.action_bar.height(), 0)
            for button in buttons:
                self.assertTrue(button.isVisible(), button.text())
                self.assertGreater(button.width(), 0, button.text())
                self.assertGreater(button.height(), 0, button.text())
            host = window.pages.widget(2)
            self.assertEqual(host.verticalScrollBar().maximum(), 0)
        self.assertFalse(page.accept_button.isEnabled())
        self.assertFalse(page.reject_button.isEnabled())
        page.close(); window.close()

    def test_task_center_refreshes_when_a_task_changes(self) -> None:
        window = self.window()
        self.open_feature(window, "tasks")
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
        self.assertEqual(window.navigation.item(1).text(), "PRINCIPAL")
        self.assertEqual(window.clear_log_button.text(), "Effacer le journal")
        self.assertIn("Prêt", window.status_label.text())
        window.close()

    def test_organization_can_prepare_a_wiki_draft(self) -> None:
        window = self.window()
        window._prepare_wiki("Unit_Test_Wiki_Tag")
        for _ in range(8):
            self.app.processEvents()
        self.assertEqual(window.navigation.currentRow(), window._visible_navigation_row("wiki"))
        self.assertEqual(window.wiki_page.tag.text(), "Unit_Test_Wiki_Tag")
        self.assertIn("[b]Description:[/b]", window.wiki_page.source.toPlainText())
        window.close()

    def test_taxonomy_preview_requires_explicit_confirmation(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        window = self.window()
        self.open_feature(window, "organization")
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
