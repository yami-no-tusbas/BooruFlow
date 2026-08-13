import importlib.util
import json
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
        preview = render_wiki_preview("[h3]Related tags[/h3] [b]See also:[/b] [[Azur_Lane]] {{1girl}} [post]42[/post] gelbooru.com/index.php?page=wiki")
        self.assertIn("<h3>Related tags</h3>", preview)
        self.assertIn("<b>See also:</b>", preview)
        self.assertIn("booruflow-tag:Azur_Lane", preview)
        self.assertIn("tags=1girl", preview)
        self.assertIn("id=42", preview)
        self.assertIn('href="https://gelbooru.com/', preview)

    def test_validator_checks_all_five_heading_levels(self) -> None:
        self.assertEqual(validate_wiki_source("[h1]One[/h1]\n[h5]Five[/h5]"), [])
        self.assertIn(("unbalanced", "h4"), validate_wiki_source("[h4]Broken"))

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
            self.assertEqual(page.heading_selector.count(), 6)
            self.assertEqual(len(page.shortcuts), 11)
            self.assertIn("Ctrl+B", page.tool_buttons["bold"].toolTip())
            page.source.clear(); page._heading_selected(5)
            self.assertEqual(page.source.toPlainText(), "[h5]Heading[/h5]")
            page.source.setPlainText("[b]Description:[/b]")
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

    def test_loaded_nested_draft_is_saved_back_to_its_original_path(self) -> None:
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.presentation.pyside6.wiki_page import WikiPage

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "MSLN" / "signum.json"
            nested.parent.mkdir()
            nested.write_text(json.dumps({
                "tag": "signum", "template": "character", "source": "original",
            }), encoding="utf-8")
            page = WikiPage(LanguageCatalog(LANGUAGES, "en"), root)
            page._load_draft_path(nested)
            page.source.setPlainText("updated in place")
            page._save_draft()
            self.assertEqual(json.loads(nested.read_text(encoding="utf-8"))["source"], "updated in place")
            self.assertFalse((root / "signum.json").exists())
            page.close()

    def test_load_dialog_directory_is_persisted_with_safe_fallback(self) -> None:
        from booruflow.infrastructure.localization import LanguageCatalog
        from booruflow.infrastructure.settings import JsonSettingsRepository
        from booruflow.presentation.pyside6.wiki_page import WikiPage

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            drafts = root / "drafts"
            drafts.mkdir()
            nested = drafts / "Science Adventure" / "characters"
            nested.mkdir(parents=True)
            repository = JsonSettingsRepository(root / "settings.json")
            page = WikiPage(
                LanguageCatalog(LANGUAGES, "en"), drafts,
                settings_repository=repository,
            )

            self.assertEqual(page._initial_load_directory(), drafts)
            page._remember_load_directory(nested)
            self.assertEqual(page._initial_load_directory(), nested)
            self.assertEqual(
                repository.load()[page.LAST_LOAD_DIRECTORY_KEY], str(nested.resolve()),
            )

            nested.rmdir()
            self.assertEqual(page._initial_load_directory(), drafts)
            page.close()
