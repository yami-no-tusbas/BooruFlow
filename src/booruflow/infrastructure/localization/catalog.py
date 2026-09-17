"""Simple external JSON language catalogs with English fallback."""

from __future__ import annotations

import json
from pathlib import Path


class LanguageCatalog:
    def __init__(self, directory: Path, language: str = "en") -> None:
        self.directory = directory
        self.available = self.discover()
        self._english = self._read("en")
        self.code = "en"
        self._strings: dict[str, str] = {}
        self.set_language(language)

    def discover(self) -> dict[str, str]:
        languages: dict[str, str] = {}
        if self.directory.is_dir():
            for path in sorted(self.directory.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8-sig"))
                    metadata = data.get("_meta", {}) if isinstance(data, dict) else {}
                    name = str(metadata.get("name", path.stem)).strip()
                    if name:
                        languages[path.stem] = name
                except (OSError, ValueError, TypeError):
                    continue
        languages.setdefault("en", "English")
        return languages

    def _read(self, code: str) -> dict[str, str]:
        try:
            data = json.loads((self.directory / f"{code}.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in data.items()
            if key != "_meta" and isinstance(value, str)
        }

    def set_language(self, code: str) -> str:
        self.code = code if code in self.available else "en"
        self._strings = self._read(self.code)
        return self.code

    def text(self, key: str, **values: object) -> str:
        template = self._strings.get(key, self._english.get(key, key))
        try:
            return template.format(**values)
        except (KeyError, ValueError):
            return template
