"""Reusable pages for the first BooruFlow PySide6 shell."""

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


@dataclass(frozen=True, slots=True)
class FeatureCard:
    title: str
    description: str
    target_index: int


class DashboardPage(QWidget):
    navigate_requested = Signal(int)

    def __init__(self, grabber: ToolAvailability, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)

        title = QLabel("BooruFlow")
        title.setObjectName("pageTitle")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Search, review, organize and maintain Booru metadata from one workspace."
        )
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        cards = (
            FeatureCard("Review", "Find and review tags by category or text list.", 1),
            FeatureCard("Tagging", "Inspect under-tagged posts without automatic submission.", 2),
            FeatureCard("Organization", "Maintain the local tag taxonomy and its sources.", 3),
            FeatureCard("Cleanup", "Audit existing folders before recoverable cleanup.", 4),
            FeatureCard("Options", "Configure sites, databases, language and credentials.", 5),
            FeatureCard(
                "Grabber",
                "Generate and resume Grabber batches when the optional tool is available.",
                6,
            ),
        )
        for position, card in enumerate(cards):
            grid.addWidget(self._card(card), position // 2, position % 2)
        layout.addLayout(grid)
        layout.addStretch(1)

        availability = QLabel(
            "Grabber: available" if grabber.available else f"Grabber: unavailable — {grabber.reason}"
        )
        availability.setWordWrap(True)
        availability.setObjectName("capabilityStatus")
        layout.addWidget(availability)

    def _card(self, card: FeatureCard) -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(frame)
        heading = QLabel(card.title)
        heading.setStyleSheet("font-size: 16px; font-weight: 600;")
        description = QLabel(card.description)
        description.setWordWrap(True)
        button = QPushButton("Open")
        button.setProperty("targetIndex", card.target_index)
        button.clicked.connect(
            lambda _checked=False, index=card.target_index: self.navigate_requested.emit(index)
        )
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button)
        layout.addWidget(heading)
        layout.addWidget(description)
        layout.addLayout(row)
        return frame


class PlaceholderPage(QWidget):
    def __init__(
        self,
        title: str,
        description: str,
        *,
        availability: ToolAvailability | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(12)

        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(heading)

        text = QLabel(description)
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(text)

        if availability is not None:
            state = "Available" if availability.available else "Unavailable"
            detail = QLabel(f"{state}: {availability.reason}" if availability.reason else state)
            detail.setWordWrap(True)
            detail.setFrameShape(QFrame.Shape.StyledPanel)
            detail.setContentsMargins(12, 10, 12, 10)
            layout.addWidget(detail)

        layout.addStretch(1)
