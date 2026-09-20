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
    state: str = "all"
    alias: str = "all"
    limit: int = 1_000


@dataclass(frozen=True, slots=True)
class TagRow:
    id: int
    name: str
    post_count: int
    category: int
    ambiguous: int
    direct_alias: str | None = None
    canonical_name: str | None = None
    alias_diagnostic: str | None = None


def _regexp(pattern: str, value: object) -> int:
    try:
        return int(re.search(pattern, str(value or ""), re.IGNORECASE) is not None)
    except re.error:
        return 0


def _alias_catalog_available(alias_database: Path | None) -> bool:
    if alias_database is None or not alias_database.is_file():
        return False
    try:
        connection = sqlite3.connect(f"file:{alias_database.resolve().as_posix()}?mode=ro", uri=True)
        try:
            return connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gelbooru_aliases'"
            ).fetchone() is not None
        finally:
            connection.close()
    except sqlite3.Error:
        return False


def _batch_aliases(
    database: Path, alias_database: Path | None, rows: list[TagRow], maximum_depth: int = 16
) -> list[TagRow]:
    """Resolve a result batch in bounded SQL rounds, never one query per row."""
    if not rows or not _alias_catalog_available(alias_database):
        return rows
    assert alias_database is not None
    names = {row.name.casefold() for row in rows}
    aliases: dict[str, str] = {}
    frontier = set(names)
    connection = sqlite3.connect(f"file:{alias_database.resolve().as_posix()}?mode=ro", uri=True)
    try:
        for _depth in range(maximum_depth):
            pending = frontier - aliases.keys()
            if not pending:
                break
            frontier = set()
            for offset in range(0, len(pending), 500):
                chunk = list(pending)[offset : offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                for source, target in connection.execute(
                    "SELECT source_name,target_name FROM gelbooru_aliases "
                    f"WHERE status='active' AND source_name IN ({placeholders})", chunk
                ):
                    source_name, target_name = str(source).casefold(), str(target).casefold()
                    aliases[source_name] = target_name
                    frontier.add(target_name)
    finally:
        connection.close()

    target_names = set(aliases.values())
    existing: set[str] = set()
    if target_names:
        tags = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
        try:
            for offset in range(0, len(target_names), 500):
                chunk = list(target_names)[offset : offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                existing.update(
                    str(item[0]).casefold() for item in tags.execute(
                        f"SELECT name FROM tags WHERE name IN ({placeholders})", chunk
                    )
                )
        finally:
            tags.close()

    enriched: list[TagRow] = []
    for row in rows:
        original = row.name.casefold()
        direct = aliases.get(original)
        current = original
        visited: set[str] = set()
        diagnostic = None
        for _depth in range(maximum_depth):
            if current in visited:
                diagnostic = "cycle"
                break
            visited.add(current)
            target = aliases.get(current)
            if target is None:
                break
            if target not in existing:
                diagnostic = "missing_target"
                break
            current = target
        else:
            diagnostic = "maximum_depth"
        canonical = current if direct and diagnostic is None and current != original else None
        enriched.append(TagRow(
            row.id, row.name, row.post_count, row.category, row.ambiguous,
            direct if canonical else None, canonical, diagnostic,
        ))
    return enriched


def search_tags(
    database: Path,
    request: TagSearch,
    *,
    site: str = "gelbooru",
    alias_database: Path | None = None,
) -> list[TagRow]:
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
        alias_available = site == "gelbooru" and _alias_catalog_available(alias_database)
        if alias_available:
            assert alias_database is not None
            connection.execute(
                "ATTACH DATABASE ? AS aliases", (f"file:{alias_database.resolve().as_posix()}?mode=ro",)
            )
        alias_exists = (
            "EXISTS(SELECT 1 FROM aliases.gelbooru_aliases a "
            "WHERE a.status='active' AND a.source_name=tags.name COLLATE NOCASE)"
        )
        if request.alias in {"with", "without"} and alias_available:
            clauses.append(alias_exists if request.alias == "with" else f"NOT {alias_exists}")
        elif request.alias == "with":
            clauses.append("0")
        if request.state == "alias":
            clauses.append(alias_exists if alias_available else "0")
        elif request.state == "deprecated":
            clauses.append("category=6" if site == "gelbooru" else "0")
        elif request.state == "ambiguous":
            clauses.append(f"{ambiguous_column}=1")
        elif request.state == "canonical":
            clauses.append(f"{ambiguous_column}=0")
            if site == "gelbooru":
                clauses.append("category<>6")
            if alias_available:
                clauses.append(f"NOT {alias_exists}")
        limit = max(1, min(int(request.limit), 25_000))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT id,name,post_count,category,{ambiguous_column} AS ambiguous FROM tags"
            f"{where} ORDER BY post_count DESC, name COLLATE NOCASE LIMIT ?"
        )
        values.append(limit)
        connection.create_function("REGEXP", 2, _regexp)
        rows = [TagRow(*row) for row in connection.execute(sql, values)]
        return _batch_aliases(database, alias_database if site == "gelbooru" else None, rows)
    finally:
        connection.close()


def exact_tags(database: Path, names: list[str]) -> list[TagRow]:
    """Fetch several exact tag names with one read-only SQLite round trip."""
    normalized = list(dict.fromkeys(str(name).strip() for name in names if str(name).strip()))
    if not normalized:
        return []
    if not database.is_file():
        raise FileNotFoundError(database)
    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    try:
        columns = {str(row[1]).casefold() for row in connection.execute("PRAGMA table_info(tags)")}
        required = {"id", "name", "post_count", "category"}
        if not required.issubset(columns):
            raise ValueError(f"Unsupported tags schema; missing: {', '.join(sorted(required - columns))}")
        ambiguous_column = "ambiguous" if "ambiguous" in columns else "0"
        rows: list[TagRow] = []
        for offset in range(0, len(normalized), 500):
            chunk = normalized[offset : offset + 500]
            placeholders = ",".join("?" for _name in chunk)
            sql = (
                f"SELECT id,name,post_count,category,{ambiguous_column} AS ambiguous "
                f"FROM tags WHERE name COLLATE NOCASE IN ({placeholders})"
            )
            rows.extend(TagRow(*row) for row in connection.execute(sql, chunk))
        return rows
    finally:
        connection.close()


def enrich_tag_rows(
    database: Path, alias_database: Path | None, rows: list[TagRow]
) -> list[TagRow]:
    """Public batched alias enrichment for other Gelbooru read-only tools."""
    return _batch_aliases(database, alias_database, rows)
