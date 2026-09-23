"""Install the two optional, local portable catalogues without touching existing DBs."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import zipfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from booruflow.infrastructure.gelbooru_aliases import ALIAS_SCHEMA_VERSION
from booruflow.infrastructure.gelbooru_tag_importer import IMPORT_VERSION

BOOTSTRAP_NAMES = {
    "gelbooru_tags": ("gelbooru-tags.zip", "tags.db", "tags"),
    "gelbooru_aliases": ("gelbooru-aliases.zip", "aliases.db", "gelbooru_aliases"),
}
Progress = Callable[[str, int, int], None]


@dataclass(frozen=True)
class BootstrapResult:
    state: str  # ready, installed, unavailable, invalid
    detail: str = ""
    warning: str = ""


def validate_database(path: Path, kind: str, *, checkpoint: int | None = None) -> int | None:
    """Read-only integrity, schema, and checkpoint validation."""
    if kind not in BOOTSTRAP_NAMES:
        raise ValueError("Unknown bootstrap database")
    if not path.is_file():
        raise ValueError("Database is absent")
    _archive, _member, table = BOOTSTRAP_NAMES[kind]
    with closing(sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        state_table = "import_state" if kind == "gelbooru_tags" else "alias_sync_state"
        if not {table, state_table} <= tables:
            raise ValueError("Database schema is incomplete")
        columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        expected = (
            {"id", "name", "post_count", "category", "ambiguous"}
            if kind == "gelbooru_tags" else
            {"source_name", "target_name", "status", "first_seen_at", "observed_at"}
        )
        if not expected <= columns:
            raise ValueError("Database columns are incompatible")
        version_key = "import_version" if kind == "gelbooru_tags" else "schema_version"
        version = db.execute(
            f"SELECT value FROM {state_table} WHERE key=?", (version_key,)
        ).fetchone()
        required_version = IMPORT_VERSION if kind == "gelbooru_tags" else ALIAS_SCHEMA_VERSION
        if version is None or version[0] != required_version:
            raise ValueError("Database schema version is incompatible")
        if kind == "gelbooru_tags":
            row = db.execute("SELECT value FROM import_state WHERE key='after_id'").fetchone()
            maximum = db.execute("SELECT COALESCE(MAX(id),0) FROM tags").fetchone()[0]
            try:
                after_id = int(row[0]) if row else 0
            except ValueError as exc:
                raise ValueError("Database checkpoint is invalid") from exc
            if after_id <= 0 or after_id < maximum or (
                checkpoint is not None and after_id != checkpoint
            ):
                raise ValueError("Database checkpoint is invalid")
            return after_id
        row = db.execute(
            "SELECT value FROM alias_sync_state WHERE key='checkpoint'"
        ).fetchone()
        try:
            relation_checkpoint = json.loads(row[0]) if row else None
        except json.JSONDecodeError as exc:
            raise ValueError("Alias checkpoint is invalid") from exc
        if not isinstance(relation_checkpoint, list) or len(relation_checkpoint) < 2:
            raise ValueError("Alias checkpoint is missing")
        return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_bundled_database(
    application_root: Path, kind: str, destination: Path,
    progress: Progress | None = None,
) -> BootstrapResult:
    """Install an absent catalogue; an invalid existing DB is never replaced."""
    if kind not in BOOTSTRAP_NAMES:
        raise ValueError("Unknown bootstrap database")
    temporary = Path(str(destination) + ".bootstrap.tmp")
    try:
        temporary.unlink(missing_ok=True)
    except OSError as exc:
        return BootstrapResult("invalid", f"Cannot clean interrupted bootstrap: {exc}")
    if destination.exists():
        try:
            validate_database(destination, kind)
        except (OSError, sqlite3.Error, ValueError) as exc:
            return BootstrapResult("invalid", str(exc))
        return BootstrapResult("ready")

    archive_name, member_name, _table = BOOTSTRAP_NAMES[kind]
    archive_dir = application_root / "bootstrap"
    archive = archive_dir / archive_name
    manifest_path = archive_dir / "manifest.json"
    if not archive.is_file() or not manifest_path.is_file():
        return BootstrapResult("unavailable", "Bundled snapshot is absent")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("version") != 1:
            raise ValueError("Unsupported bootstrap manifest")
        entry = manifest[kind]
        expected_target = application_root / Path(entry["target"])
        if Path(entry["target"]).is_absolute() or ".." in Path(entry["target"]).parts:
            raise ValueError("Unsafe bootstrap target")
        if expected_target.resolve() != destination.resolve():
            raise ValueError("Bootstrap target differs from configured database")
        if entry["archive"] != archive_name:
            raise ValueError("Unexpected bootstrap archive name")
        required_version = IMPORT_VERSION if kind == "gelbooru_tags" else ALIAS_SCHEMA_VERSION
        if entry.get("schema_version") != required_version:
            raise ValueError("Bootstrap schema version mismatch")
        if archive.stat().st_size != entry["compressed_size"]:
            raise ValueError("Bootstrap archive size mismatch")
        if sha256_file(archive) != entry["archive_sha256"]:
            raise ValueError("Bootstrap archive SHA256 mismatch")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # This exact filename is owned by bootstrap. A previous interrupted run
        # was removed above; never remove any other adjacent file.
        try:
            with zipfile.ZipFile(archive) as zipped:
                members = zipped.infolist()
                if len(members) != 1 or members[0].filename != member_name:
                    raise ValueError("Bootstrap ZIP must contain one expected database")
                if members[0].is_dir() or members[0].file_size != entry["uncompressed_size"]:
                    raise ValueError("Bootstrap database size mismatch")
                digest = hashlib.sha256()
                extracted = 0
                with zipped.open(members[0]) as source, temporary.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        extracted += len(chunk)
                        if extracted > entry["uncompressed_size"]:
                            raise ValueError("Bootstrap database exceeds expected size")
                        digest.update(chunk)
                        output.write(chunk)
                        if progress is not None:
                            progress(kind, extracted, entry["uncompressed_size"])
            if extracted != entry["uncompressed_size"] or digest.hexdigest() != entry["database_sha256"]:
                raise ValueError("Bootstrap database SHA256 mismatch")
            checkpoint = entry.get("after_id") if kind == "gelbooru_tags" else None
            if kind == "gelbooru_tags" and not isinstance(checkpoint, int):
                raise ValueError("Bootstrap checkpoint is missing")
            validate_database(temporary, kind, checkpoint=checkpoint)
            if destination.exists():
                raise ValueError("Database appeared during bootstrap; refusing replacement")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        return BootstrapResult("invalid", str(exc))

    try:
        archive.unlink()
    except OSError as exc:
        return BootstrapResult("installed", warning=f"Cannot remove bootstrap archive: {exc}")
    return BootstrapResult("installed")
