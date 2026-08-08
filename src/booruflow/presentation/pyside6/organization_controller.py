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
