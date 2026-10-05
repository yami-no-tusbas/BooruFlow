from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from booruflow.application import analysis_installation
from booruflow.application.analysis_installation import (
    analysis_installation_status,
    runtime_capabilities,
)
from booruflow.infrastructure.grabber import (
    grabber_availability,
    resolve_grabber_executable,
)


def test_grabber_detection_prefers_and_validates_exact_configured_executable(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "custom-grabber.exe"
    executable.touch()

    with patch(
        "booruflow.infrastructure.grabber.availability.which",
        return_value="C:/PATH/Grabber.exe",
    ):
        assert resolve_grabber_executable(executable) == executable
        assert grabber_availability(executable).available
        assert not grabber_availability(tmp_path / "missing.exe").available


def test_grabber_detection_can_use_path_when_no_setting_is_saved() -> None:
    with patch(
        "booruflow.infrastructure.grabber.availability.which",
        return_value="C:/Tools/Grabber.exe",
    ):
        assert resolve_grabber_executable() == Path("C:/Tools/Grabber.exe")


def test_analysis_installation_requires_complete_wd14_artifact_set(tmp_path: Path) -> None:
    directory = tmp_path / "models" / "wd14"
    settings = {"image_analysis_wd14_model_directory": str(directory)}
    directory.mkdir(parents=True)
    (directory / "model.onnx").touch()

    with patch(
        "booruflow.application.analysis_installation.runtime_capabilities",
        return_value=(True, True, "test"),
    ):
        incomplete = analysis_installation_status(tmp_path, settings)
        (directory / "selected_tags.csv").touch()
        missing_metadata = analysis_installation_status(tmp_path, settings)
        (directory / "metadata.json").touch()
        complete = analysis_installation_status(tmp_path, settings)

    assert incomplete.gpu_runtime_installed
    assert not incomplete.wd14_installed
    assert not missing_metadata.wd14_installed
    assert complete.wd14_installed


def test_runtime_capabilities_preloads_nvidia_dlls_before_cuda_provider(
    tmp_path: Path,
) -> None:
    package = tmp_path / "onnxruntime"
    capi = package / "capi"
    capi.mkdir(parents=True)
    provider = capi / "onnxruntime_providers_cuda.dll"
    provider.touch()
    runtime = SimpleNamespace(
        __file__=str(package / "__init__.py"),
        __version__="1.29.0",
        get_available_providers=lambda: [
            "CUDAExecutionProvider", "CPUExecutionProvider",
        ],
    )
    events: list[str] = []
    dll_directory = SimpleNamespace(close=lambda: None)

    with (
        patch.object(
            analysis_installation.importlib,
            "import_module",
            return_value=runtime,
        ),
        patch.object(
            analysis_installation,
            "preload_nvidia_dlls",
            side_effect=lambda _runtime: (
                events.append("preload") or (True, True, "loaded")
            ),
        ),
        patch.object(
            analysis_installation.ctypes,
            "WinDLL",
            side_effect=lambda _path: events.append("provider") or object(),
        ),
        patch.object(
            analysis_installation.os,
            "add_dll_directory",
            return_value=dll_directory,
        ),
    ):
        assert runtime_capabilities() == (True, True, "1.29.0")

    assert events == ["preload", "provider"]


def test_runtime_capabilities_rejects_cuda_when_preload_fails(tmp_path: Path) -> None:
    package = tmp_path / "onnxruntime"
    package.mkdir()
    runtime = SimpleNamespace(
        __file__=str(package / "__init__.py"),
        __version__="1.29.0",
        get_available_providers=lambda: [
            "CUDAExecutionProvider", "CPUExecutionProvider",
        ],
    )

    with (
        patch.object(
            analysis_installation.importlib,
            "import_module",
            return_value=runtime,
        ),
        patch.object(
            analysis_installation,
            "preload_nvidia_dlls",
            return_value=(True, False, "failed"),
        ),
        patch.object(analysis_installation.ctypes, "WinDLL"),
    ):
        assert runtime_capabilities() == (True, False, "1.29.0")
