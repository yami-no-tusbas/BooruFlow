"""Interfaces implemented by infrastructure adapters."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from booruflow.domain import SearchRequest, ToolAvailability


class ProgressSink(Protocol):
    def report(self, phase: str, completed: int, total: int, message: str = "") -> None: ...


class SearchProvider(Protocol):
    def search(self, request: SearchRequest, progress: ProgressSink) -> Iterable[str]: ...


class GrabberGateway(Protocol):
    def availability(self) -> ToolAvailability: ...

    def generate_batches(self, tags: Iterable[str], destination: Path) -> list[Path]: ...

    def launch_batch(self, batch: Path) -> None: ...


class SettingsRepository(Protocol):
    def load(self) -> dict[str, object]: ...

    def save(self, values: dict[str, object]) -> None: ...

