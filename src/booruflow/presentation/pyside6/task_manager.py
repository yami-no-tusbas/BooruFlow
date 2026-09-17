"""Qt facade for persistent workflow task state."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from booruflow.application.tasks import TaskRecord, TaskRepository


class TaskManager(QObject):
    changed = Signal(object)

    def __init__(self, repository: TaskRepository, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.tasks = self._recover(repository.load())
        self.repository.save(self.tasks)

    @staticmethod
    def _recover(tasks: list[TaskRecord]) -> list[TaskRecord]:
        return [
            task.finish("interrupted", "Application fermée pendant l’opération")
            if not task.is_finished
            else task
            for task in tasks
        ]

    def start(self, kind: str, title: str, message: str = "") -> str:
        task = TaskRecord.start(kind, title, message)
        self.tasks.append(task)
        self._persist(task)
        return task.id

    def progress(
        self,
        task_id: str,
        completed: int,
        total: int,
        phase: str = "",
        message: str = "",
    ) -> None:
        self._update(
            task_id,
            completed=max(0, completed),
            total=max(0, total),
            phase=phase,
            message=message,
        )

    def finish(self, task_id: str, state: str = "completed", message: str = "") -> None:
        for index, task in enumerate(self.tasks):
            if task.id == task_id:
                finished = task.finish(state, message)
                self.tasks[index] = finished
                self._persist(finished)
                return

    def _update(self, task_id: str, **changes: object) -> None:
        for index, task in enumerate(self.tasks):
            if task.id == task_id:
                updated = task.evolve(**changes)
                self.tasks[index] = updated
                self._persist(updated)
                return

    def _persist(self, changed: TaskRecord) -> None:
        self.repository.save(self.tasks)
        self.changed.emit(changed)
