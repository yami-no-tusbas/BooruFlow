"""Human-facing installation state for optional image-analysis components."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AnalysisInstallationStatus:
    gpu_runtime_installed: bool
    wd14_installed: bool
    wd14_directory: Path


def wd14_directory(root: Path, settings: dict[str, object]) -> Path:
    configured = str(settings.get("image_analysis_wd14_model_directory", "")).strip()
    return (
        Path(configured)
        if configured
        else root / "var" / "models" / "image_analysis" / "wd-vit-tagger-v3"
    )


def gpu_runtime_installed() -> bool:
    try:
        metadata.distribution("onnxruntime-gpu")
    except metadata.PackageNotFoundError:
        return False
    return True


def analysis_installation_status(
    root: Path, settings: dict[str, object]
) -> AnalysisInstallationStatus:
    directory = wd14_directory(root, settings)
    return AnalysisInstallationStatus(
        gpu_runtime_installed=gpu_runtime_installed(),
        wd14_installed=(directory / "model.onnx").is_file()
        and (directory / "selected_tags.csv").is_file(),
        wd14_directory=directory,
    )
