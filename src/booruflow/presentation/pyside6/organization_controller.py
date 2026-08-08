"""Workers for taxonomy persistence and authoritative wiki updates."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from booruflow.application.taxonomy import TaxonomyRepository


class TaxonomySaveWorker(QThread):
    completed = Signal(str, str)

    def __init__(self, repository: TaxonomyRepository, document: dict) -> None:
        super().__init__(); self.repository = repository; self.document = document

    def run(self) -> None:
        try:
            backup = self.repository.save(self.document)
            self.completed.emit(str(backup or ""), "")
        except Exception as exc:
            self.completed.emit("", str(exc))


class WikiImportWorker(QThread):
    progress = Signal(str)
    completed = Signal(object, object, str)

    def __init__(self, repository: TaxonomyRepository, document: dict) -> None:
        super().__init__(); self.repository = repository; self.document = document

    def run(self) -> None:
        try:
            from legacy.wiki_tag_importer import import_catalogues
            imported = import_catalogues(progress=self.progress.emit)
            preview, summary = self.repository.merged_preview(self.document, imported)
            self.completed.emit(preview, summary, "")
        except Exception as exc:
            self.completed.emit({}, {}, str(exc))


class TagDetailsWorker(QThread):
    completed = Signal(int, object)

    def __init__(
        self, generation: int, board: str, tag: str, cache_path,
        user_id: str = "", api_key: str = "",
    ) -> None:
        super().__init__()
        self.generation = generation; self.board = board; self.tag = tag
        self.cache_path = cache_path; self.user_id = user_id; self.api_key = api_key

    def run(self) -> None:
        from booruflow.infrastructure.tag_details import fetch_tag_details

        details = fetch_tag_details(
            self.board, self.tag, self.cache_path, self.user_id, self.api_key
        )
        self.completed.emit(self.generation, details)
