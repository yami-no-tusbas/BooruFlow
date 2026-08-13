"""Unified task history page."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from booruflow.infrastructure.localization import LanguageCatalog
from booruflow.presentation.pyside6.task_manager import TaskManager


class TaskPage(QWidget):
    def __init__(self, catalog: LanguageCatalog, manager: TaskManager) -> None:
        super().__init__()
        self.catalog = catalog
        self.manager = manager
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 22)
        layout.setSpacing(10)
        self.title = QLabel()
        self.title.setStyleSheet("font-size:22px;font-weight:600;")
        self.description = QLabel()
        self.description.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.description)
        self.table = QTableWidget(0, 6)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        for column in (0, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)
        self.manager.changed.connect(lambda _task: self.refresh())
        self.retranslate()

    def refresh(self) -> None:
        tasks = list(reversed(self.manager.tasks))
        self.table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            values = (
                task.title,
                self.catalog.text(f"tasks.state_{task.state}"),
                task.phase,
                task.message,
                task.started_at.replace("T", " ")[:19],
            )
            for column, value in enumerate(values[:4]):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, task.id)
                self.table.setItem(row, column, item)
            progress = QProgressBar()
            if task.total:
                progress.setRange(0, task.total)
                progress.setValue(min(task.completed, task.total))
                progress.setFormat(f"{task.completed}/{task.total}")
            else:
                progress.setRange(0, 0 if task.state == "running" else 1)
                progress.setValue(1 if task.is_finished else 0)
                progress.setFormat("—")
            self.table.setCellWidget(row, 4, progress)
            self.table.setItem(row, 5, QTableWidgetItem(values[4]))

    def retranslate(self) -> None:
        self.title.setText(self.catalog.text("nav.tasks"))
        self.description.setText(self.catalog.text("tasks.description"))
        self.table.setHorizontalHeaderLabels(
            [
                self.catalog.text(f"tasks.column_{key}")
                for key in ("title", "state", "phase", "message", "progress", "started")
            ]
        )
        self.refresh()
