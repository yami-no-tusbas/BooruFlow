"""PySide6 application bootstrap."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from booruflow.application import resolve_capabilities
from booruflow.infrastructure.grabber import GrabberInstallation
from booruflow.presentation.pyside6.main_window import MainWindow


def project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def configured_grabber(root: Path) -> Path | None:
    settings = root / "config" / "artist_by_tag_gui_settings.json"
    try:
        data = json.loads(settings.read_text(encoding="utf-8-sig"))
        value = str(data.get("grabber", "")).strip()
        return Path(value) if value else None
    except (OSError, ValueError, TypeError):
        return None


def create_application(argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    QCoreApplication.setOrganizationName("BooruFlow")
    QCoreApplication.setApplicationName("BooruFlow")
    root = project_root()
    capabilities = resolve_capabilities(GrabberInstallation(configured_grabber(root)))
    window = MainWindow(capabilities)
    return app, window


def run(argv: list[str] | None = None) -> int:
    app, window = create_application(argv)
    window.show()
    return app.exec()
