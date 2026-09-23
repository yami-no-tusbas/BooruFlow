"""Small, optional setup guide; uses the existing settings and credential services."""

import sqlite3
from contextlib import closing
from pathlib import Path

from PySide6.QtCore import QEvent, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from booruflow.application.analysis_installation import analysis_installation_status
from booruflow.application.credential_paste import parse_gelbooru_credentials
from booruflow.application.database_paths import gelbooru_alias_database, gelbooru_tag_database
from booruflow.infrastructure.localization import LanguageCatalog


class StatusPage(QWizardPage):
    activity_requested = Signal()

    def __init__(self, title: str, refresh, catalog: LanguageCatalog, parent=None):
        super().__init__(parent)
        self.catalog = catalog
        self.setTitle(title)
        self.label = QLabel()
        self.label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        self.activity = QGroupBox(catalog.text("wizard.activity"))
        activity_layout = QVBoxLayout(self.activity)
        self.activity_operation = QLabel()
        self.activity_message = QLabel()
        self.activity_message.setWordWrap(True)
        self.activity_progress = QProgressBar()
        self.activity_progress.setTextVisible(True)
        self.activity_cancel = QPushButton(catalog.text("wizard.cancel"))
        self.activity_cancel.clicked.connect(self.activity_requested.emit)
        activity_layout.addWidget(self.activity_operation)
        activity_layout.addWidget(self.activity_message)
        activity_layout.addWidget(self.activity_progress)
        activity_layout.addWidget(self.activity_cancel)
        layout.addWidget(self.activity)
        self.refresh = refresh
        self.set_activity("", "idle", "", -1, -1, False)

    def initializePage(self):
        self.label.setText(self.refresh())

    def set_activity(
        self, operation: str, state: str, message: str, current: int, total: int,
        cancellable: bool,
    ) -> None:
        if not operation:
            self.activity.hide()
            return
        self.activity.show()
        operation_text = self.catalog.text(f"wizard.activity_{operation}")
        state_text = self.catalog.text(f"wizard.state_{state}")
        self.activity_operation.setText(f"{operation_text} — {state_text}")
        self.activity_message.setText(message)
        if current >= 0 and total > 0:
            self.activity_progress.setRange(0, total)
            self.activity_progress.setValue(min(current, total))
            self.activity_progress.show()
        else:
            self.activity_progress.hide()
        self.activity_cancel.setVisible(cancellable)


