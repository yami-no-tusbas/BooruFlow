"""Portable catalogue installation uses only known local archives and targets."""

import hashlib
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest

from booruflow.application.bundled_database_bootstrap import (
    prepare_bundled_database,
    sha256_file,
    validate_database,
)
from booruflow.infrastructure.gelbooru_aliases import ensure_alias_schema
from booruflow.infrastructure.gelbooru_tag_importer import IMPORT_VERSION, update_database
from tools.build_portable_bootstrap import build


@pytest.fixture
def portable(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    tags = sources / "tags.sqlite"
    aliases = sources / "aliases.sqlite"
    with sqlite3.connect(tags) as db:
        db.executescript("""
            CREATE TABLE tags(id INTEGER PRIMARY KEY,name TEXT NOT NULL,
                              post_count INTEGER NOT NULL,category INTEGER NOT NULL,
                              ambiguous INTEGER NOT NULL);
            CREATE TABLE import_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO tags VALUES(42,'example',2,0,0);
        """)
        db.execute("INSERT INTO import_state VALUES('import_version',?)", (IMPORT_VERSION,))
        db.execute("INSERT INTO import_state VALUES('after_id','42')")
    ensure_alias_schema(aliases)
    with sqlite3.connect(aliases) as db:
        db.execute("INSERT INTO alias_sync_state VALUES('checkpoint','[1, 2, 3]')")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "gelbooru_tag_database": str(tags),
        "gelbooru_alias_database": str(aliases),
        "private_setting": "never-publish-this-value",
    }), encoding="utf-8")
    before = {path: (sha256_file(path), path.stat().st_mtime_ns) for path in (tags, aliases)}
    root = tmp_path / "portable"
    output = root / "bootstrap"
    manifest = build(settings, output)
    for path, (checksum, modified) in before.items():
        assert (sha256_file(path), path.stat().st_mtime_ns) == (checksum, modified)
    assert "never-publish-this-value" not in (output / "manifest.json").read_text()
    return root, manifest


@pytest.mark.parametrize("kind,filename,archive", [
    ("gelbooru_tags", "gelbooru_tags.db", "gelbooru-tags.zip"),
    ("gelbooru_aliases", "gelbooru_aliases.db", "gelbooru-aliases.zip"),
])
def test_install_and_retry_without_overwrite(portable, kind, filename, archive):
    root, manifest = portable
    destination = root / "data" / "databases" / filename
    leftover = Path(str(destination) + ".bootstrap.tmp")
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b"interrupted")
    progress = []
    result = prepare_bundled_database(root, kind, destination, progress=lambda *args: progress.append(args))
    assert result.state == "installed"
    assert progress and progress[-1][1] == manifest[kind]["uncompressed_size"]
    assert not leftover.exists()
    assert not (root / "bootstrap" / archive).exists()
    assert sha256_file(destination) == manifest[kind]["database_sha256"]
    assert validate_database(destination, kind) == manifest[kind].get("after_id")
    checksum = sha256_file(destination)
    assert prepare_bundled_database(root, kind, destination).state == "ready"
    assert sha256_file(destination) == checksum


def test_bad_archive_or_manifest_preserves_existing_and_cleans_partial(portable):
    root, manifest = portable
    destination = root / "data" / "databases" / "gelbooru_tags.db"
    archive = root / "bootstrap" / "gelbooru-tags.zip"
    archive.write_bytes(b"corrupted")
    assert prepare_bundled_database(root, "gelbooru_tags", destination).state == "invalid"
    assert not destination.exists()
    assert not Path(str(destination) + ".bootstrap.tmp").exists()
    assert archive.exists()
    manifest["gelbooru_tags"]["target"] = "../escape.db"
    (root / "bootstrap" / "manifest.json").write_text(json.dumps(manifest))
    assert prepare_bundled_database(root, "gelbooru_tags", destination).state == "invalid"
    assert not destination.exists()


def test_invalid_existing_database_is_never_overwritten(portable):
    root, _ = portable
    destination = root / "data" / "databases" / "gelbooru_tags.db"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"my-existing-file")
    assert prepare_bundled_database(root, "gelbooru_tags", destination).state == "invalid"
    assert destination.read_bytes() == b"my-existing-file"
    assert (root / "bootstrap" / "gelbooru-tags.zip").exists()


def test_valid_existing_database_is_left_alone_even_with_archive(portable):
    root, _ = portable
    destination = root / "data" / "databases" / "gelbooru_tags.db"
    destination.parent.mkdir(parents=True)
    shutil.copyfile(root.parent / "sources" / "tags.sqlite", destination)
    checksum = sha256_file(destination)
    archive = root / "bootstrap" / "gelbooru-tags.zip"
    assert prepare_bundled_database(root, "gelbooru_tags", destination).state == "ready"
    assert sha256_file(destination) == checksum and archive.exists()


def test_corrupt_sqlite_with_matching_checksums_is_rejected(portable):
    root, manifest = portable
    archive = root / "bootstrap" / "gelbooru-tags.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("tags.db", b"not sqlite")
    entry = manifest["gelbooru_tags"]
    entry.update(archive_sha256=sha256_file(archive), compressed_size=archive.stat().st_size,
                 database_sha256=hashlib.sha256(b"not sqlite").hexdigest(),
                 uncompressed_size=10)
    (root / "bootstrap" / "manifest.json").write_text(json.dumps(manifest))
    destination = root / "data" / "databases" / "gelbooru_tags.db"
    assert prepare_bundled_database(root, "gelbooru_tags", destination).state == "invalid"
    assert not destination.exists()
    assert not Path(str(destination) + ".bootstrap.tmp").exists()


def test_unlink_warning_does_not_undo_valid_install(portable, monkeypatch):
    root, _ = portable
    destination = root / "data" / "databases" / "gelbooru_tags.db"
    archive = root / "bootstrap" / "gelbooru-tags.zip"
    original_unlink = Path.unlink

    def refuse_archive(self, *args, **kwargs):
        if self == archive:
            raise PermissionError("archive is locked")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse_archive)
    result = prepare_bundled_database(root, "gelbooru_tags", destination)
    assert result.state == "installed" and "archive is locked" in result.warning
    assert validate_database(destination, "gelbooru_tags") == 42
    assert archive.exists()


def test_installed_checkpoint_is_used_by_incremental_update(portable):
    root, _ = portable
    destination = root / "data" / "databases" / "gelbooru_tags.db"
    assert prepare_bundled_database(root, "gelbooru_tags", destination).state == "installed"
    calls = []
    update_database(destination, "1", "test-key", fetcher=lambda cursor, *_: calls.append(cursor) or [],
                    progress=lambda _line: None, retries=0)
    assert calls and calls[0] == 42
