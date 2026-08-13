"""Atomic JSON storage for the local task history."""

from __future__ import annotations

import json
import os
from pathlib import Path

from booruflow.application.tasks import TaskRecord


class JsonTaskRepository:
    def __init__(self, path: Path, limit: int = 200) -> None:
        self.path = path
        self.limit = limit

    def load(self) -> list[TaskRecord]:
        try:
            values = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            return []
        if not isinstance(values, list):
            return []
        tasks: list[TaskRecord] = []
        for value in values:
            if not isinstance(value, dict):
                continue
            try:
                tasks.append(TaskRecord.from_dict(value))
            except (KeyError, TypeError, ValueError):
                continue
        return tasks[-self.limit :]

    def save(self, tasks: list[TaskRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                [task.to_dict() for task in tasks[-self.limit :]], ensure_ascii=False, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
