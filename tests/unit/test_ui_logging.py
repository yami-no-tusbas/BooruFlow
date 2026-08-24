import unittest

from booruflow.presentation.pyside6.ui_logging import (
    StreamingLogSanitizer,
    log_event,
    sanitize_log_text,
)


class UiLoggingTests(unittest.TestCase):
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
