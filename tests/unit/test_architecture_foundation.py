import tempfile
import unittest
from pathlib import Path

from booruflow.domain import EntityType, SearchRequest, Site
from booruflow.infrastructure.grabber import GrabberInstallation


class ArchitectureFoundationTests(unittest.TestCase):
    def test_search_request_rejects_invalid_percent(self) -> None:
        with self.assertRaises(ValueError):
            SearchRequest(
                query="rating:general",
                sites=(Site.GELBOORU,),
                entity_type=EntityType.ARTISTS,
                minimum_match_percent=101,
            )

    def test_grabber_is_an_optional_capability(self) -> None:
        missing = GrabberInstallation(None).availability()
        with tempfile.TemporaryDirectory() as directory:
            configured_but_missing = GrabberInstallation(Path(directory)).availability()

        self.assertFalse(missing.available)
        self.assertFalse(configured_but_missing.available)
        self.assertIn("configured", missing.reason)
        self.assertIn("Grabber.exe", configured_but_missing.reason)


if __name__ == "__main__":
    unittest.main()
