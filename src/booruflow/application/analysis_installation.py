"""Human-facing installation state for optional image-analysis components."""

from __future__ import annotations

import ctypes
import importlib
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AnalysisInstallationStatus:
    gpu_runtime_installed: bool
    cpu_runtime_available: bool
    cuda_runtime_available: bool
    runtime_version: str
    wd14_installed: bool
    wd14_directory: Path


def wd14_directory(root: Path, settings: dict[str, object]) -> Path:
    configured = str(settings.get("image_analysis_wd14_model_directory", "")).strip()
    return (
        Path(configured)
        if configured
        else root / "var" / "models" / "image_analysis" / "wd-vit-tagger-v3"
    )


def runtime_capabilities() -> tuple[bool, bool, str]:
    """Report providers from an actually importable ONNX Runtime."""
    try:
        ort = importlib.import_module("onnxruntime")
        providers = set(ort.get_available_providers())
        cuda_available = "CUDAExecutionProvider" in providers
        if cuda_available and os.name == "nt":
            capi = Path(str(getattr(ort, "__file__", ""))).parent / "capi"
            provider = capi / "onnxruntime_providers_cuda.dll"
            try:
                dll_dir = os.add_dll_directory(str(capi)) if capi.is_dir() else None
                try:
                    ctypes.WinDLL(str(provider))
                finally:
                    if dll_dir is not None:
                        dll_dir.close()
            except OSError:
                cuda_available = False
        return (
            "CPUExecutionProvider" in providers,
            cuda_available,
            str(getattr(ort, "__version__", "unknown")),
        )
    except (ImportError, OSError, AttributeError):
        return False, False, ""


def gpu_runtime_installed() -> bool:
    """Compatibility predicate: true only when CUDA is an announced provider."""
    return runtime_capabilities()[1]


def analysis_installation_status(
    root: Path, settings: dict[str, object]
) -> AnalysisInstallationStatus:
    directory = wd14_directory(root, settings)
    cpu_available, cuda_available, runtime_version = runtime_capabilities()
    return AnalysisInstallationStatus(
        gpu_runtime_installed=cuda_available,
        cpu_runtime_available=cpu_available,
        cuda_runtime_available=cuda_available,
        runtime_version=runtime_version,
        wd14_installed=all(
            (directory / name).is_file()
            for name in ("model.onnx", "selected_tags.csv", "metadata.json")
        ),
        wd14_directory=directory,
    )
