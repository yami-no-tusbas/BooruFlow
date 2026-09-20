from __future__ import annotations

import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None
LANGUAGES = Path(__file__).resolve().parents[2] / "resources" / "i18n"


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is not installed")
class OrganizationControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def build(self, directory: str):
        from booruflow.application.taxonomy import TaxonomyRepository, default_document
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.organization_controller import (
            OrganizationCoordinator,
        )
        from booruflow.presentation.pyside6.organization_page import OrganizationPage

        catalog = LanguageCatalog(LANGUAGES, "en")
        repository = TaxonomyRepository(
            Path(directory) / "taxonomy.json", Path(directory) / "databases"
        )
        page = OrganizationPage(catalog, default_document())
        coordinator = OrganizationCoordinator(
            Path(directory), catalog, page, repository, None, dict, lambda _value: None
        )
        return page, coordinator, repository

    def wait_until(self, predicate, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate())

    def test_update_stop_requests_cooperative_cancellation_and_restores_buttons(self) -> None:
        from booruflow.infrastructure.wiki_tag_importer import WikiImportCancelled

        with tempfile.TemporaryDirectory() as directory:
            page, coordinator, _repository = self.build(directory)
            page.stop_requested.connect(coordinator.stop_update)
            states: list[str] = []
            messages: list[str] = []
            page.page_status.state_changed.connect(lambda _page, state: states.append(state))
            page.page_status.message_changed.connect(
                lambda _page, message, *_rest: messages.append(message)
            )
            started = Event()
            units: list[int] = []

            def import_until_cancelled(*, progress, cancelled):
                while True:
                    if cancelled():
                        raise WikiImportCancelled()
                    units.append(len(units))
                    progress(f"unit {len(units)}")
                    started.set()
                    time.sleep(0.01)

            self.assertTrue(page.update_button.isEnabled())
            self.assertFalse(page.stop_button.isEnabled())
            with patch(
                "booruflow.infrastructure.wiki_tag_importer.import_catalogues",
                side_effect=import_until_cancelled,
            ):
                coordinator.update()
                self.wait_until(started.is_set)
                self.assertFalse(page.update_button.isEnabled())
                self.assertTrue(page.stop_button.isEnabled())
                page.stop_button.click()
                units_at_cancel = len(units)
                self.assertFalse(page.stop_button.isEnabled())
                self.wait_until(lambda: coordinator.taxonomy_worker is None)

            self.assertEqual(len(units), units_at_cancel)
            self.assertTrue(page.update_button.isEnabled())
            self.assertFalse(page.stop_button.isEnabled())
            self.assertIn("stopping", states)
            self.assertEqual(states[-1], "ready")
            self.assertIn("Update stopped", messages)
            page.close()

    def test_cache_hit_is_displayed_without_starting_a_worker(self) -> None:
        from booruflow.infrastructure.wiki_page_cache import WikiPageCache, WikiPageRecord

        with tempfile.TemporaryDirectory() as directory:
            page, coordinator, repository = self.build(directory)
            WikiPageCache(repository.database_path("gelbooru")).put(WikiPageRecord(
                "gelbooru", "cached_tag", "Cached body", "https://wiki/1",
                author="Alice", remote_updated_at="2026-09-20T09:00:00+00:00",
            ))
            page.details_title.setText("cached_tag")
            page.current_details_tag = "cached_tag"
            with patch(
                "booruflow.presentation.pyside6.organization_controller.TagDetailsWorker"
            ) as worker:
                coordinator.load_details("gelbooru", "cached_tag")
            worker.assert_not_called()
            self.assertIn("Cached body", page.definition.toPlainText())
            self.assertIn("Alice", page.wiki_metadata.text())
            self.assertIn("Remote updated", page.wiki_metadata.text())
            self.assertIn("Cached at", page.wiki_metadata.text())
            page.close()

    def test_stale_detail_response_does_not_replace_current_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            page, coordinator, _repository = self.build(directory)
            coordinator.details_generation = 2
            page.details_title.setText("tag_b")
            with patch.object(page, "show_tag_details") as shown:
                coordinator.details_ready(
                    1,
                    {"board": "gelbooru", "tag": "tag_a", "wiki_exists": True},
                    False,
                    0.1,
                )
            shown.assert_not_called()
            page.close()


if __name__ == "__main__":
    unittest.main()
