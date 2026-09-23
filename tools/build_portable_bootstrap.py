"""Build public catalogue ZIPs from read-only backups of configured databases."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from booruflow.application.bundled_database_bootstrap import (
    BOOTSTRAP_NAMES,
    sha256_file,
    validate_database,
)
from booruflow.application.database_paths import gelbooru_alias_database, gelbooru_tag_database
from booruflow.infrastructure.gelbooru_aliases import ALIAS_SCHEMA_VERSION
from booruflow.infrastructure.gelbooru_tag_importer import IMPORT_VERSION


def build(settings_path: Path, output: Path) -> dict:
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    sources = {
        "gelbooru_tags": gelbooru_tag_database(settings),
        "gelbooru_aliases": gelbooru_alias_database(settings),
    }
    if any(path is None or not path.is_file() for path in sources.values()):
        raise ValueError("Configured Gelbooru database is absent")
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"version": 1}
    with tempfile.TemporaryDirectory(prefix=".bootstrap-build-", dir=output) as staging_dir:
        staging = Path(staging_dir)
        for kind, source in sources.items():
            checkpoint = validate_database(source, kind)
            archive_name, member_name, _table = BOOTSTRAP_NAMES[kind]
            snapshot = staging / member_name
            # SQLite's online backup reads a consistent snapshot, including any
            # committed WAL pages, without modifying the selected source DB.
            with closing(sqlite3.connect(
                f"file:{source.resolve().as_posix()}?mode=ro", uri=True
            )) as original, closing(sqlite3.connect(snapshot)) as copy:
                original.backup(copy)
            validated_checkpoint = validate_database(snapshot, kind)
            if checkpoint != validated_checkpoint:
                raise ValueError(f"{kind} checkpoint changed during snapshot creation")
            archive = staging / archive_name
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6) as zipped:
                zipped.write(snapshot, member_name)
            entry = {
                "archive": archive_name,
                "target": f"data/databases/{'gelbooru_tags.db' if kind == 'gelbooru_tags' else 'gelbooru_aliases.db'}",
                "archive_sha256": sha256_file(archive),
                "database_sha256": sha256_file(snapshot),
                "compressed_size": archive.stat().st_size,
                "uncompressed_size": snapshot.stat().st_size,
                "schema_version": IMPORT_VERSION if kind == "gelbooru_tags" else ALIAS_SCHEMA_VERSION,
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            if kind == "gelbooru_tags":
                entry["after_id"] = validated_checkpoint
            manifest[kind] = entry
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        for filename in ("gelbooru-tags.zip", "gelbooru-aliases.zip", "manifest.json"):
            os.replace(staging / filename, output / filename)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build(args.settings, args.output)
    for kind in BOOTSTRAP_NAMES:
        entry = manifest[kind]
        print(f"{kind}: {entry['uncompressed_size']} -> {entry['compressed_size']} bytes")
    print("Bootstrap archives and manifest created")


if __name__ == "__main__":
    main()
