"""Read tag categories from the existing local tag catalogue."""

from __future__ import annotations

import sqlite3
from pathlib import Path

GELBOORU_CATEGORIES = {
    0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "metadata",
}


class LocalTagCategoryLookup:
    def __init__(self, database: Path) -> None:
        self.database = database

    def __call__(self, names: tuple[str, ...]) -> dict[str, str]:
        if not names or not self.database.is_file():
            return {}
        result: dict[str, str] = {}
        connection = sqlite3.connect(
            f"file:{self.database.resolve().as_posix()}?mode=ro", uri=True
        )
        try:
            for offset in range(0, len(names), 500):
                batch = names[offset : offset + 500]
                placeholders = ",".join("?" for _name in batch)
                for name, category in connection.execute(
                    f"SELECT name,category FROM tags WHERE name IN ({placeholders})", batch
                ):
                    result[str(name)] = GELBOORU_CATEGORIES.get(int(category), str(category))
        finally:
            connection.close()
        return result
