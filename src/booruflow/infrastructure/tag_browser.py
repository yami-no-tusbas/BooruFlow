"""Read-only, bounded searches over a local Booru tag database."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TagSearch:
    text: str = ""
    mode: str = "contains"
    category: int | None = None
    minimum_count: int | None = None
    maximum_count: int | None = None
    ambiguous: int | None = None
    limit: int = 1_000


@dataclass(frozen=True, slots=True)
class TagRow:
    id: int
    name: str
    post_count: int
    category: int
    ambiguous: int


def _regexp(pattern: str, value: object) -> int:
    try:
        return int(re.search(pattern, str(value or ""), re.IGNORECASE) is not None)
    except re.error:
        return 0


def search_tags(database: Path, request: TagSearch) -> list[TagRow]:
    """Search the tags table without ever opening the database for writing."""
    if not database.is_file():
        raise FileNotFoundError(database)
    if request.mode not in {"auto", "contains", "glob", "regex", "exact"}:
        raise ValueError(f"Unsupported search mode: {request.mode}")
    mode = request.mode
    if mode == "auto":
        mode = "glob" if any(token in request.text for token in "*?") else "contains"
    if mode == "regex" and request.text:
        re.compile(request.text, re.IGNORECASE)

    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    try:
        columns = {str(row[1]).casefold() for row in connection.execute("PRAGMA table_info(tags)")}
        required = {"id", "name", "post_count", "category"}
        if not required.issubset(columns):
            raise ValueError(f"Unsupported tags schema; missing: {', '.join(sorted(required - columns))}")
        ambiguous_column = "ambiguous" if "ambiguous" in columns else "0"
        clauses: list[str] = []
        values: list[object] = []
        if request.text:
            if mode == "contains":
                clauses.append("name LIKE ? ESCAPE '\\' COLLATE NOCASE")
                escaped = request.text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                values.append(f"%{escaped}%")
            elif mode == "glob":
                # Booru canonical tag names are lowercase. Avoid lower(name), which
                # forces an expensive Python/SQLite transformation over every row.
                clauses.append("name GLOB ?")
                values.append(request.text.casefold())
            elif mode == "regex":
                clauses.append("name REGEXP ?")
                values.append(request.text)
            else:
                clauses.append("name = ? COLLATE NOCASE")
                values.append(request.text)
        for column, value, operator in (
            ("category", request.category, "="),
            ("post_count", request.minimum_count, ">="),
            ("post_count", request.maximum_count, "<="),
            (ambiguous_column, request.ambiguous, "="),
        ):
            if value is not None:
                clauses.append(f"{column} {operator} ?")
                values.append(value)
        limit = max(1, min(int(request.limit), 25_000))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT id,name,post_count,category,{ambiguous_column} AS ambiguous FROM tags"
            f"{where} ORDER BY post_count DESC, name COLLATE NOCASE LIMIT ?"
        )
        values.append(limit)
        connection.create_function("REGEXP", 2, _regexp)
        return [TagRow(*row) for row in connection.execute(sql, values)]
    finally:
        connection.close()
