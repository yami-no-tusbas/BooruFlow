import importlib.util
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is not installed")
class GelbooruBackendRoutingTests(unittest.TestCase):
    def test_tabbed_browser_home_and_account_follow_only_the_current_tab_site(self) -> None:
        from PySide6.QtCore import QUrl

        from booruflow.infrastructure.embedded_gelbooru import GelbooruSessionDialog

        fake = SimpleNamespace(view=SimpleNamespace(url=lambda: QUrl("https://e621.net/posts/42")))
        self.assertEqual(
            GelbooruSessionDialog._site_urls(fake),
            ("https://e621.net/", "https://e621.net/users/home"),
        )
        fake.view = SimpleNamespace(url=lambda: QUrl("https://gelbooru.com/index.php?page=post"))
        home, account = GelbooruSessionDialog._site_urls(fake)
        self.assertEqual(home, "https://gelbooru.com/")
        self.assertIn("page=account", account)

    def test_internal_browser_tabs_are_separate_from_hidden_publisher_page(self) -> None:
        window = self.window()
        window._open_gelbooru_session()
        dialog = window.gelbooru_session_dialog
        self.assertFalse(dialog.address.isReadOnly())
        self.assertEqual(dialog.tabs.count(), 2)
        self.assertIsNot(dialog.view.page(), window.embedded_gelbooru_bridge.page)
        self.assertFalse(dialog.diagnostics_group.isChecked())
        first_count = dialog.tabs.count()
        dialog.new_tab("about:blank")
        self.assertEqual(dialog.tabs.count(), first_count + 1)
        dialog.close_tab(dialog.tabs.currentIndex())
        self.assertEqual(dialog.tabs.count(), first_count)
        window.close()

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def window(settings_repository=None):
        from booruflow.application.capabilities import ApplicationCapabilities
        from booruflow.domain import ToolAvailability
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.main_window import MainWindow

        return MainWindow(
            ApplicationCapabilities(ToolAvailability(False)),
            LanguageCatalog(Path("resources/i18n")),
            start_image_worker=False,
            settings_repository=settings_repository,
        )

    def test_backend_selection_routes_options_and_tagging_to_the_same_factory(self) -> None:
        window = self.window()
        embedded = object()
        cdp = SimpleNamespace(open=lambda: None)
        window.embedded_gelbooru_session_factory = embedded
        window.gelbooru_session_factory = cdp

        self.assertIs(window._active_gelbooru_session_factory(), embedded)
        self.assertIs(window.tagging_controller._session_factory_provider(), embedded)
        diagnostic = window.tagging_controller._diagnostic_mode_provider
        self.assertIsNotNone(diagnostic)
        self.assertFalse(diagnostic())

        window._set_embedded_form_diagnostic_active(True)
        self.assertTrue(diagnostic())
        publisher = window._build_gelbooru_publisher()
        self.assertTrue(publisher.transport.transport.diagnostic_only)

        combo = window.options_page.publish_backend
        combo.setCurrentIndex(combo.findData("cdp"))

        self.assertEqual(window.publish_backend, "cdp")
        self.assertIs(window._active_gelbooru_session_factory(), cdp)
        self.assertIs(window.tagging_controller._session_factory_provider(), cdp)
        self.assertFalse(diagnostic())

        combo.setCurrentIndex(combo.findData("disabled"))
        self.assertIsNone(window._active_gelbooru_session_factory())
        self.assertIsNone(window.tagging_controller._session_factory_provider())
        window.close()

    def test_publisher_and_preparation_use_only_the_queued_gui_log_signal(self) -> None:
        window = self.window()

        publisher = window._build_gelbooru_publisher()

        self.assertEqual(publisher.log, window.log_threadsafe)
        self.assertEqual(publisher.preparation.log, window.log_threadsafe)
        self.assertNotEqual(publisher.log, window.log)
        self.assertNotEqual(publisher.preparation.log, window.log)
        window.close()

    def test_tagging_query_is_persisted_and_restored_with_default_only_when_missing(self) -> None:
        class Settings:
            def __init__(self) -> None:
                self.values = {}

            def load(self):
                return dict(self.values)

            def save(self, values):
                self.values = dict(values)

        settings = Settings()
        first = self.window(settings)
        self.assertEqual(first.tagging_page.query.text(), "rating:general")

        first.tagging_page.query_saved.emit("artist_name landscape")
        first.close()
        second = self.window(settings)

        self.assertEqual(second.tagging_page.query.text(), "artist_name landscape")
        self.assertNotEqual(second.tagging_page.query.text(), "rating:general")
        second.close()

    def test_embedded_dialog_diagnostic_toggle_updates_future_publishers(self) -> None:
        window = self.window()

        window._open_gelbooru_session()
        dialog = window.gelbooru_session_dialog
        self.assertIsNotNone(dialog)
        self.assertFalse(window._embedded_form_diagnostic_enabled())

        dialog.manual_diagnostic.setChecked(True)

        self.assertTrue(window._embedded_form_diagnostic_enabled())
        publisher = window._build_gelbooru_publisher()
        self.assertTrue(publisher.transport.transport.diagnostic_only)
        dialog.view.stop()
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()
        window.close()

    def test_http_diagnostic_toggle_keeps_real_submit_path_and_arms_transport(self) -> None:
        window = self.window()
        window._open_gelbooru_session()
        dialog = window.gelbooru_session_dialog
        self.assertIsNotNone(dialog)
        armed = []
        window.embedded_gelbooru_profile.arm_http_diagnostic = (
            lambda source, **values: armed.append((source, values)) or True
        )

        dialog.http_removals.setText("irene_(arknights)")
        dialog.http_diagnostic.setChecked(True)
        dialog._http_submit_guard_ready('{"status":"armed"}')

        self.assertTrue(window._embedded_http_diagnostic_enabled())
        self.assertFalse(window._embedded_form_diagnostic_enabled())
        publisher = window._build_gelbooru_publisher()
        transport = publisher.transport.transport
        self.assertFalse(transport.diagnostic_only)
        self.assertTrue(transport.http_diagnostic)
        self.assertEqual(armed[0][0], "manual")
        self.assertIs(armed[0][1]["page"], dialog.view.page())
        self.assertEqual(armed[0][1]["removals"], ("irene_(arknights)",))
        dialog._http_diagnostic_finished("manual")
        self.assertFalse(window._embedded_http_diagnostic_enabled())
        self.assertFalse(dialog.http_diagnostic.isChecked())
        dialog.view.stop()
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()
        window.close()

    def test_http_diagnostic_ui_refuses_to_arm_without_startup_mode(self) -> None:
        window = self.window()
        window._open_gelbooru_session()
        dialog = window.gelbooru_session_dialog
        self.assertIsNotNone(dialog)
        window.embedded_gelbooru_profile._cdp_configuration = SimpleNamespace(
            enabled=False, error="startup_mode_disabled"
        )

        dialog.http_diagnostic.setChecked(True)
        dialog._http_submit_guard_ready('{"status":"armed"}')

        self.assertTrue(dialog.http_diagnostic.isChecked())
        self.assertFalse(window._embedded_http_diagnostic_enabled())
        self.assertIn("startup_mode_disabled", dialog.status.text())
        self.assertIn("Save changes remains blocked", dialog.status.text())
        dialog.view.stop()
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()
        window.close()

    def test_http_diagnostic_refuses_a_multi_entry_real_batch(self) -> None:
        from booruflow.presentation.pyside6.tagging_controller import TaggingController

        state = SimpleNamespace(value="pending_publish")
        entries = [
            {
                "site": "gelbooru", "post_id": str(post_id),
                "publish_state": state, "item_id": post_id,
                "additions": (), "removals": ("highres",),
            }
            for post_id in (1, 2)
        ]
        messages = []
        controller = TaggingController.__new__(TaggingController)
        controller.catalog = SimpleNamespace(
            text=lambda key, **_kwargs: {
                "tagging.publish.http_requires_one": (
                    "The real HTTP diagnostic requires exactly one pending Gelbooru entry. "
                    "Nothing was sent."
                )
            }[key]
        )
        controller.publish_worker = None
        controller.image_analysis = SimpleNamespace(
            repository=SimpleNamespace(list_batch_entries=lambda: entries)
        )
        controller.page = SimpleNamespace(
            show_batch_publish_summary=messages.append
        )
        controller._publisher_factory = object()
        controller._diagnostic_mode_provider = lambda: False
        controller._http_diagnostic_mode_provider = lambda: True

        controller._start_batch_publish(None)

        self.assertEqual(len(messages), 1)
        self.assertIn("exactly one", messages[0])
        self.assertIn("Nothing was sent", messages[0])
        self.assertIsNone(controller.publish_worker)

    def test_cdp_open_and_session_test_never_call_embedded_backend(self) -> None:
        window = self.window()
        calls = []
        cdp = SimpleNamespace(
            open=lambda: calls.append("cdp-open"),
            validate=lambda: calls.append("cdp-validate"),
        )
        embedded = SimpleNamespace(validate=lambda: calls.append("embedded-validate"))
        window.gelbooru_session_factory = cdp
        window.embedded_gelbooru_session_factory = embedded

        class Completed:
            def connect(self, callback):
                self.callback = callback

        class Worker:
            def __init__(self, factory):
                self.factory = factory
                self.completed = Completed()

            def isRunning(self):
                return False

            def start(self):
                self.factory.validate()
                self.completed.callback("Session Gelbooru valide.")

        combo = window.options_page.publish_backend
        combo.setCurrentIndex(combo.findData("cdp"))
        with (
            patch("booruflow.presentation.pyside6.main_window.SessionTestWorker", Worker),
            patch("booruflow.presentation.pyside6.tagging_controller.SessionTestWorker", Worker),
        ):
            window._open_gelbooru_session()
            window._test_gelbooru_session()
            window.tagging_controller.test_gelbooru_session()

        self.assertEqual(calls, ["cdp-open", "cdp-validate", "cdp-validate"])
        self.assertNotIn("embedded-validate", calls)
        self.assertIn("backend=browser-cdp", window.log_view.toPlainText())
        window.close()

    def test_embedded_open_remains_embedded_and_disabled_is_inert(self) -> None:
        window = self.window()
        calls = []
        window.embedded_gelbooru_session_factory = SimpleNamespace(
            validate=lambda: calls.append("embedded-validate")
        )
        window.gelbooru_session_dialog = SimpleNamespace(
            open_url=lambda *_args, **_kwargs: calls.append("account"),
            show=lambda: calls.append("show"),
            raise_=lambda: calls.append("raise"),
            activateWindow=lambda: calls.append("activate"),
        )

        class Completed:
            def connect(self, callback):
                self.callback = callback

        class Worker:
            def __init__(self, factory):
                self.factory = factory
                self.completed = Completed()

            def isRunning(self):
                return False

            def start(self):
                self.factory.validate()
                self.completed.callback("valid")

        window._open_gelbooru_session()
        with patch("booruflow.presentation.pyside6.main_window.SessionTestWorker", Worker):
            window._test_gelbooru_session()
        self.assertEqual(calls, ["account", "show", "raise", "activate", "embedded-validate"])

        combo = window.options_page.publish_backend
        combo.setCurrentIndex(combo.findData("disabled"))
        window._open_gelbooru_session()
        self.assertEqual(calls, ["account", "show", "raise", "activate", "embedded-validate"])
        self.assertFalse(window.options_page.open_embedded_session.isEnabled())
        self.assertFalse(window.options_page.test_embedded_session.isEnabled())
        window.close()

    def test_cdp_selection_is_persisted_and_used_after_save(self) -> None:
        class Repository:
            def __init__(self):
                self.saved = []

            def load(self):
                return {"gelbooru_publish_backend": "embedded"}

            def save(self, values):
                self.saved.append(dict(values))

        repository = Repository()
        window = self.window(repository)
        cdp = object()
        window.gelbooru_session_factory = cdp

        combo = window.options_page.publish_backend
        combo.setCurrentIndex(combo.findData("cdp"))
        window.options_page.save_button.click()

        self.assertEqual(window.publish_backend, "cdp")
        self.assertEqual(repository.saved[-1]["gelbooru_publish_backend"], "cdp")
        self.assertIs(window._active_gelbooru_session_factory(), cdp)
        self.assertIs(window.tagging_controller._session_factory_provider(), cdp)
        window.close()