class FirstRunWizard(QWizard):
    credentials_test_requested = Signal(str, dict)
    install_requested = Signal(str)
    completed = Signal(str, dict)
    cancel_requested = Signal()

    def __init__(self, catalog: LanguageCatalog, root: Path, settings: dict,
                 credentials: dict, parent=None):
        super().__init__(parent)
        self.catalog, self.root, self.settings = catalog, root, settings
        self.existing_credentials = credentials
        self.setWindowTitle(catalog.text("wizard.title"))
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage)
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.setMinimumWidth(560)
        welcome = QWizardPage()
        welcome.setTitle(catalog.text("wizard.welcome"))
        form = QFormLayout(welcome)
        welcome_info = QLabel(catalog.text("wizard.welcome_body"))
        welcome_info.setWordWrap(True)
        form.addRow(welcome_info)
        self.language = QComboBox()
        for code, name in catalog.available.items():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(settings.get("language", "en"))))
        form.addRow(catalog.text("options.language"), self.language)
        self.addPage(welcome)

        account = QWizardPage()
        account.setTitle(catalog.text("wizard.credentials"))
        form = QFormLayout(account)
        account_info = QLabel(catalog.text("wizard.credentials_hint"))
        account_info.setWordWrap(True)
        form.addRow(account_info)
        link = QPushButton(catalog.text("wizard.open_credentials"))
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(
            "https://gelbooru.com/index.php?page=account&s=options")))
        form.addRow(link)
        self.paste = QLineEdit()
        self.paste.setEchoMode(QLineEdit.EchoMode.Password)
        self.paste.setPlaceholderText("&api_key=…&user_id=12345")
        form.addRow(catalog.text("wizard.paste"), self.paste)
        paste_button = QPushButton(catalog.text("wizard.parse"))
        paste_button.clicked.connect(self.parse_paste)
        form.addRow(paste_button)
        saved = credentials.get("gelbooru", {})
        self.user_id = QLineEdit(str(saved.get("user_id", "")))
        self.api_key = QLineEdit(str(saved.get("api_key", "")))
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(catalog.text("options.user_id"), self.user_id)
        form.addRow(catalog.text("options.api_key"), self.api_key)
        self.test_button = QPushButton(catalog.text("options.test_credentials"))
        self.test_button.clicked.connect(self.test_credentials)
        form.addRow(self.test_button)
        self.credential_status = QLabel()
        form.addRow(self.credential_status)
        self.addPage(account)

        resources = StatusPage(catalog.text("wizard.resources"), self.resource_status, catalog)
        resource_buttons = QHBoxLayout()
        self.install_buttons = {}
        for key, label in (("tags", "wizard.install_tags"), ("aliases", "wizard.install_aliases")):
            button = QPushButton(catalog.text(label))
            button.clicked.connect(lambda _checked=False, key=key: self._request_install(key))
            self.install_buttons[key] = button
            resource_buttons.addWidget(button)
        resources.layout().addLayout(resource_buttons)
        resources.activity_requested.connect(self.cancel_requested.emit)
        self.addPage(resources)

        analysis = StatusPage(catalog.text("wizard.analysis"), self.analysis_status, catalog)
        analysis.layout().addWidget(QLabel(catalog.text("wizard.analysis_optional")))
        model_buttons = QHBoxLayout()
        for key, label in (("wd14", "wizard.install_wd14"),):
            button = QPushButton(catalog.text(label))
            button.clicked.connect(lambda _checked=False, key=key: self._request_install(key))
            model_buttons.addWidget(button)
        analysis.layout().addLayout(model_buttons)
        analysis.activity_requested.connect(self.cancel_requested.emit)
        self.addPage(analysis)

        summary = StatusPage(catalog.text("wizard.summary"), self.summary_status, catalog)
        summary.activity_requested.connect(self.cancel_requested.emit)
        self.addPage(summary)
        self._status_pages = (resources, analysis, summary)
        self._active_activity = None
        self._sync_palette()
        self.finished.connect(self._finish)

    def _sync_palette(self) -> None:
        application = QApplication.instance()
        if application is None:
            return
        palette = application.palette()
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        for page_id in self.pageIds():
            page = self.page(page_id)
            page.setPalette(palette)
            page.setAutoFillBackground(True)

    def parse_paste(self):
        try:
            values = parse_gelbooru_credentials(self.paste.text())
        except ValueError:
            self.credential_status.setText(self.catalog.text("wizard.invalid_paste"))
            return
        self.user_id.setText(values["user_id"])
        self.api_key.setText(values["api_key"])
        self.paste.clear()
        self.credential_status.setText(self.catalog.text("wizard.parsed"))

    def _request_install(self, operation: str) -> None:
        activity_operation = "database" if operation == "tags" else operation
        self.set_activity(
            activity_operation, "starting", "", -1, -1,
            activity_operation in {"database", "aliases"},
        )
        self.install_requested.emit(operation)

    def set_activity(
        self, operation: str, state: str, message: str = "", current: int = -1,
        total: int = -1, cancellable: bool = False,
    ) -> None:
        self._active_activity = (operation, state, message, current, total, cancellable)
        for page in self._status_pages:
            page.set_activity(*self._active_activity)
        database_busy = operation in {"database", "aliases"} and state in {
            "starting", "running", "stopping",
        }
        finish_button = self.button(QWizard.WizardButton.FinishButton)
        if finish_button is not None:
            finish_button.setEnabled(state not in {"starting", "running", "stopping"})
        for button in self.install_buttons.values():
            button.setEnabled(not database_busy)
            button.setToolTip(
                self.catalog.text("wizard.unavailable_database_running")
                if database_busy else ""
            )
        summary = self.page(4)
        if isinstance(summary, StatusPage):
            summary.label.setText(self.summary_status())

    def test_credentials(self):
        self.credentials_test_requested.emit("gelbooru", {
            "user_id": self.user_id.text().strip(), "api_key": self.api_key.text().strip(),
        })

    def set_credential_test_running(self, _site):
        self.test_button.setEnabled(False)
        self.credential_status.setText(self.catalog.text("options.credentials_status_testing"))

    def show_credential_test_result(self, _site, status):
        self.test_button.setEnabled(True)
        self.credential_status.setText(self.catalog.text(f"options.credentials_status_{status}"))

    def resource_status(self):
        tags = gelbooru_tag_database(self.settings)
        aliases = gelbooru_alias_database(self.settings)
        return "\n".join((
            f"{self.catalog.text('wizard.tags_database')}: {self._state(tags, 'tags')}",
            f"{self.catalog.text('wizard.aliases_database')}: {self._state(aliases, 'gelbooru_aliases')}",
        ))

    def analysis_status(self):
        status = analysis_installation_status(self.root, self.settings)
        return "\n".join((
            f"{self.catalog.text('wizard.onnx_cpu')}: {self._yes(status.cpu_runtime_available)}",
            f"{self.catalog.text('wizard.cuda')}: {self._yes(status.cuda_runtime_available)}",
            f"{self.catalog.text('wizard.wd14')}: {self._yes(status.wd14_installed)}",
        ))

    def summary_status(self):
        account = bool(self.user_id.text().strip() and self.api_key.text().strip())
        lines = [
            f"{self.catalog.text('wizard.credentials_summary')}: {self._yes(account)}",
            self.resource_status(), self.analysis_status(),
        ]
        if self._active_activity is not None:
            operation, state, _message, _current, _total, _cancellable = self._active_activity
            if state not in {"completed", "cancelled"}:
                lines.append(
                    f"{self.catalog.text(f'wizard.activity_{operation}')}: "
                    f"{self.catalog.text(f'wizard.state_{state}')}"
                )
        return "\n".join(lines)

    def _yes(self, value):
        return self.catalog.text("wizard.ready" if value else "wizard.skipped")

    def _state(self, path, table):
        if not path or not path.is_file() or path.stat().st_size == 0:
            return self._yes(False)
        try:
            with closing(sqlite3.connect(
                f"file:{path.resolve().as_posix()}?mode=ro", uri=True
            )) as db:
                present = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
        except sqlite3.Error:
            return self._yes(False)
        return self._yes(bool(present))

    def _finish(self, result):
        values = ({"user_id": self.user_id.text().strip(),
                   "api_key": self.api_key.text().strip()}
                  if result == QWizard.DialogCode.Accepted else {})
        self.completed.emit(str(self.language.currentData()), values)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            self._sync_palette()
        super().changeEvent(event)
