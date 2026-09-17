"""Manual real-model checks; never download or load large models in ordinary CI."""

import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from booruflow.infrastructure.embedding_backends import (
    AuthorIdEmbeddingBackend,
    OpenClipEmbeddingBackend,
)

RUN_MODELS = os.environ.get("BOORUFLOW_RUN_MODEL_TESTS") == "1"


@unittest.skipUnless(RUN_MODELS, "set BOORUFLOW_RUN_MODEL_TESTS=1 for real model checks")
class EmbeddingBackendIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.root = Path(self.temporary.name)
        self.image = self.root / "sample.png"; Image.new("RGBA", (640, 480), (20, 80, 160, 180)).save(self.image)

    def tearDown(self): self.temporary.cleanup()

    def test_author_id_real_model(self):
        source = Path(os.environ["BOORUFLOW_AUTHOR_ID_MODEL"])
        backend = AuthorIdEmbeddingBackend(source, self.root / "derived.onnx", "cuda")
        try:
            result = backend.encode(self.image)
            self.assertEqual(len(result.vector), 512)
            self.assertAlmostEqual(sum(value * value for value in result.vector), 1.0, places=4)
        finally: backend.close()

    def test_openclip_real_model(self):
        backend = OpenClipEmbeddingBackend(device="cuda")
        try:
            result = backend.encode(self.image)
            self.assertEqual(len(result.vector), 512)
            self.assertAlmostEqual(sum(value * value for value in result.vector), 1.0, places=4)
        finally: backend.close()


if __name__ == "__main__": unittest.main()
