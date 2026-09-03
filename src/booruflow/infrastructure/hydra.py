"""Lazy local adapter for Project RedRocket Hydra 3.5.

Hydra ships its Python package together with the model repository instead of as
a standalone wheel.  Imports therefore happen only inside the image-analysis
worker and only when an e621 item needs it.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from booruflow.domain.image_analysis import ModelIdentity

HYDRA_MODEL_ID = "RedRocket/Hydra"
HYDRA_VERSION = "3.5"


class HydraUnavailableError(RuntimeError):
    """The local Hydra checkout, dependencies, or weights cannot be used."""


@dataclass(frozen=True, slots=True)
class HydraConfig:
    source_directory: Path
    model_path: Path
    device: str = "auto"
    seqlen: int = 256
    calibration: str = "f1.0@0.1"
    implications: str = "inherit"

    @property
    def configuration_hash(self) -> str:
        value = "|".join((str(self.model_path), self.device, str(self.seqlen), self.calibration, self.implications))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class HydraTag:
    raw_name: str
    category: str
    score: float


@dataclass(frozen=True, slots=True)
class HydraResult:
    predictions: tuple[HydraTag, ...]


class HydraBackend:
    """Direct Python Hydra inference, deliberately loaded only on first e621 use."""

    supported_sites = frozenset({"e621"})

    def __init__(self, config: HydraConfig) -> None:
        self.config = config
        self._model = None
        self._calibration = None
        self._image = None
        self._torch = None
        self.device = ""
        self.runtime = ""

    @property
    def identity(self) -> ModelIdentity:
        return ModelIdentity(
            "hydra", HYDRA_MODEL_ID, HYDRA_VERSION, self.config.configuration_hash, self.device
        )

    def prepare(self) -> None:
        if self._model is not None:
            return
        if not self.config.source_directory.is_dir():
            raise HydraUnavailableError(f"Hydra source is unavailable: {self.config.source_directory}")
        if not self.config.model_path.is_file() or self.config.model_path.stat().st_size < 1_000_000:
            raise HydraUnavailableError(f"Hydra 3.5 weights are unavailable: {self.config.model_path}")
        source = str(self.config.source_directory)
        if source not in sys.path:
            sys.path.insert(0, source)
        try:
            import torch
            from hydra import image
            from hydra.model import load_model
        except ImportError as exc:
            raise HydraUnavailableError(f"Hydra dependencies are unavailable: {exc}") from exc
        selected_device = self.config.device
        if selected_device == "auto":
            selected_device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            model = load_model(
                str(self.config.model_path), legacy_metadata_dir=str(self.config.source_directory / "data")
            )
            model.to(selected_device)
            model.eval()
            self._calibration = model.calibrate(self.config.calibration)
        except Exception as exc:  # Hydra exposes several implementation-specific errors.
            raise HydraUnavailableError(f"Hydra 3.5 could not load on {selected_device}: {exc}") from exc
        self._model, self._image, self._torch = model, image, torch
        self.device = selected_device
        self.runtime = f"torch {torch.__version__}"

    def analyze(self, path: Path) -> HydraResult:
        self.prepare()
        assert self._model is not None and self._image is not None and self._torch is not None
        try:
            tensor = __import__("hydra.model", fromlist=["load_image"]).load_image(
                str(path), self._model.image_config, self.config.seqlen
            )
            patches, sizes = self._image.stack([tensor], 16, self.config.seqlen, device=self.device)
            with self._torch.inference_mode():
                output = self._model.forward(self._model.from_srgb(patches), sizes)[0].cpu()
            selected = self._calibration.classify_output(
                output, implications=self.config.implications, sort=False
            )
            categories = {label.label: label.category for label in self._model.labels}
            return HydraResult(tuple(
                HydraTag(name, categories.get(name, "general"), float(score))
                for name, score in selected.items()
            ))
        except HydraUnavailableError:
            raise
        except Exception as exc:
            raise HydraUnavailableError(f"Hydra 3.5 inference failed: {exc}") from exc

    def close(self) -> None:
        self._model = self._calibration = self._image = self._torch = None
