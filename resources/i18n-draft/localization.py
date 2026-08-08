"""Small JSON-based translation loader for the desktop interface."""

from __future__ import annotations

import json
from pathlib import Path


class LanguageCatalog:
    def __init__(self, directory: Path, language: str = "en") -> None:
        self.directory = directory
        self.available = self.discover()
        self.code = language if language in self.available else "en"
        self.strings = self._read(self.code)
        self.english = self._read("en")
        self.literals = self._read_literals(self.code)
        self.english_literals = self._read_literals("en")

    def discover(self) -> dict[str, str]:
        result: dict[str, str] = {}
        if not self.directory.is_dir():
            return {"en": "English"}
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict):
                    continue
                meta = data.get("_meta", {})
                name = str(meta.get("name", path.stem)).strip()
                if name:
                    result[path.stem] = name
            except (OSError, ValueError, TypeError):
                continue
        result.setdefault("en", "English")
        return result

    def _read(self, code: str) -> dict[str, str]:
        path = self.directory / f"{code}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            return {
                str(key): str(value)
                for key, value in data.items()
                if key != "_meta" and isinstance(value, str)
            }
        except (OSError, ValueError, TypeError):
            return {}

    def _read_literals(self, code: str) -> dict[str, str]:
        try:
            data = json.loads(
                (self.directory / f"{code}.json").read_text(encoding="utf-8-sig")
            )
            values = data.get("_literals", {})
            return {
                str(key): str(value)
                for key, value in values.items()
                if isinstance(value, str)
            } if isinstance(values, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def text(self, key: str, **values: object) -> str:
        template = self.strings.get(key, self.english.get(key, key))
        try:
            return template.format(**values)
        except (KeyError, ValueError):
            return template

    def literal(self, value: str) -> str:
        return self.literals.get(value, self.english_literals.get(value, value))
