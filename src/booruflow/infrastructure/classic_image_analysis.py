"""Deterministic, lightweight image statistics computed with Pillow."""

from __future__ import annotations

import colorsys
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from booruflow.domain.image_analysis import ColorStatistics, ModelIdentity

FORMULA_VERSION = "classic-pillow-v1"
PASTEL_FORMULA = "clamp(0.45*(1-S)+0.35*L+0.20*(1-C),0,1)"


@dataclass(frozen=True, slots=True)
class ClassicAnalysisConfig:
    sample_size: int = 256
    palette_size: int = 6

    def __post_init__(self) -> None:
        if self.sample_size < 16 or not 1 <= self.palette_size <= 8:
            raise ValueError("invalid classic analysis configuration")

    @property
    def configuration_hash(self) -> str:
        value = json.dumps(
            {
                "formula": PASTEL_FORMULA,
                "palette_size": self.palette_size,
                "sample_size": self.sample_size,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ClassicImageAnalyzer:
    backend = "classic"
    name = "pillow-color-statistics"
    version = FORMULA_VERSION

    def __init__(self, config: ClassicAnalysisConfig | None = None) -> None:
        config = config or ClassicAnalysisConfig()
        self.config = config
        self.identity = ModelIdentity(
            self.backend, self.name, self.version, config.configuration_hash, "cpu"
        )

    def analyze(self, path: Path) -> ColorStatistics:
        with Image.open(path) as source:
            source.seek(0)
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((self.config.sample_size, self.config.sample_size))
            raw = image.tobytes()
            pixels = list(zip(raw[0::3], raw[1::3], raw[2::3], strict=True))
        if not pixels:
            raise ValueError("image contains no pixels")
        saturations: list[float] = []
        luminances: list[float] = []
        for red, green, blue in pixels:
            r, g, b = red / 255.0, green / 255.0, blue / 255.0
            saturations.append(colorsys.rgb_to_hsv(r, g, b)[1])
            luminances.append(0.2126 * r + 0.7152 * g + 0.0722 * b)
        mean_saturation = sum(saturations) / len(saturations)
        mean_luminance = sum(luminances) / len(luminances)
        variance = sum((value - mean_luminance) ** 2 for value in luminances) / len(luminances)
        ordered = sorted(luminances)
        low = ordered[int((len(ordered) - 1) * 0.05)]
        high = ordered[int((len(ordered) - 1) * 0.95)]
        contrast = high - low
        pastel_score = max(
            0.0,
            min(1.0, 0.45 * (1.0 - mean_saturation) + 0.35 * mean_luminance
                + 0.20 * (1.0 - contrast)),
        )
        return ColorStatistics(
            self._palette(image), mean_saturation, mean_luminance,
            math.sqrt(variance), contrast, pastel_score,
        )

    def _palette(self, image: Image.Image) -> tuple[str, ...]:
        quantized = image.quantize(
            colors=self.config.palette_size,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.NONE,
        ).convert("RGB")
        counts = quantized.getcolors(maxcolors=image.width * image.height) or []
        colors = sorted(
            ((count, f"#{r:02x}{g:02x}{b:02x}") for count, (r, g, b) in counts),
            key=lambda entry: (-entry[0], entry[1]),
        )
        return tuple(color for _count, color in colors[: self.config.palette_size])
