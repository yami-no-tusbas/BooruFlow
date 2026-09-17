import unittest

from booruflow.infrastructure.localization import translate_legacy_log


class LegacyLogTranslationTests(unittest.TestCase):
    def test_french_output_is_translated_for_english_ui(self) -> None:
        line = "Page Gelbooru 121 (1/20 du bloc) : 100 posts, total analysé 100"
        translated = translate_legacy_log(line, "en")
        self.assertEqual(
            translated,
            "Gelbooru page 121 (1/20 in this block) : 100 posts, total analyzed 100",
        )

    def test_paths_and_queries_are_preserved(self) -> None:
        line = "Classement détaillé : D:\\BooruFlow\\résultats\\rating_general.csv"
        translated = translate_legacy_log(line, "en")
        self.assertIn("D:\\BooruFlow\\résultats\\rating_general.csv", translated)

    def test_french_ui_keeps_engine_output(self) -> None:
        line = "Calculs terminés."
        self.assertEqual(translate_legacy_log(line, "fr"), line)
