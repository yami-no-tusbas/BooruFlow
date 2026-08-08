"""Reusable localized pages for the BooruFlow PySide6 shell."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from booruflow.domain import ToolAvailability
from booruflow.infrastructure.localization import LanguageCatalog


@dataclass(frozen=True, slots=True)
class FeatureCard:
    navigation_key: str
    description_key: str
    target_index: int


class DashboardPage(QWidget):
    navigate_requested = Signal(int)

    def __init__(
        self,
        catalog: LanguageCatalog,
        grabber: ToolAvailability,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.grabber = grabber
        self.card_widgets: list[tuple[FeatureCard, QLabel, QLabel, QPushButton]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 24px; font-weight: 600;")
        layout.addWidget(self.title)
        self.subtitle = QLabel()
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        cards = (
            FeatureCard("review", "home.review", 1),
            FeatureCard("tagging", "home.tagging", 2),
            FeatureCard("organization", "home.organization", 3),
            FeatureCard("wiki", "home.wiki", 4),
            FeatureCard("cleanup", "home.cleanup", 5),
            FeatureCard("options", "home.options", 6),
            FeatureCard("grabber", "home.grabber", 7),
        )
        for position, card in enumerate(cards):
            grid.addWidget(self._card(card), position // 2, position % 2)
        layout.addLayout(grid)
        layout.addStretch(1)
        self.availability = QLabel()
        self.availability.setWordWrap(True)
        layout.addWidget(self.availability)
        self.retranslate()

    def _card(self, card: FeatureCard) -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(frame)
        heading = QLabel()
        heading.setStyleSheet("font-size: 16px; font-weight: 600;")
        description = QLabel()
        description.setWordWrap(True)
        button = QPushButton()
        button.clicked.connect(
            lambda _checked=False, index=card.target_index: self.navigate_requested.emit(index)
        )
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button)
        layout.addWidget(heading)
        layout.addWidget(description)
        layout.addLayout(row)
        self.card_widgets.append((card, heading, description, button))
        return frame

    def retranslate(self) -> None:
        text = self.catalog.text
        self.title.setText(text("app.title"))
        self.subtitle.setText(text("home.subtitle"))
        for card, heading, description, button in self.card_widgets:
            heading.setText(text(f"nav.{card.navigation_key}"))
            description.setText(text(card.description_key))
            button.setText(text("home.open"))
        if self.grabber.available:
            self.availability.setText(text("grabber.available"))
        else:
            self.availability.setText(text("grabber.unavailable", reason=self.grabber.reason))


class PlaceholderPage(QWidget):
    def __init__(
        self,
        catalog: LanguageCatalog,
        navigation_key: str,
        description_key: str,
        *,
        availability: ToolAvailability | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.navigation_key = navigation_key
        self.description_key = description_key
        self.tool_availability = availability
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(12)
        self.heading = QLabel()
        self.heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(self.heading)
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.description)
        self.availability_label: QLabel | None = None
        if availability is not None:
            self.availability_label = QLabel()
            self.availability_label.setWordWrap(True)
            self.availability_label.setFrameShape(QFrame.Shape.StyledPanel)
            self.availability_label.setContentsMargins(12, 10, 12, 10)
            layout.addWidget(self.availability_label)
        layout.addStretch(1)
        self.retranslate()

    def retranslate(self) -> None:
        text = self.catalog.text
        self.heading.setText(text(f"nav.{self.navigation_key}"))
        self.description.setText(text(self.description_key))
        if self.availability_label is not None and self.tool_availability is not None:
            key = (
                "capability.available"
                if self.tool_availability.available
                else "capability.unavailable"
            )
            state = text(key)
            reason = self.tool_availability.reason
            self.availability_label.setText(f"{state}: {reason}" if reason else state)
