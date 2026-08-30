import tempfile
import unittest
from pathlib import Path

from booruflow.infrastructure.settings import JsonSettingsRepository, migrate_blacklist_setting


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

    def test_legacy_blacklist_folder_is_migrated_only_when_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blacklist = root / "blacklist.txt"
            blacklist.write_text("blocked_tag\n", encoding="utf-8")
            executable = root / "Grabber.exe"
            executable.touch()

            migrated, changed = migrate_blacklist_setting(
                {"grabber_directory": str(root), "output_root": "results"}
            )

            self.assertTrue(changed)
            self.assertEqual(migrated["blacklist_file"], str(blacklist))
            self.assertEqual(migrated["grabber_executable"], str(executable))
            self.assertNotIn("grabber_directory", migrated)
            self.assertEqual(migrated["output_root"], "results")

    def test_legacy_blacklist_folder_is_preserved_when_file_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = {"grabber_directory": directory}
            migrated, changed = migrate_blacklist_setting(settings)

            self.assertFalse(changed)
            self.assertEqual(migrated, settings)
