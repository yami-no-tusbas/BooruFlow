"""Stockage relationnel de la taxonomie, dans une base distincte par board."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class TaxonomyDatabase:
    def __init__(self, path: Path, board: str) -> None:
        self.path = path
        self.board = board
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS categories(
                path TEXT PRIMARY KEY,
                parent_path TEXT,
                label TEXT NOT NULL,
                selectable_tag TEXT,
                source TEXT NOT NULL DEFAULT 'local'
            );
            CREATE TABLE IF NOT EXISTS tags(
                name TEXT PRIMARY KEY,
                definition TEXT,
                wiki_url TEXT,
                locally_excluded INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS memberships(
                category_path TEXT NOT NULL REFERENCES categories(path) ON DELETE CASCADE,
                tag_name TEXT NOT NULL REFERENCES tags(name) ON DELETE CASCADE,
                source TEXT NOT NULL DEFAULT 'local',
                PRIMARY KEY(category_path, tag_name)
            );
            CREATE TABLE IF NOT EXISTS sources(
                url TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_memberships_tag ON memberships(tag_name);
            """
        )
        self.connection.commit()

    def sync_from_document(
        self, tree: dict, metadata: dict, exclusions: list[str], sources: list[dict]
    ) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM memberships")
            self.connection.execute("DELETE FROM categories")
            self.connection.execute("DELETE FROM sources")
            for tag, values in metadata.items():
                self.connection.execute(
                    """INSERT INTO tags(name,definition,wiki_url) VALUES(?,?,?)
                       ON CONFLICT(name) DO UPDATE SET
                         definition=COALESCE(excluded.definition,tags.definition),
                         wiki_url=COALESCE(excluded.wiki_url,tags.wiki_url)""",
                    (tag, values.get("definition"), values.get("wiki_url")),
                )
            excluded = set(exclusions)
            self.connection.execute("UPDATE tags SET locally_excluded=0")
            for tag in excluded:
                self.connection.execute(
                    """INSERT INTO tags(name,locally_excluded) VALUES(?,1)
                       ON CONFLICT(name) DO UPDATE SET locally_excluded=1""",
                    (tag,),
                )

            def walk(node, path: list[str]) -> None:
                if isinstance(node, list):
                    category_path = json.dumps(path, ensure_ascii=False)
                    for tag in node:
                        self.connection.execute(
                            "INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag,)
                        )
                        self.connection.execute(
                            "INSERT OR REPLACE INTO memberships(category_path,tag_name,source) VALUES(?,?,?)",
                            (category_path, tag, "wiki" if "From wiki" in path else "local"),
                        )
                    return
                if isinstance(node, dict) and node.get("__tags__") and path:
                    category_path = json.dumps(path, ensure_ascii=False)
                    for tag in node["__tags__"]:
                        self.connection.execute(
                            "INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag,)
                        )
                        self.connection.execute(
                            "INSERT OR REPLACE INTO memberships(category_path,tag_name,source) VALUES(?,?,?)",
                            (category_path, tag, "local"),
                        )
                if isinstance(node, dict) and "__tag__" in node and path:
                    category_path = json.dumps(path, ensure_ascii=False)
                    tag = str(node["__tag__"])
                    self.connection.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag,))
                    self.connection.execute(
                        "INSERT OR REPLACE INTO memberships(category_path,tag_name,source) VALUES(?,?,?)",
                        (category_path, tag, "wiki"),
                    )
                for label, child in node.items():
                    if label in {"__tag__", "__tags__", "__manual__"}:
                        continue
                    child_path = path + [label]
                    encoded = json.dumps(child_path, ensure_ascii=False)
                    parent = json.dumps(path, ensure_ascii=False) if path else None
                    self.connection.execute(
                        "INSERT OR REPLACE INTO categories(path,parent_path,label,source) VALUES(?,?,?,?)",
                        (encoded, parent, label, "wiki" if "From wiki" in child_path else "local"),
                    )
                    walk(child, child_path)

            walk(tree, [])
            for source in sources:
                if source.get("board") == self.board:
                    self.connection.execute(
                        "INSERT OR REPLACE INTO sources(url,label,updated_at) VALUES(?,?,?)",
                        (source.get("url", ""), source.get("label", ""), source.get("updated_at")),
                    )

    def integrity(self) -> str:
        return str(self.connection.execute("PRAGMA integrity_check").fetchone()[0])

    def close(self) -> None:
        self.connection.close()
