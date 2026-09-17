import tempfile
import unittest
from pathlib import Path

from PIL import Image

from booruflow.infrastructure.classic_image_analysis import (
    ClassicAnalysisConfig,
    ClassicImageAnalyzer,
)


class ClassicImageAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.analyzer = ClassicImageAnalyzer()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def solid(self, name: str, color) -> Path:
        path = self.root / name
        Image.new("RGB", (32, 32), color).save(path)
        return path

    def test_black_white_gray_and_red_metrics(self) -> None:
        black = self.analyzer.analyze(self.solid("black.png", "black"))
        white = self.analyzer.analyze(self.solid("white.png", "white"))
        gray = self.analyzer.analyze(self.solid("gray.png", (128, 128, 128)))
        red = self.analyzer.analyze(self.solid("red.png", "red"))
        self.assertAlmostEqual(black.mean_luminance, 0.0)
        self.assertAlmostEqual(white.mean_luminance, 1.0)
        self.assertAlmostEqual(gray.mean_saturation, 0.0)
        self.assertAlmostEqual(red.mean_saturation, 1.0)
        self.assertEqual(red.dominant_colors, ("#ff0000",))

    def test_high_contrast_and_pastel_score_are_explainable(self) -> None:
        path = self.root / "contrast.png"
        image = Image.new("RGB", (20, 10), "black")
        for x in range(10, 20):
            for y in range(10): image.putpixel((x, y), (255, 255, 255))
        image.save(path)
        contrast = self.analyzer.analyze(path)
        pastel = self.analyzer.analyze(self.solid("pastel.png", (220, 200, 220)))
        saturated = self.analyzer.analyze(self.solid("saturated.png", (255, 0, 0)))
        self.assertAlmostEqual(contrast.contrast, 1.0)
        self.assertGreater(pastel.pastel_score, saturated.pastel_score)

    def test_palette_and_configuration_are_deterministic(self) -> None:
        path = self.solid("stable.png", (12, 34, 56))
        self.assertEqual(self.analyzer.analyze(path), self.analyzer.analyze(path))
        self.assertEqual(
            ClassicAnalysisConfig().configuration_hash,
            ClassicAnalysisConfig().configuration_hash,
        )
        self.assertNotEqual(
            ClassicAnalysisConfig().configuration_hash,
            ClassicAnalysisConfig(palette_size=5).configuration_hash,
        )


if __name__ == "__main__":
    unittest.main()
