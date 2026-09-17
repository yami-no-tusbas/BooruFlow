"""Asynchronous site-scoped credential validation orchestration."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal

from booruflow.application.credential_validation import (
    CredentialValidationResult,
    validate_site_credentials,
)
from booruflow.presentation.pyside6.ui_logging import log_event


class CredentialValidationWorker(QThread):
    completed = Signal(object)

    def __init__(self, site: str, credentials: dict[str, str], validator: Callable) -> None:
        super().__init__()
        self.site = site
        self.credentials = credentials
        self.validator = validator

    def run(self) -> None:
        self.completed.emit(self.validator(self.site, self.credentials))


class CredentialValidationController(QObject):
    def __init__(
        self,
        page,
        *,
        validator: Callable = validate_site_credentials,
        log: Callable[[str], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.page = page
        self.validator = validator
        self.log = log or (lambda _message: None)
        self._workers: set[CredentialValidationWorker] = set()

    def start(self, site: str, credentials: dict[str, str]) -> None:
        self.page.set_credential_test_running(site)
        worker = CredentialValidationWorker(site, credentials, self.validator)
        self._workers.add(worker)
        worker.completed.connect(self._completed)
        worker.finished.connect(lambda worker=worker: self._workers.discard(worker))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _completed(self, result: CredentialValidationResult) -> None:
        self.page.show_credential_test_result(result.site, result.status.value)
        warning = result.status.value not in {"valid", "not_tested", "testing"}
        logged_status = (
            "invalid_credentials" if result.status.value == "invalid" else result.status.value
        )
        self.log(log_event(
            "Credentials",
            f"{result.site} validation result={logged_status}",
            level="WARNING" if warning else "INFO",
        ))
