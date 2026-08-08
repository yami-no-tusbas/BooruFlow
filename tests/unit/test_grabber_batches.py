import tempfile
import unittest
from pathlib import Path

from booruflow.application.grabber_batches import (
    BatchRequest, GrabberSessionStore, build_tab, read_tag_entries,
)


class GrabberBatchTests(unittest.TestCase):
    def test_settings_remain_independent_and_auth_is_site_scoped(self) -> None:
        gel = build_tab("cat", "42", "secret", images_per_tab=25, prefix="", suffix="solo")
        e621 = build_tab("wolf", "42", "secret", site="e621", images_per_tab=100)
        self.assertEqual(gel["perpage"], 25)
        self.assertEqual(e621["perpage"], 100)
        self.assertIn("api_key=secret", gel["lastUrls"]["gelbooru.com"]["Json"])
        self.assertNotIn("secret", str(e621))

    def test_session_creation_filters_without_deleting_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = BatchRequest(read_tag_entries("cat\ndog", "gelbooru"), 1, 20, "", "")
            state, skipped = GrabberSessionStore(root).create(request, "1", "key", {"dog"})
            self.assertEqual(skipped, 1)
            self.assertEqual(state["total_tags"], 1)
            self.assertTrue((root / "tabs.json").is_file())
            self.assertTrue((root / "artist_by_tag_session.json").is_file())
