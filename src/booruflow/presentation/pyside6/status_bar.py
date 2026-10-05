"""Shared page-status channel and responsive application status bar."""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QLabel, QProgressBar, QSizePolicy, QStatusBar

from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.process_memory import (
    ProcessMemorySnapshot,
    collect_process_memory,
    format_bytes,
)


class _MemorySampleTask(QRunnable):
    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    def run(self) -> None:
        try:
            snapshot = collect_process_memory()
        except (OSError, ctypes.Error):
            snapshot = ProcessMemorySnapshot(0, ())
        self._callback(snapshot)


class StatusPriority(IntEnum):
    NORMAL = 0
    PROGRESS = 1
    WARNING = 2
    ERROR = 3
    CRITICAL = 4


class PageStatus(QObject):
    """Small page-facing API; it never exposes MainWindow widgets."""

    state_changed = Signal(str, str)
    message_changed = Signal(str, str, int, bool, bool)
    priority_message_changed = Signal(str, str, int, bool, bool, str)
    message_cleared = Signal(str)
    progress_changed = Signal(str, int, int, str, bool)
    progress_cleared = Signal(str, bool)
    thumbnail_state_changed = Signal(str, int, int, int, str)
    analysis_state_changed = Signal(str, int, int, int, int, int, int, str)
    analysis_state_cleared = Signal(str)
    position_changed = Signal(str, int, int)

    def __init__(self, page_key: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.page_key = page_key

    def set_state(self, state: str) -> None:
        self.state_changed.emit(self.page_key, state)

    def set_position(self, current: int, total: int) -> None:
        self.position_changed.emit(self.page_key, current, total)

    def show_message(
        self,
        message: str,
        *,
        timeout_ms: int = 5_000,
        log: bool = False,
        global_message: bool = False,
        level: str = "NORMAL",
    ) -> None:
        self.message_changed.emit(
            self.page_key, message, max(0, timeout_ms), log, global_message
        )
        self.priority_message_changed.emit(
            self.page_key, message, max(0, timeout_ms), log, global_message, level.upper()
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

    def set_thumbnail_state(self, loaded: int, total: int, failed: int = 0, tooltip: str = "") -> None:
        self.thumbnail_state_changed.emit(self.page_key, loaded, total, failed, tooltip)

    def set_analysis_state(
        self,
        completed: int,
        total: int,
        cached: int,
        analyzed: int,
        failed: int = 0,
        retrying: int = 0,
        tooltip: str = "",
    ) -> None:
        self.analysis_state_changed.emit(
            self.page_key, completed, total, cached, analyzed, failed, retrying, tooltip
        )

    def clear_analysis_state(self) -> None:
        self.analysis_state_cleared.emit(self.page_key)


@dataclass(frozen=True, slots=True)
class _Progress:
    value: int
    total: int
    accessible_text: str


@dataclass(frozen=True, slots=True)
class _Analysis:
    completed: int
    total: int
    cached: int
    analyzed: int
    failed: int
    retrying: int
    tooltip: str


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
        self.page_positions: dict[str, tuple[int, int]] = {}
        self.page_progress: dict[str, _Progress] = {}
        self.global_progress: _Progress | None = None
        self.message_owner: str | None = None
        self.message_is_global = False
        self.message_priority = StatusPriority.NORMAL
        self.thumbnail_states: dict[str, tuple[int, int, int, str]] = {}
        self.analysis_states: dict[str, _Analysis] = {}
        self._thumbnail_tooltip = ""
        self._memory_sample_pending = False

        self.page_label = QLabel()
        self.page_label.setMinimumWidth(150)
        self.page_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.message_label = QLabel()
        self.message_label.setMinimumWidth(0)
        self.message_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.analysis_label = QLabel()
        self.analysis_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.ram_label = QLabel()
        self.ram_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(140)
        self.progress.setMaximumHeight(18)
        self.progress.setTextVisible(True)
        self.progress.hide()
        self.message_timer = QTimer(self)
        self.message_timer.setSingleShot(True)
        self.message_timer.timeout.connect(self.clear_message)
        self._memory_timer = QTimer(self)
        self._memory_timer.setInterval(1_500)
        self._memory_timer.timeout.connect(self._request_memory_sample)
        self._memory_timer.start()

        status_bar.addWidget(self.page_label)
        status_bar.addWidget(self.thumbnail_label)
        status_bar.addWidget(self.analysis_label)
        status_bar.addWidget(self.ram_label)
        status_bar.addWidget(self.message_label, 1)
        status_bar.addPermanentWidget(self.progress)
        self.retranslate()

    def bind(self, channel: PageStatus) -> None:
        channel.state_changed.connect(self.set_page_state)
        channel.position_changed.connect(self.set_position)
        channel.priority_message_changed.connect(self.show_priority_message)
        channel.message_cleared.connect(self.clear_page_message)
        channel.progress_changed.connect(self.set_progress)
        channel.progress_cleared.connect(self.clear_progress)
        channel.thumbnail_state_changed.connect(self.set_thumbnail_state)
        channel.analysis_state_changed.connect(self.set_analysis_state)
        channel.analysis_state_cleared.connect(self.clear_analysis_state)

    def activate_page(self, page_key: str) -> None:
        self.active_page = page_key
        if self.message_owner != page_key and not self.message_is_global:
            self.clear_message()
        self._refresh_page_label()
        self._refresh_thumbnail()
        self._refresh_analysis()
        self._refresh_progress()

    def set_page_state(self, page_key: str, state: str) -> None:
        self.page_states[page_key] = state
        if page_key == self.active_page:
            self._refresh_page_label()

    def set_position(self, page_key: str, current: int, total: int) -> None:
        self.page_positions[page_key] = (current, total)
        if page_key == self.active_page:
            self._refresh_page_label()

    def set_thumbnail_state(
        self, page_key: str, loaded: int, total: int, failed: int, tooltip: str
    ) -> None:
        self.thumbnail_states[page_key] = (loaded, total, failed, tooltip)
        if page_key != self.active_page:
            return
        self._refresh_thumbnail()

    def set_analysis_state(
        self,
        page_key: str,
        completed: int,
        total: int,
        cached: int,
        analyzed: int,
        failed: int,
        retrying: int,
        tooltip: str,
    ) -> None:
        self.analysis_states[page_key] = _Analysis(
            completed, total, cached, analyzed, failed, retrying, tooltip
        )
        if page_key == self.active_page:
            self._refresh_analysis()

    def clear_analysis_state(self, page_key: str) -> None:
        self.analysis_states.pop(page_key, None)
        if page_key == self.active_page:
            self._refresh_analysis()

    def _refresh_analysis(self) -> None:
        state = self.analysis_states.get(self.active_page)
        if state is None:
            self.analysis_label.clear()
            self.analysis_label.setToolTip("")
            self.analysis_label.setAccessibleName("")
            return
        text = self.catalog.text(
            "status.analysis",
            completed=state.completed,
            total=state.total,
            cached=state.cached,
            analyzed=state.analyzed,
            failed=state.failed,
            retrying=state.retrying,
        )
        self.analysis_label.setText(f"⚠ {text}" if state.failed else text)
        self.analysis_label.setToolTip(state.tooltip or text)
        self.analysis_label.setAccessibleName(text)
        self.analysis_label.setProperty("statusLevel", "error" if state.failed else "normal")
        self.analysis_label.style().unpolish(self.analysis_label)
        self.analysis_label.style().polish(self.analysis_label)

    def _refresh_thumbnail(self) -> None:
        loaded, total, failed, tooltip = self.thumbnail_states.get(
            self.active_page, (0, 0, 0, "")
        )
        if total <= 0:
            self.thumbnail_label.clear()
            self.thumbnail_label.setToolTip("")
            self._thumbnail_tooltip = ""
            self._refresh_ram_tooltip()
            return
        text = self.catalog.text("status.thumbnails", loaded=loaded, total=total)
        if failed:
            text += self.catalog.text("status.thumbnails_failed", failed=failed)
        self.thumbnail_label.setText(text)
        self.thumbnail_label.setToolTip(tooltip)
        self.thumbnail_label.setAccessibleName(text)
        self._thumbnail_tooltip = tooltip
        self._refresh_ram_tooltip()

    def _request_memory_sample(self) -> None:
        if self._memory_sample_pending:
            return
        self._memory_sample_pending = True
        QThreadPool.globalInstance().start(_MemorySampleTask(self._memory_sample_ready))

    def _memory_sample_ready(self, snapshot: ProcessMemorySnapshot) -> None:
        self._memory_sample_pending = False
        self.ram_label.setText(self.catalog.text("status.ram", value=format_bytes(snapshot.total_bytes)))
        self.ram_label.setToolTip(self._memory_tooltip(snapshot))

    def _memory_tooltip(self, snapshot: ProcessMemorySnapshot) -> str:
        lines = [self.catalog.text("status.ram.tooltip", value=format_bytes(snapshot.total_bytes))]
        for row in snapshot.rows:
            lines.append(f"{row.name} (PID {row.pid}): {format_bytes(row.working_set)}")
        if snapshot.available_bytes is not None:
            lines.append(self.catalog.text("status.ram.available", value=format_bytes(snapshot.available_bytes)))
        if self._thumbnail_tooltip:
            lines.extend(("", self._thumbnail_tooltip))
        return "\n".join(lines)

    def _refresh_ram_tooltip(self) -> None:
        if self.ram_label.toolTip():
            # The next asynchronous sample will rebuild the process section;
            # this keeps thumbnail details current without sampling per image.
            self.ram_label.setToolTip(self.ram_label.toolTip().split("\n\nThumbnail images:")[0] +
                                      (("\n\n" + self._thumbnail_tooltip) if self._thumbnail_tooltip else ""))

    def show_message(
        self,
        page_key: str,
        message: str,
        timeout_ms: int = 5_000,
        log_message: bool = False,
        global_message: bool = False,
        level: str = "NORMAL",
    ) -> None:
        priority = StatusPriority.__members__.get(level.upper(), StatusPriority.NORMAL)
        if (
            priority < self.message_priority
            and self.message_priority >= StatusPriority.WARNING
            and self.message_owner is not None
        ):
            return
        if log_message:
            self.log(message)
        if page_key != self.active_page and not global_message:
            return
        self.message_timer.stop()
        self.message_owner = page_key
        self.message_is_global = global_message
        self.message_priority = priority
        self.message_label.setText(message)
        self.message_label.setToolTip(message)
        self.message_label.setAccessibleName(message)
        if timeout_ms > 0 and priority < StatusPriority.ERROR:
            self.message_timer.start(timeout_ms)

    def show_priority_message(
        self,
        page_key: str,
        message: str,
        timeout_ms: int = 5_000,
        log_message: bool = False,
        global_message: bool = False,
        level: str = "NORMAL",
    ) -> None:
        self.show_message(page_key, message, timeout_ms, log_message, global_message, level)

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
        self.message_priority = StatusPriority.NORMAL

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
        text = self.catalog.text("status.page_state", page=page, state=state)
        current, total = self.page_positions.get(self.active_page, (0, 0))
        if 0 < current <= total:
            text += f" │ {current} / {total}"
        self.page_label.setText(text)

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
        self._refresh_analysis()
        self._refresh_progress()
