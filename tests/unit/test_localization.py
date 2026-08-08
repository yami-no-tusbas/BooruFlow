import json
import tempfile
import unittest
from pathlib import Path

from booruflow.infrastructure.localization import LanguageCatalog


class LanguageCatalogTests(unittest.TestCase):
    def test_discovers_added_languages_and_falls_back_to_english(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "en.json").write_text(
                json.dumps({"_meta": {"name": "English"}, "hello": "Hello", "only.en": "Fallback"}),
                encoding="utf-8",
            )
            (root / "de.json").write_text(
                json.dumps({"_meta": {"name": "Deutsch"}, "hello": "Hallo"}),
                encoding="utf-8",
            )
            catalog = LanguageCatalog(root, "de")
            self.assertEqual(catalog.available, {"de": "Deutsch", "en": "English"})
            self.assertEqual(catalog.text("hello"), "Hallo")
            self.assertEqual(catalog.text("only.en"), "Fallback")

    def test_unknown_language_uses_english(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "en.json").write_text(
                json.dumps({"_meta": {"name": "English"}, "hello": "Hello"}),
                encoding="utf-8",
            )
            catalog = LanguageCatalog(root, "xx")
            self.assertEqual(catalog.code, "en")
            self.assertEqual(catalog.text("hello"), "Hello")
class BundledCatalogTests(unittest.TestCase):
    def test_french_and_english_catalogs_have_the_same_keys(self) -> None:
        languages = Path(__file__).resolve().parents[2] / "resources" / "i18n"
        english = json.loads((languages / "en.json").read_text(encoding="utf-8"))
        french = json.loads((languages / "fr.json").read_text(encoding="utf-8"))
        self.assertEqual(set(english) - {"_meta"}, set(french) - {"_meta"})
