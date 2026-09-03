import json
from pathlib import Path

from booruflow.application.tasks import MemoryTaskRepository, TaskRecord
from booruflow.infrastructure.task_repository import JsonTaskRepository
from booruflow.presentation.pyside6.task_manager import TaskManager


def test_task_record_tracks_progress_and_completion() -> None:
    task = TaskRecord.start("scan", "Scan")
    task = task.evolve(completed=4, total=10, phase="index")
    assert task.completed == 4
    assert not task.is_finished
    task = task.finish("completed", "done")
    assert task.is_finished
    assert task.finished_at


def test_json_task_repository_round_trip_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    repository = JsonTaskRepository(path)
    task = TaskRecord.start("review", "Review").finish("completed")
    repository.save([task])
    assert repository.load() == [task]
    assert json.loads(path.read_text(encoding="utf-8"))[0]["id"] == task.id
    assert not path.with_suffix(".json.tmp").exists()


def test_memory_task_repository_does_not_expose_internal_list() -> None:
    repository = MemoryTaskRepository()
    loaded = repository.load()
    loaded.append(TaskRecord.start("test", "Test"))
    assert repository.load() == []


def test_task_manager_recovers_running_tasks_as_interrupted() -> None:
    repository = MemoryTaskRepository([TaskRecord.start("review", "Review")])
    manager = TaskManager(repository)
    assert manager.tasks[0].state == "interrupted"
    assert repository.load()[0].finished_at
