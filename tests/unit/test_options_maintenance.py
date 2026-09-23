from pathlib import Path
from unittest.mock import patch

from booruflow.application.analysis_installation import analysis_installation_status
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
