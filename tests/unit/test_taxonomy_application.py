import tempfile
import unittest
from pathlib import Path

from booruflow.application.taxonomy import TaxonomyRepository, iter_tag_paths


class TaxonomyApplicationTests(unittest.TestCase):
    def test_iter_tag_paths_supports_manual_and_imported_nodes(self) -> None:
        tree = {
            "Animals": {
                "__tags__": ["cat"],
                "Dog": {"__tag__": "dog"},
                "legacy_empty_leaf": {},
            }
        }
        self.assertEqual(
            set(iter_tag_paths(tree)),
            {
                ("cat", ("Animals",)),
                ("dog", ("Animals", "Dog")),
                ("legacy_empty_leaf", ("Animals", "legacy_empty_leaf")),
            },
        )

    def test_save_creates_a_dated_backup_and_synchronizes_databases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / "taxonomy.json"
            repository = TaxonomyRepository(path, root)
            document = {"version": 1, "boards": {"gelbooru": {}, "e621": {}}, "metadata": {}, "sources": [], "excluded_imported_tags": {}}
            self.assertIsNone(repository.save(document))
            backup = repository.save(document)
            self.assertIsNotNone(backup); self.assertTrue(backup.is_file())
            self.assertTrue((root / "tag_organization_gelbooru.sqlite").is_file())
