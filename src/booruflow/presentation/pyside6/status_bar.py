"""Shared page-status channel and responsive application status bar."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QLabel, QProgressBar, QSizePolicy, QStatusBar

from booruflow.infrastructure.localization import LanguageCatalog


class PageStatus(QObject):
    """Small page-facing API; it never exposes MainWindow widgets."""

    state_changed = Signal(str, str)
    message_changed = Signal(str, str, int, bool, bool)
    message_cleared = Signal(str)
    progress_changed = Signal(str, int, int, str, bool)
    progress_cleared = Signal(str, bool)

    def __init__(self, page_key: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.page_key = page_key

    def set_state(self, state: str) -> None:
        self.state_changed.emit(self.page_key, state)

    def show_message(
        self,
        message: str,
        *,
        timeout_ms: int = 5_000,
        log: bool = False,
        global_message: bool = False,
    ) -> None:
        self.message_changed.emit(
            self.page_key, message, max(0, timeout_ms), log, global_message
        )

    def clear_message(self) -> None:
        self.message_cleared.emit(self.page_key)

    def set_progress(
        self,
        value: int,
        total: int,
        *,
        accessible_text: str = "",
        global_task: bool = False,
    ) -> None:
        self.progress_changed.emit(
            self.page_key, max(0, value), max(0, total), accessible_text, global_task
        )

    def set_busy(self, *, accessible_text: str = "", global_task: bool = False) -> None:
        self.progress_changed.emit(self.page_key, 0, 0, accessible_text, global_task)

    def clear_progress(self, *, global_task: bool = False) -> None:
        self.progress_cleared.emit(self.page_key, global_task)


@dataclass(frozen=True, slots=True)
class _Progress:
    value: int
    total: int
    accessible_text: str


class StatusBarController(QObject):
    """Own status widgets and keep page-scoped feedback from leaking across pages."""

    def __init__(
        self,
        status_bar: QStatusBar,
        catalog: LanguageCatalog,
        log: Callable[[str], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.log = log
        self.active_page = "home"
        self.page_states: dict[str, str] = {}
        self.page_progress: dict[str, _Progress] = {}
        self.global_progress: _Progress | None = None
        self.message_owner: str | None = None
        self.message_is_global = False

        self.page_label = QLabel()
        self.page_label.setMinimumWidth(150)
        self.page_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.message_label = QLabel()
        self.message_label.setMinimumWidth(0)
        self.message_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(140)
        self.progress.setMaximumHeight(18)
        self.progress.setTextVisible(True)
        self.progress.hide()
        self.message_timer = QTimer(self)
        self.message_timer.setSingleShot(True)
        self.message_timer.timeout.connect(self.clear_message)

        status_bar.addWidget(self.page_label)
        status_bar.addWidget(self.message_label, 1)
        status_bar.addPermanentWidget(self.progress)
        self.retranslate()

    def bind(self, channel: PageStatus) -> None:
        channel.state_changed.connect(self.set_page_state)
        channel.message_changed.connect(self.show_message)
        channel.message_cleared.connect(self.clear_page_message)
        channel.progress_changed.connect(self.set_progress)
        channel.progress_cleared.connect(self.clear_progress)

    def activate_page(self, page_key: str) -> None:
        self.active_page = page_key
        if self.message_owner != page_key and not self.message_is_global:
            self.clear_message()
        self._refresh_page_label()
        self._refresh_progress()

    def set_page_state(self, page_key: str, state: str) -> None:
        self.page_states[page_key] = state
        if page_key == self.active_page:
            self._refresh_page_label()

    def show_message(
        self,
        page_key: str,
        message: str,
        timeout_ms: int = 5_000,
        log_message: bool = False,
        global_message: bool = False,
    ) -> None:
        if log_message:
            self.log(message)
        if page_key != self.active_page and not global_message:
            return
        self.message_timer.stop()
        self.message_owner = page_key
        self.message_is_global = global_message
        self.message_label.setText(message)
        self.message_label.setToolTip(message)
        self.message_label.setAccessibleName(message)
        if timeout_ms > 0:
            self.message_timer.start(timeout_ms)

    def clear_page_message(self, page_key: str) -> None:
        if self.message_owner == page_key and not self.message_is_global:
            self.clear_message()

    def clear_message(self) -> None:
        self.message_timer.stop()
        self.message_owner = None
        self.message_is_global = False
        self.message_label.clear()
        self.message_label.setToolTip("")
        self.message_label.setAccessibleName("")

    def set_progress(
        self,
        page_key: str,
        value: int,
        total: int,
        accessible_text: str = "",
        global_task: bool = False,
    ) -> None:
        progress = _Progress(value, total, accessible_text)
        if global_task:
            self.global_progress = progress
        else:
            self.page_progress[page_key] = progress
        self._refresh_progress()

    def clear_progress(self, page_key: str, global_task: bool = False) -> None:
        if global_task:
            self.global_progress = None
        else:
            self.page_progress.pop(page_key, None)
        self._refresh_progress()

    def _refresh_page_label(self) -> None:
        page = self.catalog.text(f"nav.{self.active_page}")
        state = self.catalog.text(
            f"status.state.{self.page_states.get(self.active_page, 'ready')}"
        )
        self.page_label.setText(self.catalog.text("status.page_state", page=page, state=state))

    def _refresh_progress(self) -> None:
        progress = self.global_progress or self.page_progress.get(self.active_page)
        if progress is None:
            self.progress.hide()
            self.progress.setAccessibleName("")
            return
        if progress.total <= 0:
            self.progress.setRange(0, 0)
            self.progress.setFormat("")
        else:
            self.progress.setRange(0, progress.total)
            self.progress.setValue(min(progress.value, progress.total))
            self.progress.setFormat("%v / %m")
        accessible = progress.accessible_text or self.catalog.text("status.progress")
        self.progress.setAccessibleName(accessible)
        self.progress.setToolTip(accessible)
        self.progress.show()

    def retranslate(self) -> None:
        self._refresh_page_label()
        self._refresh_progress()
