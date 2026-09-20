"""Deterministic feature preparation after the first usable window."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from booruflow import startup_profile


class FeaturePolicy(StrEnum):
    EAGER = "eager"
    WARMUP = "warmup"
    LAZY = "lazy"


class FeatureState(StrEnum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


FeatureDone = Callable[[], None]
FeatureFailed = Callable[[BaseException | str], None]
FeaturePrepare = Callable[[FeatureDone, FeatureFailed], None]


@dataclass(slots=True)
class FeatureRegistration:
    key: str
    policy: FeaturePolicy
    prepare: FeaturePrepare
    dependencies: tuple[str, ...] = ()
    state: FeatureState = FeatureState.UNLOADED
    error: str = ""
    attempts: int = 0


class FeatureLifecycle(QObject):
    """Serialize preparation, while putting explicit user requests first."""

    state_changed = Signal(str, str, str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        schedule: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._features: dict[str, FeatureRegistration] = {}
        self._queue: deque[str] = deque()
        self._active: str | None = None
        self._active_started_ns = 0
        self._user_pending: set[str] = set()
        self._schedule = schedule or (lambda callback: QTimer.singleShot(0, callback))

    def register(
        self,
        key: str,
        policy: FeaturePolicy,
        prepare: FeaturePrepare,
        *,
        ready: bool = False,
        dependencies: tuple[str, ...] = (),
    ) -> None:
        if key in self._features:
            raise ValueError(f"feature already registered: {key}")
        state = FeatureState.READY if ready else FeatureState.UNLOADED
        self._features[key] = FeatureRegistration(
            key, policy, prepare, tuple(dependencies), state
        )

    def policy(self, key: str) -> FeaturePolicy:
        return self._features[key].policy

    def state(self, key: str) -> FeatureState:
        return self._features[key].state

    def error(self, key: str) -> str:
        return self._features[key].error

    def start_warmups(self) -> None:
        for feature in self._features.values():
            if feature.policy is FeaturePolicy.WARMUP:
                self._enqueue(feature.key)
        self._schedule(self._pump)

    def request(self, key: str) -> None:
        feature = self._features[key]
        if feature.state is FeatureState.READY:
            return
        requested = (key, *reversed(feature.dependencies))
        for requested_key in requested:
            requested_feature = self._features[requested_key]
            if requested_feature.state is FeatureState.READY:
                continue
            self._user_pending.add(requested_key)
            if requested_feature.state is FeatureState.FAILED:
                requested_feature.state = FeatureState.UNLOADED
                requested_feature.error = ""
            self._remove_queued(requested_key)
            self._queue.appendleft(requested_key)
        self._schedule(self._pump)

    def retry(self, key: str) -> None:
        self.request(key)

    def mark_ready(self, key: str) -> None:
        if self._active != key:
            return
        feature = self._features[key]
        feature.state = FeatureState.READY
        feature.error = ""
        self._user_pending.discard(key)
        self._active = None
        startup_profile.event(f"Feature ready: {key}")
        self.state_changed.emit(key, feature.state.value, "")
        self._schedule(self._pump)

    def mark_failed(self, key: str, error: BaseException | str) -> None:
        if self._active != key:
            return
        feature = self._features[key]
        feature.state = FeatureState.FAILED
        feature.error = str(error)
        self._user_pending.discard(key)
        self._active = None
        startup_profile.event(f"Feature failed: {key}")
        self.state_changed.emit(key, feature.state.value, feature.error)
        self._schedule(self._pump)

    def _enqueue(self, key: str) -> None:
        feature = self._features[key]
        if (
            feature.state is FeatureState.UNLOADED
            and key != self._active
            and key not in self._queue
        ):
            self._queue.append(key)

    def _remove_queued(self, key: str) -> None:
        self._queue = deque(value for value in self._queue if value != key)

    def _pump(self) -> None:
        if self._active is not None:
            return
        key = self._next_key()
        if key is None:
            return
        feature = self._features[key]
        if feature.state is not FeatureState.UNLOADED:
            self._schedule(self._pump)
            return
        failed_dependency = next(
            (
                self._features[dependency]
                for dependency in feature.dependencies
                if self._features[dependency].state is FeatureState.FAILED
            ),
            None,
        )
        if failed_dependency is not None:
            feature.state = FeatureState.FAILED
            feature.error = (
                f"dependency {failed_dependency.key} failed: {failed_dependency.error}"
            )
            self._user_pending.discard(key)
            self.state_changed.emit(key, feature.state.value, feature.error)
            self._schedule(self._pump)
            return
        pending_dependencies = [
            dependency
            for dependency in feature.dependencies
            if self._features[dependency].state is not FeatureState.READY
        ]
        if pending_dependencies:
            self._queue.append(key)
            for dependency in reversed(pending_dependencies):
                self._enqueue(dependency)
            self._schedule(self._pump)
            return
        self._active = key
        if QThread.currentThread() is not self.thread():
            self.mark_failed(key, "feature preparation must run on the GUI thread")
            return
        feature.state = FeatureState.LOADING
        feature.error = ""
        feature.attempts += 1
        self.state_changed.emit(key, feature.state.value, "")
        startup_profile.event(f"Feature loading: {key}")
        self._active_started_ns = startup_profile.now_ns()
        # Yield once so the page can paint its loading overlay before any
        # synchronous GUI-thread preparation begins.
        self._schedule(lambda key=key: self._run_prepare(key))

    def _run_prepare(self, key: str) -> None:
        if self._active != key:
            return
        feature = self._features[key]

        def done() -> None:
            startup_profile.duration(f"Feature prepare: {key}", self._active_started_ns)
            self.mark_ready(key)

        try:
            feature.prepare(done, lambda error: self.mark_failed(key, error))
        except Exception as exc:  # noqa: BLE001 - feature boundary
            self.mark_failed(key, exc)

    def _next_key(self) -> str | None:
        if self._user_pending:
            for key in tuple(self._queue):
                if key in self._user_pending:
                    self._remove_queued(key)
                    return key
            return None
        return self._queue.popleft() if self._queue else None


def synchronous_prepare(operation: Callable[[], None]) -> FeaturePrepare:
    def prepare(done: FeatureDone, _failed: FeatureFailed) -> None:
        operation()
        done()

    return prepare
