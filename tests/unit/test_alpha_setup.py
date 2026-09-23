import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from booruflow.application.credential_paste import parse_gelbooru_credentials
from booruflow.application.database_snapshots import incremental_tags, install_snapshot
from booruflow.application.download_progress import DownloadProgress, parse_download_line
from booruflow.infrastructure.gelbooru_aliases import ALIAS_SCHEMA_VERSION, ensure_alias_schema
from booruflow.infrastructure.gelbooru_tag_importer import IMPORT_VERSION


def test_credential_paste_accepts_fragment_and_url_without_exposing_value():
    expected = {"user_id": "12345", "api_key": "secret-value"}
    assert parse_gelbooru_credentials("&api_key=secret-value&user_id=12345") == expected
    assert parse_gelbooru_credentials(
        "https://gelbooru.com/index.php?page=account&s=options&user_id=12345&api_key=secret-value"
    ) == expected
    for value in ("api_key=secret-value", "&api_key=x&user_id=1&user_id=2"):
        with pytest.raises(ValueError, match="Invalid Gelbooru credential parameters") as caught:
            parse_gelbooru_credentials(value)
        assert "secret-value" not in str(caught.value)


def test_download_progress_omits_unknown_eta_and_smooths_known_rate():
    ticks = iter((0.0, 2.0, 4.0))
    progress = DownloadProgress(clock=lambda: next(ticks))
    assert parse_download_line("DOWNLOAD model.onnx 1048576 0") == ("model.onnx", 1048576, 0)
    assert "ETA" not in progress.update("model.onnx", 1048576, 0)
    known = progress.update("model.onnx", 2097152, 4194304)
    assert "50.0%" in known and "ETA" in known


