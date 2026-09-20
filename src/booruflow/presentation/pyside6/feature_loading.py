"""Shared, non-modal loading and failure surface for deferred features."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.feature_lifecycle import FeatureState
from booruflow.presentation.pyside6.pages import ScrollablePageHost


class BusySpinner(QWidget):
    """Small Qt-painted spinner; no image or WebEngine dependency."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(0, 0)
        self._angle = 0
        self.setFixedSize(44, 44)
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._advance)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _advance(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self.palette().highlight().color()
        rect = QRectF(7, 7, 30, 30)
        for index in range(12):
            segment = QColor(color)
            segment.setAlpha(35 + index * 18)
            painter.setPen(QPen(segment, 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(rect, (self._angle + index * 30) * 16, 18 * 16)


class FeaturePageHost(QWidget):
    retry_requested = Signal()

    def __init__(
        self,
        page: QWidget | None,
        catalog: LanguageCatalog,
        feature_name_key: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.feature_name_key = feature_name_key
        self.page: QWidget | None = page
        self.content = ScrollablePageHost(page or QWidget())
        self._state = FeatureState.READY if page is not None else FeatureState.UNLOADED
        self._error = ""
        self._animation: QPropertyAnimation | None = None
        self.loading: QWidget | None = None
        self.spinner: BusySpinner | None = None
        self.loading_label: QLabel | None = None
        self.failure: QWidget | None = None
        self.failure_label: QLabel | None = None
        self.retry_button: QPushButton | None = None
        self.details_button: QPushButton | None = None
        self.details: QPlainTextEdit | None = None

        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self.stack.addWidget(self.content)

        self.retranslate()

    def _ensure_status_widgets(self) -> None:
        if self.loading is not None:
            return
        self.loading = QWidget()
        self.loading.setAutoFillBackground(True)
        loading_layout = QVBoxLayout(self.loading)
        loading_layout.addStretch(1)
        self.spinner = BusySpinner()
        spinner_row = QHBoxLayout()
        spinner_row.addStretch(1)
        spinner_row.addWidget(self.spinner)
        spinner_row.addStretch(1)
        loading_layout.addLayout(spinner_row)
        self.loading_label = QLabel()
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(self.loading_label)
        loading_layout.addStretch(1)
        self.stack.addWidget(self.loading)

        self.failure = QWidget()
        self.failure.setAutoFillBackground(True)
        failure_layout = QVBoxLayout(self.failure)
        failure_layout.addStretch(1)
        self.failure_label = QLabel()
        self.failure_label.setWordWrap(True)
        self.failure_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        failure_layout.addWidget(self.failure_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.retry_button = QPushButton()
        self.retry_button.clicked.connect(self.retry_requested)
        self.details_button = QPushButton()
        self.details_button.clicked.connect(self._toggle_details)
        buttons.addWidget(self.retry_button)
        buttons.addWidget(self.details_button)
        buttons.addStretch(1)
        failure_layout.addLayout(buttons)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(160)
        self.details.hide()
        failure_layout.addWidget(self.details)
        failure_layout.addStretch(1)
        self.stack.addWidget(self.failure)
        self.loading.hide()
        self.failure.hide()
        self.retranslate()

    def set_page(self, page: QWidget) -> None:
        old_content = self.content
        self.stack.removeWidget(old_content)
        old_content.hide()
        old_content.deleteLater()
        self.page = page
        self.content = ScrollablePageHost(page)
        self.stack.insertWidget(0, self.content)
        self.content.show()

    def clear_page(self) -> None:
        if self.page is None:
            return
        self.set_page(QWidget())
        self.page = None

    def horizontalScrollBar(self):
        return self.content.horizontalScrollBar()

    def verticalScrollBar(self):
        return self.content.verticalScrollBar()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.content.resize(event.size())

    def set_feature_state(self, state: FeatureState, error: str = "") -> None:
        self._state = state
        self._error = error
        if state is FeatureState.LOADING or state is FeatureState.UNLOADED:
            self._ensure_status_widgets()
            assert self.failure is not None and self.loading is not None
            assert self.spinner is not None
            self.failure.hide()
            self.loading.show()
            self.loading.raise_()
            self.spinner.start()
        elif state is FeatureState.FAILED:
            self._ensure_status_widgets()
            assert self.spinner is not None and self.details is not None
            assert self.loading is not None and self.failure is not None
            self.spinner.stop()
            self.details.setPlainText(error)
            self.loading.hide()
            self.failure.show()
            self.failure.raise_()
        else:
            if self.spinner is not None:
                self.spinner.stop()
            if self.loading is not None:
                self.loading.hide()
            if self.failure is not None:
                self.failure.hide()
            self.content.raise_()
            self._fade_in_content()
        self.retranslate()

    def retranslate(self) -> None:
        if self.loading_label is None:
            return
        assert self.failure_label is not None
        assert self.retry_button is not None and self.details_button is not None
        name = self.catalog.text(self.feature_name_key)
        self.loading_label.setText(self.catalog.text("feature.loading", feature=name))
        self.failure_label.setText(self.catalog.text("feature.failed", feature=name))
        self.retry_button.setText(self.catalog.text("feature.retry"))
        self.details_button.setText(self.catalog.text("feature.details"))

    def _toggle_details(self) -> None:
        if self.details is not None:
            self.details.setVisible(not self.details.isVisible())

    def _fade_in_content(self) -> None:
        effect = QGraphicsOpacityEffect(self.content)
        self.content.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(140)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: self.content.setGraphicsEffect(None))
        self._animation = animation
        animation.start()
