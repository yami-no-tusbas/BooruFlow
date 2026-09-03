"""Persistent task history shared by long-running workflows."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

FINAL_TASK_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class TaskRecord:
    id: str
    kind: str
    title: str
    state: str = "running"
    phase: str = ""
    completed: int = 0
    total: int = 0
    message: str = ""
    started_at: str = ""
    updated_at: str = ""
    finished_at: str = ""

    @classmethod
    def start(cls, kind: str, title: str, message: str = "") -> TaskRecord:
        now = utc_now()
        return cls(uuid4().hex, kind, title, message=message, started_at=now, updated_at=now)

    @property
    def is_finished(self) -> bool:
        return self.state in FINAL_TASK_STATES

    def evolve(self, **changes: object) -> TaskRecord:
        changes.setdefault("updated_at", utc_now())
        return replace(self, **changes)

    def finish(self, state: str, message: str = "") -> TaskRecord:
        if state not in FINAL_TASK_STATES:
            raise ValueError(f"invalid final task state: {state}")
        now = utc_now()
        return replace(self, state=state, message=message, updated_at=now, finished_at=now)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> TaskRecord:
        fields = cls.__dataclass_fields__
        return cls(**{key: value[key] for key in fields if key in value})


class TaskRepository(Protocol):
    def load(self) -> list[TaskRecord]: ...

    def save(self, tasks: list[TaskRecord]) -> None: ...


class MemoryTaskRepository:
    def __init__(self, tasks: list[TaskRecord] | None = None) -> None:
        self.tasks = list(tasks or [])

    def load(self) -> list[TaskRecord]:
        return list(self.tasks)

    def save(self, tasks: list[TaskRecord]) -> None:
        self.tasks = list(tasks)
