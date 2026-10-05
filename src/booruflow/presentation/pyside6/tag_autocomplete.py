"""Qt orchestration for isolated, stale-safe tag autocomplete requests."""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from booruflow.application.tag_autocomplete import (
    AutocompleteRequest,
    TagAutocompleteService,
    TagSuggestion,
)


class TagAutocompleteWorker(QThread):
    completed = Signal(object, object)
    failed = Signal(object, str)

    def __init__(self, service: TagAutocompleteService, request: AutocompleteRequest) -> None:
        super().__init__()
        self.service = service
        self.request = request

    def run(self) -> None:
        try:
            self.completed.emit(self.request, self.service.suggestions(self.request))
        except Exception as exc:  # noqa: BLE001 - noncritical GUI boundary
            self.failed.emit(self.request, str(exc))


class TagAutocompleteCoordinator(QObject):
    suggestions_ready = Signal(str, object, object)
    lookup_failed = Signal(str, object, str)

    def __init__(self, service: TagAutocompleteService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._state: dict[str, tuple[int, str]] = {}
        self._workers: set[TagAutocompleteWorker] = set()

    def request(
        self,
        owner: str,
        site: str,
        text: str,
        *,
        category_filter: str | None = None,
        limit: int = 20,
    ) -> AutocompleteRequest:
        generation = self._state.get(owner, (0, site))[0] + 1
        self._state[owner] = (generation, site)
        request = AutocompleteRequest(owner, generation, site, text, category_filter, limit)
        worker = TagAutocompleteWorker(self.service, request)
        self._workers.add(worker)
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.finished.connect(lambda current=worker: self._workers.discard(current))
        worker.start()
        return request

    def invalidate(self, owner: str, site: str | None = None) -> None:
        generation, current_site = self._state.get(owner, (0, site or ""))
        self._state[owner] = (generation + 1, site or current_site)

    def _is_current(self, request: AutocompleteRequest) -> bool:
        return self._state.get(request.owner) == (request.generation, request.site)

    def _completed(
        self, request: AutocompleteRequest, suggestions: list[TagSuggestion]
    ) -> None:
        if self._is_current(request):
            self.suggestions_ready.emit(request.owner, request, suggestions)

    def _failed(self, request: AutocompleteRequest, message: str) -> None:
        if self._is_current(request):
            self.lookup_failed.emit(request.owner, request, message)

    def shutdown(self) -> None:
        for worker in tuple(self._workers):
            worker.wait()