def _manifest(tmp_path: Path, *, valid_hash=True) -> str:
    database = tmp_path / "tags.db"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT, post_count INTEGER,
                               category INTEGER, ambiguous INTEGER);
            CREATE TABLE import_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO tags VALUES (42,'example',1,0,0);
        """)
        connection.execute("INSERT INTO import_state VALUES('import_version',?)", (IMPORT_VERSION,))
    archive = tmp_path / "gelbooru-tags.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(database, "tags.db")
    manifest = {"schema_version": 1, "databases": {"tags": {
        "filename": archive.name, "size": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest() if valid_hash else "0" * 64,
        "generated_at": "2026-09-21T00:00:00Z", "database_schema_version": IMPORT_VERSION,
        "last_tag_id": 42,
    }}}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path.as_uri()


def test_local_snapshot_verifies_and_activates(tmp_path: Path):
    url = _manifest(tmp_path)
    destination = tmp_path / "installed.db"
    events = []
    install_snapshot(url, "tags", destination, lambda *args: events.append(args))
    assert events and events[-1][1] == events[-1][2]
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT name FROM tags").fetchone() == ("example",)


def test_bad_snapshot_hash_preserves_old_db(tmp_path: Path):
    url = _manifest(tmp_path, valid_hash=False)
    destination = tmp_path / "installed.db"
    destination.write_bytes(b"existing database placeholder")
    with pytest.raises(ValueError, match="SHA256"):
        install_snapshot(url, "tags", destination)
    assert destination.read_bytes() == b"existing database placeholder"
    assert not list(tmp_path.glob(".booruflow-snapshot-*"))


def test_snapshot_schema_mismatch_preserves_old_db(tmp_path: Path):
    url = _manifest(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["databases"]["tags"]["last_tag_id"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    destination = tmp_path / "installed.db"
    destination.write_bytes(b"untouched")
    with pytest.raises(ValueError, match="checkpoint mismatch"):
        install_snapshot(url, "tags", destination)
    assert destination.read_bytes() == b"untouched"


def test_snapshot_incremental_resumes_at_manifest_checkpoint(tmp_path: Path):
    destination = tmp_path / "installed.db"
    install_snapshot(_manifest(tmp_path), "tags", destination)
    cursors = []

    def fetcher(after_id, _user_id, _api_key):
        cursors.append(after_id)
        return [{"id": 43, "name": "next", "count": 2}] if after_id == 42 else []

    assert incremental_tags(destination, 42, "123", "safe", fetcher=fetcher) == 43
    assert cursors == [42, 43]


def test_local_alias_snapshot_requires_checkpoint(tmp_path: Path):
    database = tmp_path / "aliases.db"
    ensure_alias_schema(database)
    archive = tmp_path / "gelbooru-aliases.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(database, "aliases.db")
    manifest = {"schema_version": 1, "databases": {"aliases": {
        "filename": archive.name, "size": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "generated_at": "2026-09-21T00:00:00Z",
        "database_schema_version": ALIAS_SCHEMA_VERSION,
    }}}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="checkpoint"):
        install_snapshot(manifest_path.as_uri(), "aliases", tmp_path / "installed-aliases.db")
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO alias_sync_state VALUES('checkpoint',?)", (
            json.dumps([["a", "b", "active"], ["c", "d", "active"]]),
        ))
    with zipfile.ZipFile(archive, "w") as output:
        output.write(database, "aliases.db")
    manifest["databases"]["aliases"]["size"] = archive.stat().st_size
    manifest["databases"]["aliases"]["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    install_snapshot(manifest_path.as_uri(), "aliases", tmp_path / "installed-aliases.db")


def test_wizard_and_options_footer(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from booruflow.infrastructure.localization import LanguageCatalog
    from booruflow.presentation.pyside6.feature_loading import FeaturePageHost
    from booruflow.presentation.pyside6.first_run_wizard import FirstRunWizard
    from booruflow.presentation.pyside6.options_page import OptionsPage

    _app = QApplication.instance() or QApplication([])
    catalog = LanguageCatalog(Path(__file__).resolve().parents[2] / "resources" / "i18n", "en")
    wizard = FirstRunWizard(catalog, tmp_path, {}, {})
    wizard.paste.setText("&api_key=test-key&user_id=123")
    wizard.parse_paste()
    assert wizard.api_key.text() == "test-key" and wizard.paste.text() == ""
    assert wizard.page(4).title() == "Setup summary"
    completed = []
    wizard.completed.connect(lambda *args: completed.append(args))
    wizard.accept()
    assert completed and completed[0][0] == "en"
    page = OptionsPage(catalog, {}, {}, tmp_path)
    host = FeaturePageHost(None, catalog, "nav.options")
    host.set_page(page)
    assert page.save_button.parent() is page.persistent_footer
    assert page.persistent_footer.parent() is host.content
    host.resize(900, 600)
    host.show()
    _app.processEvents()
    assert page.save_button.isVisibleTo(host)
    host.close()
    wizard.close()


def test_first_run_shows_gelbooru_wd14_without_hydra(tmp_path: Path):
    from PySide6.QtWidgets import QApplication, QPushButton

    from booruflow.infrastructure.localization import LanguageCatalog
    from booruflow.presentation.pyside6.first_run_wizard import FirstRunWizard

    app = QApplication.instance() or QApplication([])
    for language, label in (
        ("en", "WD14 — Gelbooru tagging helper"),
        ("fr", "WD14 — Assistant de tagging Gelbooru"),
    ):
        catalog = LanguageCatalog(Path(__file__).resolve().parents[2] / "resources" / "i18n", language)
        wizard = FirstRunWizard(catalog, tmp_path, {"language": language}, {})
        buttons = [button.text() for button in wizard.page(3).findChildren(QPushButton)]
        assert any(label in button for button in buttons)
        assert "Hydra" not in wizard.analysis_status()
        assert all("Hydra" not in button for button in buttons)
        wizard.close()
    assert app is not None


def test_wizard_close_is_configure_later_and_emits_completion(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from booruflow.infrastructure.localization import LanguageCatalog
    from booruflow.presentation.pyside6.first_run_wizard import FirstRunWizard

    _app = QApplication.instance() or QApplication([])
    catalog = LanguageCatalog(Path(__file__).resolve().parents[2] / "resources" / "i18n", "en")
    wizard = FirstRunWizard(catalog, tmp_path, {"first_run_wizard_completed": False}, {})
    completed = []
    wizard.completed.connect(lambda *args: completed.append(args))
    wizard.reject()
    assert completed == [("en", {})]
    wizard.close()


def test_wizard_pages_follow_application_palette(tmp_path: Path):
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication

    from booruflow.infrastructure.localization import LanguageCatalog
    from booruflow.presentation.pyside6.first_run_wizard import FirstRunWizard

    app = QApplication.instance() or QApplication([])
    catalog = LanguageCatalog(Path(__file__).resolve().parents[2] / "resources" / "i18n", "en")
    original = app.palette()
    palette = QPalette(original)
    palette.setColor(QPalette.ColorRole.Window, QColor("#202124"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f1f3f4"))
    app.setPalette(palette)
    wizard = FirstRunWizard(catalog, tmp_path, {}, {})
    assert wizard.page(0).palette().color(QPalette.ColorRole.Window).name() == "#202124"
    app.setPalette(original)
    wizard.close()


def test_wizard_activity_updates_progress_summary_and_actions(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from booruflow.infrastructure.localization import LanguageCatalog
    from booruflow.presentation.pyside6.first_run_wizard import FirstRunWizard

    _app = QApplication.instance() or QApplication([])
    catalog = LanguageCatalog(Path(__file__).resolve().parents[2] / "resources" / "i18n", "en")
    wizard = FirstRunWizard(catalog, tmp_path, {}, {})
    cancelled = []
    wizard.cancel_requested.connect(lambda: cancelled.append(True))
    wizard.set_activity("database", "running", "Update pages 12 | tags 1,200 | after_id 42", 42, 100, True)
    assert "Gelbooru database" in wizard.page(2).activity_operation.text()
    assert wizard.page(2).activity_progress.value() == 42
    assert not wizard.install_buttons["aliases"].isEnabled()
    assert not wizard.button(wizard.WizardButton.FinishButton).isEnabled()
    assert "Running" in wizard.page(4).label.text()
    wizard.page(2).activity_cancel.click()
    assert cancelled == [True]
    wizard.set_activity("database", "completed")
    assert wizard.install_buttons["aliases"].isEnabled()
    assert wizard.button(wizard.WizardButton.FinishButton).isEnabled()
    wizard.close()


def test_frozen_options_disables_gpu_installer_but_keeps_wd14(tmp_path: Path, monkeypatch):
    import sys

    from PySide6.QtWidgets import QApplication

    from booruflow.infrastructure.localization import LanguageCatalog
    from booruflow.presentation.pyside6.options_page import OptionsPage

    _app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    catalog = LanguageCatalog(Path(__file__).resolve().parents[2] / "resources" / "i18n", "en")
    page = OptionsPage(catalog, {}, {}, tmp_path)
    page.refresh_analysis_status()
    assert not page.gpu_runtime_install.isEnabled()
    assert "CPU" in page.gpu_runtime_install.toolTip()
    assert page.wd14_install.isEnabled()
    page.close()


def test_onedir_spec_excludes_binaries_from_exe():
    spec = (Path(__file__).resolve().parents[2] / "tools" / "booruflow.spec").read_text()
    exe_section = spec.split("exe = EXE(", 1)[1].split("coll = COLLECT(", 1)[0]
    assert "exclude_binaries=True" in exe_section
    assert "a.binaries" not in exe_section and "a.datas" not in exe_section
    assert '"qopensslbackend.dll"' in spec


def test_database_helper_redacts_credentials_in_error_lines(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from booruflow.infrastructure.localization import LanguageCatalog
    from booruflow.presentation.pyside6.database_update_controller import DatabaseUpdateController
    from booruflow.presentation.pyside6.options_page import OptionsPage

    _app = QApplication.instance() or QApplication([])
    catalog = LanguageCatalog(Path(__file__).resolve().parents[2] / "resources" / "i18n", "en")
    credentials = {"gelbooru": {"user_id": "123", "api_key": "super-secret-key"}}
    page = OptionsPage(catalog, {}, credentials, tmp_path)
    controller = DatabaseUpdateController(
        tmp_path, "python", catalog, page, None, lambda: credentials, lambda _line: None
    )
    assert controller._redact("request api_key=super-secret-key failed") == (
        "request api_key=[redacted] failed"
    )


def test_database_helper_start_failure_is_visible(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from booruflow.infrastructure.localization import LanguageCatalog
    from booruflow.presentation.pyside6.database_update_controller import DatabaseUpdateController
    from booruflow.presentation.pyside6.options_page import OptionsPage

    _app = QApplication.instance() or QApplication([])
    catalog = LanguageCatalog(Path(__file__).resolve().parents[2] / "resources" / "i18n", "en")
    page = OptionsPage(catalog, {}, {}, tmp_path)
    messages = []
    controller = DatabaseUpdateController(
        tmp_path, str(tmp_path / "missing-python.exe"), catalog, page, None,
        dict, messages.append,
    )
    controller.start("e621", str(tmp_path / "e621.db"))
    assert not controller.process.waitForStarted(2000)
    _app.processEvents()
    assert page.database_status.text()
    assert any("Database helper error" in message for message in messages)
    assert page._database_running_site == ""


def test_alias_helper_failure_updates_wizard_activity_with_message(tmp_path: Path):
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication

    from booruflow.infrastructure.localization import LanguageCatalog
    from booruflow.presentation.pyside6.database_update_controller import DatabaseUpdateController
    from booruflow.presentation.pyside6.options_page import OptionsPage

    _app = QApplication.instance() or QApplication([])
    catalog = LanguageCatalog(Path(__file__).resolve().parents[2] / "resources" / "i18n", "en")
    page = OptionsPage(catalog, {}, {}, tmp_path)
    controller = DatabaseUpdateController(tmp_path, "python", catalog, page, None, dict, lambda _: None)
    controller.site = "aliases:initial"
    controller._last_error = "ERROR: Invalid argument"
    activities = []
    controller.activity_changed.connect(lambda *values: activities.append(values))
    controller.finished(1, QProcess.ExitStatus.NormalExit)
    assert activities[-1][0:2] == ("aliases", "failed")
    assert "Invalid argument" in activities[-1][2]
    page.close()


def test_database_cancel_sends_cooperative_stop_before_fallback(tmp_path: Path):
    from unittest.mock import MagicMock

    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication

    from booruflow.infrastructure.localization import LanguageCatalog
    from booruflow.presentation.pyside6.database_update_controller import DatabaseUpdateController
    from booruflow.presentation.pyside6.options_page import OptionsPage

    _app = QApplication.instance() or QApplication([])
    catalog = LanguageCatalog(Path(__file__).resolve().parents[2] / "resources" / "i18n", "en")
    page = OptionsPage(catalog, {}, {}, tmp_path)
    status = []
    controller = DatabaseUpdateController(
        tmp_path, "python", catalog, page, None, dict, lambda _line: None,
        status=lambda message, level: status.append((message, level)),
    )
    process = MagicMock()
    process.state.return_value = QProcess.ProcessState.Running
    controller.process = process
    controller.site = "gelbooru"
    controller.stop()
    process.write.assert_called_once_with(b"STOP\n")
    process.terminate.assert_not_called()
    assert status[-1] == ("Stopping Gelbooru database rebuild...", "PROGRESS")
