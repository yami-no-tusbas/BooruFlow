import tempfile
import unittest
from pathlib import Path

from booruflow.application import hydra_model_manager as manager
from booruflow.application.model_inventory import inventory_models, model_totals


class ModelInventoryTests(unittest.TestCase):
    def test_known_weights_and_lfs_storage_are_reported_separately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "var/models/image_analysis"
            files = {
                base / "wd-vit-tagger-v3/model.onnx": 11,
                base / "hydra-3.5-src/models/hydra-3.5.safetensors": 13,
                base / "hydra-3.5-src/models/jtp-3-hydra.safetensors": 17,
                base / "hydra-3.5-src/.git/lfs/objects/aa/object": 13,
            }
            for path, size in files.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x" * size)
            entries = inventory_models(root)
            by_key = {entry.key: entry for entry in entries}
            self.assertEqual(by_key["hydra-lfs"].size, 13)
            self.assertFalse(by_key["hydra-3"].required)
            totals = model_totals(entries)
            self.assertEqual(totals["wd14"], 11)
            self.assertEqual(totals["e621"], 13)
            self.assertEqual(totals["total"], sum(entry.size for entry in entries))

    def test_clean_hydra_is_separate_from_legacy_overhead(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clean = manager.hydra_directory(root)
            legacy = manager.legacy_hydra_directory(root)
            clean.mkdir(parents=True)
            (clean / "hydra-3.5.safetensors").write_bytes(b"clean")
            (legacy / ".git/lfs/objects/aa").mkdir(parents=True)
            (legacy / ".git/lfs/objects/aa/copy").write_bytes(b"duplicate")
            entries = inventory_models(root)
            by_key = {entry.key: entry for entry in entries}
            self.assertEqual(by_key["hydra-3.5"].size, 5)
            self.assertEqual(by_key["hydra-lfs"].size, 9)


if __name__ == "__main__":
    unittest.main()
