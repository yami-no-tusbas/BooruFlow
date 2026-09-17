import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy
from PIL import Image

from booruflow.infrastructure.wd14 import (
    WD14Backend,
    WD14Config,
    WD14UnavailableError,
    load_selected_tags,
    prepare_wd14_image,
)


class _Meta:
    def __init__(self, name, shape): self.name = name; self.shape = shape


class _Session:
    def __init__(self, _path, providers): self.providers = providers
    def get_providers(self): return self.providers
    def get_inputs(self): return [_Meta("input", [None, 448, 448, 3])]
    def get_outputs(self): return [_Meta("output", [None, 3])]
    def run(self, _outputs, values):
        assert values["input"].shape == (1, 448, 448, 3)
        return [numpy.asarray([[0.05, 0.75, 0.25]], dtype=numpy.float32)]


class WD14Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.root = Path(self.temporary.name)

    def tearDown(self): self.temporary.cleanup()

    def artifacts(self):
        (self.root / "model.onnx").write_bytes(b"model")
        (self.root / "selected_tags.csv").write_text(
            "tag_id,name,category,count\n0,safe,9,1\n1,blue hair,0,1\n2,miku,4,1\n",
            encoding="utf-8",
        )
        (self.root / "metadata.json").write_text(json.dumps({
            "model_id": "SmilingWolf/wd-vit-tagger-v3", "model_sha256": "a" * 64,
        }), encoding="utf-8")

    def test_preprocessing_is_nhwc_float32_bgr_with_white_alpha(self):
        image = Image.new("RGBA", (2, 1), (255, 0, 0, 255)); image.putpixel((1, 0), (0, 0, 0, 0))
        result = prepare_wd14_image(image, 2)
        self.assertEqual(result.shape, (1, 2, 2, 3)); self.assertEqual(result.dtype, numpy.float32)
        self.assertEqual(tuple(result[0, 0, 0]), (0, 0, 255))
        self.assertEqual(tuple(result[0, 0, 1]), (255, 255, 255))

    def test_categories_and_backend_threshold(self):
        self.artifacts()
        self.assertEqual(load_selected_tags(self.root / "selected_tags.csv")[1], ("blue hair", "general"))
        fake = types.SimpleNamespace(
            __version__="1.20", get_available_providers=lambda: [
                "CUDAExecutionProvider", "CPUExecutionProvider"
            ], InferenceSession=_Session,
        )
        with patch.dict(sys.modules, {"onnxruntime": fake}):
            backend = WD14Backend(WD14Config(self.root, store_threshold=0.10)); backend.prepare()
            image = self.root / "image.png"; Image.new("RGB", (4, 2), "red").save(image)
            result = backend.analyze(image)
        self.assertEqual(backend.device, "cuda")
        self.assertEqual([(tag.raw_name, tag.category) for tag in result.predictions], [
            ("blue hair", "general"), ("miku", "character")
        ])

    def test_missing_artifacts_are_explicit(self):
        with self.assertRaises(WD14UnavailableError):
            WD14Backend(WD14Config(self.root)).prepare()


if __name__ == "__main__": unittest.main()
