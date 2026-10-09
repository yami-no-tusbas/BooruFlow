"""Simulate legacy HTML repairs on SQLite snapshots; --apply backs up before writing."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from booruflow.application.database_paths import gelbooru_tag_database
from booruflow.infrastructure.gelbooru_html_migration import (
    migrate_entity_profiles,
    migrate_image_source_cache,
    migrate_metadata_cache,
)
from booruflow.infrastructure.gelbooru_normalization import decode_tag

DATABASES = {
    "entity_profiles": ("var/state/entity_profiles.sqlite", migrate_entity_profiles),
    "post_metadata": ("var/cache/post_metadata.sqlite", migrate_metadata_cache),
    "image_analysis": ("var/state/image_analysis.sqlite", migrate_image_source_cache),
}


def _connect(path, *, readonly=False):
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30) if readonly else sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _snapshot(source, destination):
    if destination.exists():
        raise FileExistsError(destination)
    reader = _connect(source, readonly=True)
    writer = _connect(destination)
    try:
        reader.backup(writer)
    finally:
        reader.close(); writer.close()


def _stats(connection, name):
    columns = {
        "entity_profiles": [("profile_post_tags", "tag", "1"), ("recurring_tags", "tag", "1"),
                            ("profiles", "entity_tag", "1"), ("profiles", "query", "1")],
        "post_metadata": [("remote_post_tags", "tag", "site='gelbooru'")],
        "image_analysis": [("source_tags", "tag_name", "site='gelbooru'"),
                           ("post_metadata_cache", "tags_json", "site='gelbooru'")],
    }[name]
    result = {}
    for table, column, where in columns:
        result[f"{table}.{column}.escaped"] = sum(decode_tag(value) != value for (value,) in
            connection.execute(f"SELECT {column} FROM {table} WHERE {where} AND {column} LIKE '%&%'"))
        result[f"{table}.rows"] = connection.execute(f"SELECT count(*) FROM {table} WHERE {where}").fetchone()[0]
    if name == "entity_profiles":
        result["samples"] = dict(connection.execute("SELECT id,sample_count FROM profiles"))
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='gelbooru_html_conflicts'").fetchone():
        result["decision_conflicts"] = connection.execute("SELECT count(*) FROM gelbooru_html_conflicts").fetchone()[0]
        result["archived_rows"] = connection.execute("SELECT count(*) FROM gelbooru_html_archive").fetchone()[0]
    return result


def _repair(path, name, migration, catalogue):
    connection = _connect(path)
    try:
        before = _stats(connection, name)
        migration(connection, catalogue)
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise RuntimeError(f"Integrity check failed: {name}")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError(f"Foreign-key check failed: {name}")
        return {"before": before, "after": _stats(connection, name)}
    finally:
        connection.close()


def repair(root: Path, *, apply=False):
    root = root.resolve()
    settings_path = root / "config/booruflow_settings.json"
    if not settings_path.is_file():
        settings_path = root / "config/gelbooru_tagging_settings.json"
    settings = json.loads(settings_path.read_text("utf-8")) if settings_path.is_file() else {}
    catalogue = gelbooru_tag_database(settings)
    if catalogue is not None and not catalogue.is_absolute():
        catalogue = root / catalogue
    temporary_root = root / "var/tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    selected = {name: (root / relative, migration) for name, (relative, migration) in DATABASES.items()
                if (root / relative).is_file()}
    report = {"mode": "apply" if apply else "dry-run", "simulation": {}}
    with TemporaryDirectory(prefix="gelbooru-html-", dir=temporary_root) as directory:
        temporary = Path(directory).resolve()
        if not temporary.is_relative_to(temporary_root.resolve()):
            raise RuntimeError("Temporary repair directory escaped its workspace")
        for name, (source, migration) in selected.items():
            clone = temporary / (name + ".sqlite")
            _snapshot(source, clone)
            report["simulation"][name] = _repair(clone, name, migration, catalogue)
    if apply:
        backup = root / "var/backups/gelbooru-html" / (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
        backup.mkdir(parents=True, exist_ok=False)
        for name, (source, _migration) in selected.items():
            _snapshot(source, backup / (name + ".sqlite"))
        report["backup_directory"] = str(backup)
        report["applied"] = {name: _repair(source, name, migration, catalogue)
                             for name, (source, migration) in selected.items()}
    report_path = temporary_root / ("gelbooru-html-repair-applied.json" if apply else "gelbooru-html-repair-dry-run.json")
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    print(json.dumps(repair(arguments.root, apply=arguments.apply), indent=2, ensure_ascii=False))
