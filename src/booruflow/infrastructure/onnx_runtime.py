"""Shared ONNX Runtime provider selection, DLL preload and diagnostics."""

from __future__ import annotations

import importlib
import os
import subprocess
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class OnnxRuntimeDiagnostic:
    installed: bool
    runtime_version: str = ""
    expected_cuda: str = ""
    expected_cudnn: str = ""
    announced_providers: tuple[str, ...] = ()
    active_providers: tuple[str, ...] = ()
    preload_available: bool = False
    preload_succeeded: bool = False
    cuda_runtime_installed: bool = False
    cudnn_installed: bool = False
    message: str = ""
    gpu_devices: tuple[str, ...] = ()

    @property
    def effective_provider(self) -> str:
        return self.active_providers[0] if self.active_providers else ""


def expected_nvidia_versions(runtime_version: str) -> tuple[str, str]:
    try:
        major, minor = (int(value) for value in runtime_version.split(".")[:2])
    except (TypeError, ValueError):
        return "unknown", "unknown"
    if major == 1 and minor >= 27:
        return "13.0", "9.x"
    if major == 1 and minor >= 21:
        return "12.8", "9.x"
    if major == 1 and minor >= 19:
        return "12.x", "9.x"
    return "see ONNX Runtime compatibility table", "see compatibility table"


def _package_installed(*names: str) -> bool:
    for name in names:
        try:
            version(name)
            return True
        except PackageNotFoundError:
            pass
    return False


def import_onnxruntime():
    return importlib.import_module("onnxruntime")


def detect_nvidia_gpus() -> tuple[str, ...]:
    try:
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5, check=False,
            startupinfo=startupinfo, creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def preload_nvidia_dlls(ort: Any) -> tuple[bool, bool, str]:
    preload = getattr(ort, "preload_dlls", None)
    if not callable(preload):
        return False, False, "preload_dlls is unavailable in this ONNX Runtime version"
    try:
        preload(cuda=True, cudnn=True, msvc=True, directory="")
        return True, True, "NVIDIA DLL preload completed from Python site-packages"
    except Exception as exc:  # noqa: BLE001 - provider-specific runtime boundary
        return True, False, f"NVIDIA DLL preload failed: {exc}"


def create_inference_session(model: Path, provider_preference: tuple[str, ...],trace=None):
    """Create a session, preload isolated DLLs, and report the provider actually active."""
    trace=trace or (lambda _message:None);trace("ONNX import begin")
    ort = import_onnxruntime()
    trace("ONNX import complete")
    runtime_version = str(getattr(ort, "__version__", "unknown"))
    expected_cuda, expected_cudnn = expected_nvidia_versions(runtime_version)
    announced = tuple(ort.get_available_providers())
    trace(f"ONNX providers announced={announced!r}")
    providers = [value for value in provider_preference if value in announced]
    if not providers:
        raise RuntimeError("ONNX Runtime has no requested execution provider")
    trace("NVIDIA DLL preload begin");preload_available, preload_succeeded, preload_message = preload_nvidia_dlls(ort);trace(f"NVIDIA DLL preload complete succeeded={preload_succeeded}")
    cuda_error = ""
    try:
        trace(f"InferenceSession begin providers={providers!r}")
        session = ort.InferenceSession(str(model), providers=providers)
        trace("InferenceSession complete")
    except Exception as exc:
        if "CUDAExecutionProvider" not in providers or "CPUExecutionProvider" not in announced:
            raise
        cuda_error = str(exc)
        trace(f"CUDA session failed={exc}; CPU InferenceSession begin")
        session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
        trace("CPU InferenceSession complete")
    active = tuple(session.get_providers())
    cuda_runtime = _package_installed("nvidia-cuda-runtime", "nvidia-cuda-runtime-cu13")
    cudnn = _package_installed("nvidia-cudnn-cu13", "nvidia-cudnn-cu12")
    message = preload_message
    if cuda_error:
        message += f"; CUDA session initialization failed and CPU fallback succeeded: {cuda_error}"
    if "CUDAExecutionProvider" in announced and "CUDAExecutionProvider" not in active:
        message += "; CUDA was announced but session creation fell back to CPU"
    diagnostic = OnnxRuntimeDiagnostic(
        True, runtime_version, expected_cuda, expected_cudnn, announced, active,
        preload_available, preload_succeeded, cuda_runtime, cudnn, message,
        detect_nvidia_gpus(),
    )
    return session, diagnostic


def diagnose_runtime(model: Path, provider_preference: tuple[str, ...]) -> OnnxRuntimeDiagnostic:
    try:
        session, diagnostic = create_inference_session(model, provider_preference)
        del session
        return diagnostic
    except ImportError:
        return OnnxRuntimeDiagnostic(False, message="ONNX Runtime is not installed")
    except Exception as exc:  # noqa: BLE001 - diagnostic must survive runtime failures
        try:
            ort = import_onnxruntime()
            runtime_version = str(getattr(ort, "__version__", "unknown"))
            announced = tuple(ort.get_available_providers())
            expected_cuda, expected_cudnn = expected_nvidia_versions(runtime_version)
        except ImportError:
            return OnnxRuntimeDiagnostic(False, message="ONNX Runtime is not installed")
        return OnnxRuntimeDiagnostic(
            True, runtime_version, expected_cuda, expected_cudnn, announced,
            cuda_runtime_installed=_package_installed(
                "nvidia-cuda-runtime", "nvidia-cuda-runtime-cu13"
            ),
            cudnn_installed=_package_installed("nvidia-cudnn-cu13", "nvidia-cudnn-cu12"),
            message=str(exc),
            gpu_devices=detect_nvidia_gpus(),
        )
