"""Qt worker for Gelbooru tagging review."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from booruflow.application.tagging import TaggingRequest
from booruflow.infrastructure.gelbooru_tagging import GelbooruTaggingScanner


class TaggingWorker(QThread):
    progress = Signal(int, int, int, int, int)
    completed = Signal(list, int, int, bool, str, bool)

    def __init__(self, request: TaggingRequest, user_id: str, api_key: str) -> None:
        super().__init__()
        self.request = request
        self.user_id = user_id
        self.api_key = api_key

    def run(self) -> None:
        try:
            posts, examined, next_page, reached_end = GelbooruTaggingScanner().scan(
                self.request,
                self.user_id,
                self.api_key,
                cancelled=self.isInterruptionRequested,
                progress=lambda *values: self.progress.emit(*values),
            )
            self.completed.emit(
                posts, examined, next_page, reached_end, "", self.isInterruptionRequested()
            )
        except Exception as exc:
            self.completed.emit([], 0, self.request.start_page, False, str(exc), False)
