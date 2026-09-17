import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from booruflow.application.tagging import TaggingRequest
from booruflow.infrastructure.settings import JsonSettingsRepository

PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None
LANGUAGES = Path(__file__).resolve().parents[2] / "resources" / "i18n"


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 is not installed")
def test_standalone_tagging_saves_credentials_and_starts_without_main_shell(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from booruflow.infrastructure.localization import LanguageCatalog
    from booruflow.presentation.pyside6.tagging_standalone import StandaloneTaggingWindow

    application = QApplication.instance() or QApplication([])
    settings = JsonSettingsRepository(tmp_path / "settings.json")
    credentials = JsonSettingsRepository(tmp_path / "credentials.json")
    window = StandaloneTaggingWindow(LanguageCatalog(LANGUAGES, "en"), settings, credentials)
    window.user_id.setText("123")
    window.api_key.setText("secret")
    request = TaggingRequest("rating:general", 10, 1, 0, 12, 5, 8)
    with patch.object(window.controller, "start") as start:
        window.start(request)
    start.assert_called_once_with(request)
    assert credentials.load() == {"gelbooru": {"user_id": "123", "api_key": "secret"}}
    assert settings.load()["tagging_query"] == "rating:general"
    assert window.centralWidget().findChildren(type(window.page)) == [window.page]
    application.processEvents()
    window.close()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 is not installed")
def test_standalone_tagging_refuses_missing_credentials(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from booruflow.infrastructure.localization import LanguageCatalog
    from booruflow.presentation.pyside6.tagging_standalone import StandaloneTaggingWindow

    application = QApplication.instance() or QApplication([])
    window = StandaloneTaggingWindow(
        LanguageCatalog(LANGUAGES, "en"),
        JsonSettingsRepository(tmp_path / "settings.json"),
        JsonSettingsRepository(tmp_path / "credentials.json"),
    )
    request = TaggingRequest("rating:general", 10, 1, 0, 12, 5, 8)
    with patch.object(window.controller, "start") as start:
        window.start(request)
    start.assert_not_called()
    assert "user id" in window.page.state.text().casefold()
    application.processEvents()
    window.close()


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 is not installed")
def test_frozen_runtime_keeps_config_next_to_executable(tmp_path: Path) -> None:
    import sys

    from booruflow.presentation.pyside6.tagging_standalone import runtime_paths

    executable = tmp_path / "portable" / "Gelbooru-Tagging-Helper.exe"
    bundle = tmp_path / "portable" / "_internal"
    with (
        patch.object(sys, "frozen", True, create=True),
        patch.object(sys, "executable", str(executable)),
        patch.object(sys, "_MEIPASS", str(bundle), create=True),
    ):
        assert runtime_paths() == (executable.parent, bundle)


@pytest.mark.skipif(not PYSIDE6_AVAILABLE, reason="PySide6 is not installed")
def test_packaged_application_defaults_to_english(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from booruflow.presentation.pyside6 import tagging_standalone

    application = QApplication.instance() or QApplication([])
    with patch.object(tagging_standalone, "runtime_paths", return_value=(tmp_path, Path.cwd())):
        _application, window = tagging_standalone.create_application([])
    assert window.windowTitle() == "Gelbooru Tagging Helper"
    assert window.credentials_group.title() == "Gelbooru access"
    assert "no automatic tag submission" in window.status.text().casefold()
    application.processEvents()
    window.close()
