"""Main window and top-level navigation for BooruFlow."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from booruflow.application.capabilities import ApplicationCapabilities
from booruflow.application.ports import SettingsRepository
from booruflow.presentation.pyside6.icons import navigation_icon
from booruflow.presentation.pyside6.options_page import OptionsPage
from booruflow.presentation.pyside6.pages import DashboardPage, PlaceholderPage


class MainWindow(QMainWindow):
    NAVIGATION = (
        "Home",
        "Review",
        "Tagging",
        "Organization",
        "Cleanup",
        "Options",
        "Grabber",
    )

    def __init__(
        self,
        capabilities: ApplicationCapabilities,
        parent: QWidget | None = None,
        settings_repository: SettingsRepository | None = None,
        credentials_repository: SettingsRepository | None = None,
    ) -> None:
        super().__init__(parent)
        self.capabilities = capabilities
        self.settings_repository = settings_repository
        self.credentials_repository = credentials_repository
        self.setWindowTitle("BooruFlow")
        self.resize(1120, 760)
        self.setMinimumSize(860, 600)

        self.navigation = QListWidget()
        self.navigation.setObjectName("mainNavigation")
        self.navigation.setFixedWidth(230)
        self.navigation.setIconSize(QSize(28, 28))
        self.navigation.setSpacing(2)
        for label in self.NAVIGATION:
            item = QListWidgetItem(navigation_icon(label), label)
            item.setData(Qt.ItemDataRole.UserRole, label)
            item.setSizeHint(QSize(210, 44))
            item.setToolTip(label)
            self.navigation.addItem(item)

        self.pages = QStackedWidget()
        dashboard = DashboardPage(capabilities.grabber)
        dashboard.navigate_requested.connect(self.navigate_to)
        self.pages.addWidget(dashboard)
        self.pages.addWidget(
            PlaceholderPage("Review", "Category review and TXT-list review will be consolidated here.")
        )
        self.pages.addWidget(
            PlaceholderPage("Tagging", "Manual browser-assisted tagging review will be migrated here.")
        )
        self.pages.addWidget(
            PlaceholderPage(
                "Organization",
                "Taxonomy browsing, editing and source updates will live here.",
            )
        )
        self.pages.addWidget(
            PlaceholderPage(
                "Cleanup",
                "Folder drop, audit progress and recoverable actions will live here.",
            )
        )
        options = OptionsPage(
            settings_repository.load() if settings_repository else {},
            credentials_repository.load() if credentials_repository else {},
        )
        options.save_requested.connect(self._save_options)
        self.pages.addWidget(options)
        self.pages.addWidget(
            PlaceholderPage(
                "Grabber",
                "This section is optional. The rest of BooruFlow remains usable without Grabber.",
                availability=capabilities.grabber,
            )
        )

        workspace = QSplitter(Qt.Orientation.Horizontal)
        workspace.setChildrenCollapsible(False)
        workspace.addWidget(self.navigation)
        workspace.addWidget(self.pages)
        workspace.setStretchFactor(1, 1)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("applicationLog")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2_000)
        self.log_view.setMinimumHeight(120)
        self.log_view.setMaximumHeight(190)
        self.log_view.hide()

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(8, 8, 8, 4)
        central_layout.setSpacing(6)
        central_layout.addWidget(workspace, 1)
        central_layout.addWidget(self.log_view)
        self.setCentralWidget(central)

        status_bar = QStatusBar()
        status_bar.setContentsMargins(8, 3, 8, 3)
        self.setStatusBar(status_bar)
        self.status_label = QLabel("Ready.")
        self.status_label.setContentsMargins(4, 2, 12, 2)
        status_bar.addWidget(self.status_label, 1)
        self.log_button = QPushButton("Show log")
        self.log_button.clicked.connect(self.toggle_log)
        self.clear_log_button = QPushButton("Clear log")
        self.clear_log_button.clicked.connect(self.log_view.clear)
        status_bar.addPermanentWidget(self.log_button)
        status_bar.addPermanentWidget(self.clear_log_button)

        self.navigation.currentRowChanged.connect(self._navigation_changed)
        self.navigation.setCurrentRow(0)
        self.log("BooruFlow PySide6 shell started.")
        if not capabilities.grabber.available:
            self.log(f"Optional Grabber integration unavailable: {capabilities.grabber.reason}")

    def navigate_to(self, index: int) -> None:
        if 0 <= index < self.pages.count():
            self.navigation.setCurrentRow(index)

    def _navigation_changed(self, index: int) -> None:
        if index < 0:
            return
        self.pages.setCurrentIndex(index)
        label = self.navigation.item(index).data(Qt.ItemDataRole.UserRole)
        self.status_label.setText(f"{label} — Ready.")

    def _save_options(self, settings: dict, credentials: dict) -> None:
        try:
            if self.settings_repository:
                self.settings_repository.save(settings)
            if self.credentials_repository:
                self.credentials_repository.save(credentials)
        except OSError as exc:
            self.status_label.setText("Options — Save failed.")
            self.log(f"Could not save options: {exc}")
            self.log_view.show()
            self.log_button.setText("Hide log")
            return
        self.status_label.setText("Options — Saved.")
        self.log("Options saved. Restart BooruFlow to refresh external tool availability.")

    def toggle_log(self) -> None:
        visible = not self.log_view.isVisible()
        self.log_view.setVisible(visible)
        self.log_button.setText("Hide log" if visible else "Show log")

    def log(self, message: str) -> None:
        self.log_view.appendPlainText(f"[{datetime.now():%H:%M:%S}] {message}")
