import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy
from PIL import Image

from booruflow.cli.embedding_benchmark import gallery, import_manifest
from booruflow.experiments.embedding_benchmark import (
    AuthorIdEmbeddingBackend,
    ExperimentalStore,
    ExperimentIdentity,
    balanced_sample,
    evaluate,
    nearest,
    normalize,
    rank_artists,
)


class EmbeddingBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.root = Path(self.temporary.name)
        self.store = ExperimentalStore(self.root / "experiment.sqlite")
        self.identity = ExperimentIdentity("test", "model", "v1", "{}")

    def tearDown(self): self.store.close(); self.temporary.cleanup()

    def image(self, name, artist, tags, vector):
        path = self.root / name; Image.new("RGB", (8, 8), "white").save(path)
        image_id = self.store.add_image(path, artist, tuple(tags))
        self.store.save_vector(image_id, self.identity, numpy.asarray(vector, dtype=numpy.float32))
        return image_id

    def dataset(self):
        return [
            self.image("a1.png", "A", ("human", "sword"), (1, 0, 0)),
            self.image("a2.png", "A", ("anthro", "casual"), (.9, .1, 0)),
            self.image("b1.png", "B", ("human", "sword"), (.2, .8, 0)),
            self.image("b2.png", "B", ("landscape",), (0, 1, 0)),
        ]

    def test_vectors_are_normalized_cached_and_versioned(self):
        image_id = self.image("one.png", "A", (), (3, 4))
        vector = self.store.cached_vector(image_id, self.identity)
        self.assertAlmostEqual(float(numpy.linalg.norm(vector)), 1.0)
        self.assertIsNone(self.store.cached_vector(
            image_id, ExperimentIdentity("test", "model", "v2", "{}")
        ))
        with self.assertRaises(ValueError): normalize((0, 0))

    def test_metrics_separate_artist_cross_subject_and_leakage(self):
        self.dataset(); result = evaluate(self.store.vectors(self.identity))
        self.assertEqual(result["images"], 4); self.assertEqual(result["artists"], 2)
        self.assertEqual(result["recall@1"], 1.0)
        self.assertIsNotNone(result["cross_subject_mrr"])
        self.assertIsNotNone(result["content_leakage_rate"])
        self.assertIn("coherence", result["artist_statistics"]["A"])
        self.assertIn("distinctiveness", result["artist_statistics"]["A"])

    def test_neighbors_artist_rankings_and_gallery(self):
        ids = self.dataset(); records = self.store.vectors(self.identity)
        self.assertEqual(nearest(records, ids[0], 1)[0][1]["artist"], "A")
        methods = rank_artists(records, ids[0]); self.assertEqual(methods[0]["artist"], "A")
        self.assertIn("top_k_mean", methods[0]); self.assertIn("centroid", methods[0])
        output = self.root / "gallery.html"
        class Backend: identity = self.identity
        gallery(self.store, Backend(), ids[0], output, 3)
        self.assertIn("Embedding neighbors", output.read_text(encoding="utf-8"))

    def test_manifest_and_balanced_seed_are_deterministic(self):
        paths = []
        for index in range(4):
            path = self.root / f"m{index}.png"; Image.new("RGB", (2, 2)).save(path); paths.append(path)
        manifest = self.root / "manifest.csv"
        manifest.write_text("path,artist,tags,groups\n" + "\n".join(
            f"{path.name},{'A' if i < 3 else 'B'},tag{i},same_artist" for i, path in enumerate(paths)
        ), encoding="utf-8")
        self.assertEqual(import_manifest(self.store, manifest), 4)
        first = balanced_sample(self.store.rows(), 1, 42)
        second = balanced_sample(self.store.rows(), 1, 42)
        self.assertEqual([row["id"] for row in first], [row["id"] for row in second])
        self.assertEqual(len(first), 2)

    def test_human_judgments_are_user_supplied(self):
        ids = self.dataset(); self.store.judge(ids[0], ids[1], "style_only", "manual")
        row = self.store.connection.execute("SELECT * FROM judgments").fetchone()
        self.assertEqual((row["label"], row["note"]), ("style_only", "manual"))
        with self.assertRaises(ValueError): self.store.judge(ids[0], ids[2], "invented")

    @unittest.skipUnless(importlib.util.find_spec("onnx"), "optional ONNX package is absent")
    def test_author_id_derived_copy_exposes_existing_embedding_node(self):
        import onnx
        from onnx import TensorProto, helper
        source = self.root / "author.onnx"; derived = self.root / "derived.onnx"
        input_value = helper.make_tensor_value_info("image", TensorProto.FLOAT, [None, 512])
        final_value = helper.make_tensor_value_info("final", TensorProto.FLOAT, [None, 512])
        candidate = "/backbone/Div_output_0"
        graph = helper.make_graph([
            helper.make_node("Identity", ["image"], [candidate]),
            helper.make_node("Identity", [candidate], ["final"]),
        ], "test", [input_value], [final_value])
        onnx.save(helper.make_model(graph), source)
        backend = AuthorIdEmbeddingBackend(source, derived); backend._create_derived_model()
        model = onnx.load(derived)
        self.assertEqual([output.name for output in model.graph.output], [candidate])


if __name__ == "__main__": unittest.main()
