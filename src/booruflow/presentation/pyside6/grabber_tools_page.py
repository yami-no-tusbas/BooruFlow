"""Shared shell for the tag-list and Grabber workflow."""

from __future__ import annotations

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from booruflow.infrastructure.localization import LanguageCatalog


class GrabberToolsPage(QWidget):
    """Expose the existing Review and Grabber pages behind one navigation entry."""

    def __init__(
        self,
        catalog: LanguageCatalog,
        review_page: QWidget,
        grabber_page: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.review_page = review_page
        self.grabber_page = grabber_page
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.addTab(review_page, "")
        self.tabs.addTab(grabber_page, "")
        layout.addWidget(self.tabs)
        self.retranslate()

    def show_builder(self) -> None:
        self.tabs.setCurrentWidget(self.review_page)

    def show_launcher(self) -> None:
        self.tabs.setCurrentWidget(self.grabber_page)

    def retranslate(self) -> None:
        self.tabs.setTabText(0, self.catalog.text("grabber_tools.builder"))
        self.tabs.setTabText(1, self.catalog.text("grabber_tools.launcher"))
        for page in (self.review_page, self.grabber_page):
            if hasattr(page, "retranslate"):
                page.retranslate()
