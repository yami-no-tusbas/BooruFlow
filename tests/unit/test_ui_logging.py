import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from booruflow.presentation.pyside6.ui_logging import (
    LOG_FILENAME_PATTERN,
    RunLog,
    StreamingLogSanitizer,
    log_event,
    sanitize_log_text,
)


class UiLoggingTests(unittest.TestCase):
    def test_each_run_gets_a_timestamped_unique_filename(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            launched_at = datetime(2026, 8, 30, 8, 15, 42, tzinfo=UTC)
            first = RunLog.create(root, launched_at=launched_at)
            second = RunLog.create(root, launched_at=launched_at)
            self.assertEqual(first.path.name, "booruflow_2026-08-30_08-15-42.log")
            self.assertEqual(second.path.name, "booruflow_2026-08-30_08-15-42_01.log")
            self.assertNotEqual(first.path, second.path)
            self.assertTrue(LOG_FILENAME_PATTERN.fullmatch(first.path.name))
            self.assertTrue(LOG_FILENAME_PATTERN.fullmatch(second.path.name))

    def test_file_entry_contains_full_date_and_time(self) -> None:
        with TemporaryDirectory() as temporary:
            run_log = RunLog.create(Path(temporary))
            formatted = run_log.format(
                "Publish Gelbooru #14675642: published",
                logged_at=datetime(2026, 8, 30, 16, 34, 56, tzinfo=UTC),
            )
            run_log.write(formatted)
            self.assertEqual(
                run_log.path.read_text(encoding="utf-8"),
                "[2026-08-30 16:34:56] Publish Gelbooru #14675642: published\n",
            )

    def test_retention_only_deletes_eligible_old_logs_and_keeps_current(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            log_directory = root / "var" / "logs"
            log_directory.mkdir(parents=True)
            for day in range(1, 6):
                (log_directory / f"booruflow_2026-08-0{day}_08-00-00.log").touch()
            unrelated = log_directory / "notes.log"
            legacy = log_directory / "booruflow.log"
            unrelated.touch()
            legacy.touch()

            current = RunLog.create(
                root,
                launched_at=datetime(2026, 8, 30, 8, 0, 0, tzinfo=UTC),
                retention_count=3,
            )

            remaining = {path.name for path in log_directory.iterdir()}
            self.assertIn(current.path.name, remaining)
            self.assertIn("booruflow_2026-08-05_08-00-00.log", remaining)
            self.assertIn("booruflow_2026-08-04_08-00-00.log", remaining)
            self.assertNotIn("booruflow_2026-08-03_08-00-00.log", remaining)
            self.assertIn(unrelated.name, remaining)
            self.assertIn(legacy.name, remaining)

    def test_cleanup_failure_does_not_crash_initialization(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "var" / "logs" / "booruflow_2026-08-01_08-00-00.log"
            old.parent.mkdir(parents=True)
            old.touch()
            with patch.object(Path, "unlink", side_effect=PermissionError("locked")):
                run_log = RunLog.create(
                    root,
                    launched_at=datetime(2026, 8, 30, 8, 0, 0, tzinfo=UTC),
                    retention_count=1,
                )
            self.assertTrue(run_log.path.exists())
            self.assertTrue(old.exists())

    def test_unwritable_log_directory_falls_back_without_crashing(self) -> None:
        with TemporaryDirectory() as temporary:
            with patch.object(Path, "mkdir", side_effect=PermissionError("read-only")):
                run_log = RunLog.create(Path(temporary))
            self.assertIsNone(run_log.path)
            self.assertRegex(run_log.format("still visible in UI"), r"^\[\d{4}-\d{2}-\d{2} ")

    def test_ansi_csi_is_removed_and_unicode_is_preserved(self) -> None:
        value = "\x1b[31mERROR\x1b[0m \x1b[1;34mgras\x1b[0m — français 🚀"
        self.assertEqual(sanitize_log_text(value), "ERROR gras — français 🚀")

    def test_normal_text_is_unchanged(self) -> None:
        self.assertEqual(sanitize_log_text("normal\nsecond"), "normal\nsecond")

    def test_structured_event_has_component_level_and_context(self) -> None:
        event = log_event(
            "Tagging", "Existing analysis reused", context="gelbooru:42 item:7"
        )
        self.assertEqual(
            event,
            "[INFO] [Tagging] [gelbooru:42 item:7] Existing analysis reused",
        )

    def test_fragmented_csi_is_sanitized_streamingly(self) -> None:
        sanitizer = StreamingLogSanitizer()
        chunks = ["\x1b", "[31mERR", "OR\x1b[", "0m test\n"]
        self.assertEqual("".join(sanitizer.feed(chunk) for chunk in chunks), "ERROR test\n")
        self.assertEqual(sanitizer.flush(), "")

    def test_fragmented_osc_and_controls_are_removed(self) -> None:
        sanitizer = StreamingLogSanitizer()
        chunks = ["before\x1b]0;ti", "tle\x07after\x00\x03", " français 🚀"]
        self.assertEqual(
            "".join(sanitizer.feed(chunk) for chunk in chunks),
            "beforeafter français 🚀",
        )
