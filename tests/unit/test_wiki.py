import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from booruflow.application.wiki import missing_local_tags, render_wiki_preview, validate_wiki_source


PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None
LANGUAGES = Path(__file__).resolve().parents[2] / "resources" / "i18n"


class WikiMarkupTests(unittest.TestCase):
    def test_validator_rejects_danbooru_alias_spaces_and_headings(self) -> None:
        issues = validate_wiki_source("h4. Title\n[[bad tag]] [[good_tag|label]]")
        self.assertEqual({code for code, _value in issues}, {"heading", "spaces", "alias"})

    def test_preview_renders_confirmed_gelbooru_markup(self) -> None:
        preview = render_wiki_preview("[b]See also:[/b] [[Azur_Lane]] {{1girl}} [post]42[/post] gelbooru.com/index.php?page=wiki")
        self.assertIn("<b>See also:</b>", preview)
        self.assertIn("booruflow-tag:Azur_Lane", preview)
        self.assertIn("tags=1girl", preview)
        self.assertIn("id=42", preview)
        self.assertIn('href="https://gelbooru.com/', preview)

    def test_missing_tags_are_checked_case_insensitively_in_local_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tags.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute("CREATE TABLE tags(name TEXT)")
                connection.execute("INSERT INTO tags(name) VALUES('Azur_Lane')")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(missing_local_tags(path, ["azur_lane", "missing_tag"]), ["missing_tag"])


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is not installed")
class WikiPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_character_template_and_local_draft(self) -> None:
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.wiki_page import WikiPage

        with tempfile.TemporaryDirectory() as directory:
            page = WikiPage(LanguageCatalog(LANGUAGES, "en"), Path(directory))
            page.set_tag("Aulick_(Azur_Lane)")
            self.assertIn("[b]Description:[/b]", page.source.toPlainText())
            page._save_draft()
            draft = Path(directory) / "Aulick_(Azur_Lane).json"
            self.assertTrue(draft.is_file())
            page.source.setPlainText("custom draft")
            page._save_draft()
            page.set_tag("Other_character")
            self.assertNotEqual(page.source.toPlainText(), "custom draft")
            page.set_tag("Aulick_(Azur_Lane)")
            self.assertEqual(page.source.toPlainText(), "custom draft")
            page.close()
