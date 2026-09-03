"""Persistent taxonomy document operations independent from a GUI toolkit."""

from __future__ import annotations

import copy
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from booruflow.infrastructure.taxonomy_database import TaxonomyDatabase


def default_document() -> dict:
    return {
        "version": 1,
        "boards": {"gelbooru": {}, "e621": {}},
        "metadata": {},
        "sources": [],
        "excluded_imported_tags": {},
    }


def iter_tag_paths(node, path: tuple[str, ...] = ()):
    if isinstance(node, list):
        for tag in node:
            yield str(tag), path
    elif isinstance(node, dict):
        if node.get("__tag__"):
            yield str(node["__tag__"]), path
        for tag in node.get("__tags__", []):
            yield str(tag), path
        for key, child in node.items():
            if not str(key).startswith("__"):
                child_path = path + (str(key),)
                if isinstance(child, dict) and not child:
                    # Historical catalogues stored canonical leaf tags as
                    # ``{"tag_name": {}}`` before the explicit __tag__ marker.
                    yield str(key), child_path
                else:
                    yield from iter_tag_paths(child, child_path)


class TaxonomyRepository:
    def __init__(self, path: Path, databases_directory: Path) -> None:
        self.path = path
        self.databases_directory = databases_directory

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict) and isinstance(data.get("boards"), dict):
                data.setdefault("metadata", {})
                data.setdefault("sources", [])
                data.setdefault("excluded_imported_tags", {})
                return data
        except (OSError, ValueError, TypeError):
            pass
        return default_document()

    def save(self, document: dict) -> Path | None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if self.path.is_file():
            backup = self.path.with_name(
                f"{self.path.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}.json"  # noqa: DTZ005
            )
            shutil.copy2(self.path, backup)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
        for board in ("gelbooru", "e621"):
            database = TaxonomyDatabase(
                self.databases_directory / f"tag_organization_{board}.sqlite", board
            )
            try:
                database.sync_from_document(
                    document.get("boards", {}).get(board, {}),
                    document.get("metadata", {}).get(board, {}),
                    document.get("excluded_imported_tags", {}).get(board, []),
                    document.get("sources", []),
                )
            finally:
                database.close()
        return backup

    @staticmethod
    def merged_preview(document: dict, imported: dict) -> tuple[dict, dict]:
        from booruflow.infrastructure.wiki_tag_importer import merge_catalogues

        preview = copy.deepcopy(document)
        summary = merge_catalogues(preview, imported)
        return preview, summary
