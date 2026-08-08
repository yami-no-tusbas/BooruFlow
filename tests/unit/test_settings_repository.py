import json
import tempfile
import unittest
from pathlib import Path

from booruflow.infrastructure.settings import JsonSettingsRepository


class JsonSettingsRepositoryTests(unittest.TestCase):
    def test_round_trip_and_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "settings.json"
            repository = JsonSettingsRepository(path)
            self.assertEqual(repository.load(), {})
            repository.save({"language": "fr", "enabled": True})
            self.assertEqual(repository.load(), {"language": "fr", "enabled": True})
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_invalid_json_returns_empty_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(JsonSettingsRepository(path).load(), {})
