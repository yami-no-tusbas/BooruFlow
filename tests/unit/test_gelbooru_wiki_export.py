"""Offline integration tests for the Wiki exporter, using real temporary importers."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote, quote_plus

import pytest

from booruflow.infrastructure import gelbooru_wiki_export as exporter
from booruflow.infrastructure.gelbooru_aliases import (
    AliasRelation,
    GelbooruAliasRepository,
    parse_alias_page,
)
from booruflow.infrastructure.gelbooru_tag_importer import IMPORT_VERSION, extract_tags
from booruflow.infrastructure.gelbooru_tag_importer import SCHEMA as TAG_SCHEMA

ROOT = Path(__file__).resolve().parents[2]
EXACT = "MiXeD+100%_O'Brien_(é)"
TAGS = [
    {"id": 1, "name": "general", "count": 0, "type": 0},
    {"id": 2, "name": EXACT, "count": 0, "type": 1},
    {"id": 3, "name": "series", "count": 30, "type": 3},
    {"id": 4, "name": "character", "count": 20, "type": 4},
    {"id": 5, "name": "redirected", "count": 5, "type": 0},
    {"id": 6, "name": "metadata_tag", "count": 100, "type": 5},
    {"id": 7, "name": "deprecated", "count": 0, "type": 6},
    {"id": 8, "name": "invalid", "count": 0, "type": 2},
]
ALIASES = [
    ("deprecated", EXACT, "active"),
    ("chain_old", "deprecated", "active"),
    ("redirected", EXACT, "active"),
    ("Exact+%_Alias_(É)", EXACT, "active"),
    ("metadata_alias", "metadata_tag", "active"),
    ("invalid_alias", "invalid", "active"),
    ("absent_alias", "absent", "active"),
    ("pending_alias", "character", "pending"),
    ("cycle_a", "cycle_b", "active"),
    ("cycle_b", "cycle_a", "active"),
    ("conflict", "series", "active"),
    ("conflict", "character", "active"),
    ("before_conflict", "conflict", "active"),
    ("OldCase", "series", "active"),
    ("oldcase", "character", "active"),
]


def html_page(rows, pids=()):
    body = []
    for source, target, status in rows:
        css = " class='pending-tag'" if status == "pending" else ""
        links = "".join(
            f"<td><a href='?page=post&amp;s=list&amp;tags={quote_plus(name)}'>x</a></td>"
            for name in (source, target)
        )
        body.append(f"<tr{css}>{links}</tr>")
    pager = "".join(f"<a href='?page=alias&amp;s=list&amp;pid={pid}'>x</a>" for pid in pids)
    return "<html><table>" + "".join(body) + "</table><nav>" + pager + "</nav></html>"


def fixture_catalogues(directory, tags=TAGS, aliases=ALIASES):
    tags_path, aliases_path = directory / "tags.db", directory / "aliases.db"
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    with closing(sqlite3.connect(tags_path)) as connection, connection:
        connection.executescript(TAG_SCHEMA)
        connection.executemany(
            "INSERT INTO tags VALUES(?,?,?,?,?)",
            [(row["id"], row["name"], row["count"], row["type"], 0) for row in tags],
        )
        connection.executemany(
            "INSERT INTO import_state VALUES(?,?)",
            [
                ("completed", "1"),
                ("completed_at", stamp),
                ("import_version", IMPORT_VERSION),
                ("after_id", str(max(row["id"] for row in tags))),
            ],
        )
    # Deliberately allow contradictory fixture rows to test the export's own
    # collision guard, independently of the normal catalogue's unique index.
    with closing(sqlite3.connect(aliases_path)) as connection, connection:
        connection.executescript(
            "CREATE TABLE gelbooru_aliases(source_name TEXT,target_name TEXT,status TEXT);"
            "CREATE TABLE alias_sync_state(key TEXT PRIMARY KEY,value TEXT);"
        )
        connection.executemany("INSERT INTO gelbooru_aliases VALUES(?,?,?)", aliases)
        connection.execute(
            "INSERT INTO alias_sync_state VALUES('last_full_sync',?)", (json.dumps(stamp),)
        )
    return tags_path, aliases_path


def run_export(path, *, tags=None, aliases=None, **kwargs):
    values = TAGS if tags is None else tags
    relations = ALIASES if aliases is None else aliases
    with TemporaryDirectory(prefix="wiki-test-sources-") as directory:
        tags_path, aliases_path = fixture_catalogues(Path(directory), values, relations)
        if {"tag_fetcher", "maximum_id_fetcher", "minimum_rows"} & kwargs.keys():
            tags_path.unlink()
        if "alias_fetcher" in kwargs:
            aliases_path.unlink()
        options = {
            "tags_database": tags_path,
            "aliases_database": aliases_path,
            "tag_fetcher": lambda cursor, *_: [row for row in values if row["id"] > cursor],
            "maximum_id_fetcher": lambda *_: max(row["id"] for row in values),
            "alias_fetcher": lambda *_: html_page(relations),
            "minimum_rows": 1,
            "retries": 0,
            "alias_delay": 0,
            "progress": lambda _: None,
        }
        options.update(kwargs)
        return exporter.export_wiki_database(path, "12345", "test-secret", **options)


def test_categories_zero_counts_exact_names_aliases_and_integrity(tmp_path):
    target = tmp_path / "gelbooru_wiki_tags.db"
    result = run_export(target)
    assert result.destination == target
    assert (result.tags, result.aliases) == (4, 4)
    with closing(sqlite3.connect(target)) as connection:
        tags = connection.execute(
            "SELECT name,category,post_count FROM tags ORDER BY id"
        ).fetchall()
        assert tags == [("general", 0, 0), (EXACT, 1, 0), ("series", 3, 30), ("character", 4, 20)]
        aliases = dict(connection.execute("SELECT alias,canonical_tag FROM aliases"))
        assert aliases == {
            "deprecated": EXACT,
            "chain_old": EXACT,
            "redirected": EXACT,
            "Exact+%_Alias_(É)": EXACT,
        }
        assert connection.execute(
            "SELECT chain_length FROM aliases WHERE alias='chain_old'"
        ).fetchone() == (2,)
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        assert metadata["tag_count"] == "4"
        assert metadata["alias_count"] == "4"
        assert metadata["schema_version"] == "1"
        assert metadata["source_tag_checkpoint"] == "8"
        assert "generated_at" in metadata
        assert "test-secret" not in json.dumps(metadata)
        assert result.rejected_aliases == {"excluded_or_missing": 3, "cycle": 2, "ambiguous": 4}
        assert {row[1] for row in connection.execute("PRAGMA index_list(tags)")}
        assert {row[1] for row in connection.execute("PRAGMA index_list(aliases)")}
    assert list(tmp_path.iterdir()) == [target]


def test_parser_opt_in_preserves_case_plus_percent_and_defaults_unchanged():
    text = html_page([("Exact+%_Alias_(É)", EXACT, "active")])
    preserved = parse_alias_page(text, name_normalizer=lambda name: name).relations[0]
    assert preserved.source_name == "Exact+%_Alias_(É)"
    assert preserved.target_name == EXACT
    assert parse_alias_page(text).relations[0].source_name != preserved.source_name


def test_chains_longer_than_the_ui_depth_limit(tmp_path):
    relations = [(f"old{i}", f"old{i + 1}", "active") for i in range(30)]
    relations.append(("old30", "character", "active"))
    target = tmp_path / "wiki.db"
    run_export(target, aliases=relations)
    with closing(sqlite3.connect(target)) as connection:
        assert connection.execute(
            "SELECT canonical_tag,chain_length FROM aliases WHERE alias='old0'"
        ).fetchone() == ("character", 31)


def test_initialization_of_each_missing_catalogue_starts_at_zero(tmp_path):
    target = tmp_path / "wiki.db"
    calls = []

    def fetch(cursor, *_):
        calls.append(cursor)
        return TAGS if cursor == 0 else []

    run_export(target, tag_fetcher=fetch)
    run_export(target, tag_fetcher=fetch)
    assert calls.count(0) == 2
    assert calls == [0, 8, 8, 0, 8, 8]


@pytest.mark.parametrize("failure", ["network", "aliases", "validation", "activation", "interrupt"])
def test_failure_preserves_previous_export_and_cleans_temporary_files(
    tmp_path, monkeypatch, failure
):
    target = tmp_path / "wiki.db"
    run_export(target)
    before = target.read_bytes()

    def fail(*_args, **_kwargs):
        if failure == "interrupt":
            raise KeyboardInterrupt
        raise OSError("fixture failure")

    options = {}
    if failure in {"network", "interrupt"}:
        options["tag_fetcher"] = fail
    elif failure == "aliases":
        options["alias_fetcher"] = lambda *_: "<html>Login required</html>"
    elif failure == "validation":
        monkeypatch.setattr(exporter, "validate_export", fail)
    elif failure == "activation":
        original_replace = exporter.os.replace

        def fail_activation(source, destination):
            if Path(destination) == target:
                fail()
            return original_replace(source, destination)

        monkeypatch.setattr(exporter.os, "replace", fail_activation)
    with pytest.raises((OSError, ValueError, KeyboardInterrupt)):
        run_export(target, **options)
    assert target.read_bytes() == before
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize(
    "options",
    [
        {"tag_fetcher": lambda *_: TAGS[:4]},
        {"tag_fetcher": lambda *_: []},
        {"tag_fetcher": lambda *_: list(reversed(TAGS))},
        {"tag_fetcher": lambda *_: [{"id": 1, "name": "broken", "type": 0}]},
        {"maximum_id_fetcher": lambda *_: 0},
        {"minimum_rows": 1000},
        {"aliases": [("orphan", "absent", "active")]},
        {"tags": [TAGS[0], TAGS[2], TAGS[3]]},
        {"tags": TAGS + [{"id": 9, "name": "general", "count": 2, "type": 0}]},
    ],
)
def test_partial_invalid_and_empty_sources_fail_without_an_export(tmp_path, options):
    with pytest.raises((ValueError, RuntimeError)):
        run_export(tmp_path / "wiki.db", **options)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["empty", "short", "repeated", "changed"])
def test_alias_pagination_must_complete_consistently(tmp_path, kind):
    first = html_page([("old1", "character", "active"), ("old2", "series", "active")], (2, 4))
    calls = []

    def fetch(pid, _search):
        calls.append(pid)
        if pid == 0:
            return first
        if kind == "empty":
            return "<html>Bad gateway</html>"
        if kind == "repeated":
            return first
        if kind == "changed":
            return html_page(
                [("new1", "character", "active"), ("new2", "series", "active")], (2, 6)
            )
        return html_page([("new1", "character", "active")], (2, 4))

    with pytest.raises(ValueError):
        run_export(tmp_path / "wiki.db", alias_fetcher=fetch)
    assert calls == [0, 2]
    assert list(tmp_path.iterdir()) == []


def test_alias_listing_change_at_final_recheck_is_not_published(tmp_path):
    calls = 0

    def fetch(*_):
        nonlocal calls
        calls += 1
        return html_page(ALIASES[:4] if calls == 1 else ALIASES[:3])

    with pytest.raises(ValueError, match="changed"):
        run_export(tmp_path / "wiki.db", alias_fetcher=fetch)


def test_only_temporary_catalogues_are_opened_and_configured_production_is_refused(
    tmp_path, monkeypatch
):
    production = tmp_path / "production.db"
    production.write_bytes(b"untouched production")
    original = sqlite3.connect
    opened = []

    def guarded(path, *args, **kwargs):
        value = str(path)
        assert "production.db" not in value
        assert ".gelbooru-wiki-" in value or "wiki-test-sources-" in value
        opened.append(value)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded)
    run_export(tmp_path / "wiki.db")
    with pytest.raises(ValueError, match="protected"):
        run_export(production, protected_paths=(production,))
    assert opened
    assert production.read_bytes() == b"untouched production"


def test_unknown_existing_output_is_never_replaced(tmp_path):
    production = tmp_path / "production.db"
    production.write_bytes(b"not a wiki export")
    with pytest.raises(ValueError, match="unrecognized"):
        run_export(production)
    assert production.read_bytes() == b"not a wiki export"


def test_sidecar_prevents_output_replacement(tmp_path):
    target = tmp_path / "wiki.db"
    run_export(target)
    before = target.read_bytes()
    Path(str(target) + "-wal").write_bytes(b"active")
    with pytest.raises(ValueError, match="sidecars"):
        run_export(target)
    assert target.read_bytes() == before


@pytest.mark.parametrize(
    "payload", [None, "error", {"success": False}, {}, {"tag": None}, {"tags": [1]}]
)
def test_strict_api_extraction_rejects_malformed_payloads(payload):
    with pytest.raises((ValueError, RuntimeError)):
        extract_tags(payload, strict=True)


def test_strict_api_extraction_allows_explicit_empty_end_page():
    assert extract_tags({"tag": []}, strict=True) == []
    assert extract_tags([], strict=True) == []
    assert extract_tags({}) == []  # Original importer callers retain their contract.


def test_cli_import_and_help_work_without_site_packages_or_qt():
    script = ROOT / "tools/export_wiki_db.py"
    code = (
        "import runpy,sys; sys.argv=['export_wiki_db.py','--help']; "
        f"module=runpy.run_path({str(script)!r}); "
        "assert not any(n.startswith(('PySide6','PyQt','booruflow.runtime')) for n in sys.modules); "
        "module['main']()"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", code], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "--output" in result.stdout


def test_cli_reads_credentials_and_protected_paths_without_writing(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "export_wiki_db", ROOT / "tools/export_wiki_db.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = tmp_path / "config"
    config.mkdir()
    creds = config / "booruflow_credentials.json"
    creds.write_text(json.dumps({"gelbooru": {"user_id": "1", "api_key": "secret"}}))
    settings = config / "booruflow_settings.json"
    settings.write_text(json.dumps({"gelbooru_tag_database": "data/production.db"}))
    before = {p.name: p.read_bytes() for p in config.iterdir()}
    monkeypatch.delenv("GELBOORU_USER_ID", raising=False)
    monkeypatch.delenv("GELBOORU_API_KEY", raising=False)
    assert module.load_configuration(tmp_path) == (
        "1",
        "secret",
        (tmp_path / "data/production.db",),
    )
    monkeypatch.setenv("GELBOORU_USER_ID", "2")
    with pytest.raises(ValueError, match="both"):
        module.load_configuration(tmp_path)
    monkeypatch.setenv("GELBOORU_API_KEY", "environment-secret")
    assert module.load_configuration(tmp_path)[:2] == ("2", "environment-secret")
    assert before == {p.name: p.read_bytes() for p in config.iterdir()}


def test_secret_redaction_includes_url_encoded_keys():
    secret = "a+b/%secret"
    text = " ".join((secret, quote(secret, safe=""), quote_plus(secret)))
    assert exporter.redact(text, secret) == "[redacted] [redacted] [redacted]"


def test_real_api_empty_end_page_can_omit_tag_key():
    assert extract_tags({"@attributes": {"count": 0, "limit": 100, "offset": 0}}, strict=True) == []
    with pytest.raises(ValueError):
        extract_tags({"@attributes": {"count": 100}}, strict=True)


@pytest.mark.parametrize("declared_total", [8, 9])
def test_remaining_count_metadata_is_not_mistaken_for_total(tmp_path, monkeypatch, declared_total):
    from booruflow.infrastructure import gelbooru_tag_importer as importer

    def fake_fetch(cursor, _uid, _key, *, strict, response_metadata):
        assert strict
        response_metadata.clear()
        response_metadata.update(count=declared_total if cursor == 0 else 0)
        return TAGS if cursor == 0 else []

    monkeypatch.setattr(importer, "fetch_page", fake_fetch)
    destination = tmp_path / "wiki.db"
    options = {
        "maximum_id_fetcher": lambda *_: 8,
        "alias_fetcher": lambda *_: html_page(ALIASES[:4]),
        "minimum_rows": 1,
        "retries": 0,
        "alias_delay": 0,
        "progress": lambda _: None,
    }
    if declared_total == 9:
        with pytest.raises(ValueError, match="fewer tags"):
            run_export(destination, tag_fetcher=None, **options)
        assert not destination.exists()
    else:
        run_export(destination, tag_fetcher=None, **options)
        with closing(sqlite3.connect(destination)) as connection:
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        assert metadata["source_tags_imported"] == "8"
        assert metadata["source_tag_checkpoint"] == "8"


def test_multiple_alias_pages_complete_and_preserve_final_short_page(tmp_path):
    pages = {
        0: html_page([("old1", "character", "active"), ("old2", "series", "active")], (2,)),
        2: html_page([("old3", "old1", "active")], ()),
    }
    calls = []
    result = run_export(
        tmp_path / "wiki.db", alias_fetcher=lambda pid, _: calls.append(pid) or pages[pid]
    )
    assert result.aliases == 3
    assert calls == [0, 2, 0]


def normal_catalogues(directory, tag_age=1, alias_age=1):
    directory.mkdir(parents=True, exist_ok=True)
    tags_path, aliases_path = fixture_catalogues(directory, aliases=ALIASES[:4])
    aliases_path.unlink()
    repository = GelbooruAliasRepository(aliases_path)
    repository.migrate()
    for order, (source, target, status) in enumerate(ALIASES[:4]):
        repository.upsert(AliasRelation(source, target, status, 0, order))
    tag_stamp = (datetime.now(UTC) - timedelta(hours=tag_age)).isoformat(timespec="seconds")
    alias_stamp = (datetime.now(UTC) - timedelta(hours=alias_age)).isoformat(timespec="seconds")
    with closing(sqlite3.connect(tags_path)) as connection, connection:
        connection.executemany(
            "INSERT OR REPLACE INTO import_state VALUES(?,?)",
            [
                ("last_successful_update_at", tag_stamp),
                ("last_successful_update_mode", "incremental_new_ids"),
            ],
        )
    repository.set_state(
        {
            "last_full_sync": alias_stamp,
            "last_successful_sync_at": alias_stamp,
            "last_successful_sync_mode": "last_full_sync",
            "observed_last_pid": 0,
            "checkpoint": repository.checkpoint(20),
        }
    )
    return tags_path, aliases_path


def local_export(destination, sources, **kwargs):
    options = {
        "tags_database": sources[0],
        "aliases_database": sources[1],
        "tag_fetcher": lambda cursor, *_: (
            [{"id": 9, "name": "new_tag", "count": 0, "type": 0}] if cursor < 9 else []
        ),
        "maximum_id_fetcher": lambda *_: 9,
        "alias_fetcher": lambda *_: html_page(ALIASES[:4]),
        "minimum_rows": 1,
        "retries": 0,
        "alias_delay": 0,
        "progress": lambda _: None,
    }
    options.update(kwargs)
    return exporter.export_wiki_database(destination, "12345", "test-secret", **options)


@pytest.mark.parametrize(
    "tag_age,alias_age,expected",
    [
        (1, 1, (False, False)),
        (25, 1, (True, False)),
        (1, 25, (False, True)),
        (25, 25, (True, True)),
    ],
)
def test_freshness_is_independent_and_only_stale_catalogues_sync(
    tmp_path, tag_age, alias_age, expected
):
    sources = normal_catalogues(tmp_path / "sources", tag_age, alias_age)
    tag_calls, alias_calls = [], []

    def tags(cursor, *_):
        tag_calls.append(cursor)
        return [{"id": 9, "name": "new_tag", "count": 0, "type": 0}] if cursor < 9 else []

    def aliases(pid, _):
        alias_calls.append(pid)
        return html_page(ALIASES[:4])

    local_export(tmp_path / "wiki.db", sources, tag_fetcher=tags, alias_fetcher=aliases)
    assert (bool(tag_calls), bool(alias_calls)) == expected
    if tag_calls:
        assert tag_calls == [8, 9, 9]  # Real normal update, not reconstruction from ID zero.
    with closing(sqlite3.connect(tmp_path / "wiki.db")) as connection:
        metadata = dict(connection.execute("SELECT key,value FROM metadata"))
    assert metadata["source_snapshot_method"] == "sqlite_backup"
    assert metadata["source_tags_database"] == str(sources[0])
    assert metadata["source_aliases_database"] == str(sources[1])
    assert metadata["source_tags_sync_at"] and metadata["source_aliases_sync_at"]
    assert metadata["source_tags_sync_mode"] == "incremental_new_ids"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".gelbooru-wiki-")] == []


def test_fresh_catalogues_need_no_credentials_no_network_and_remain_unchanged(
    tmp_path, monkeypatch
):
    sources = normal_catalogues(tmp_path / "sources")
    before = [p.read_bytes() for p in sources]

    def forbidden(*_args, **_kwargs):
        raise AssertionError("A fresh local export must not synchronize or access the network")

    monkeypatch.setattr(exporter, "_synchronize_tags", forbidden)
    monkeypatch.setattr(exporter, "_synchronize_aliases", forbidden)
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    target = tmp_path / "wiki.db"
    exporter.export_wiki_database(
        target, tags_database=sources[0], aliases_database=sources[1], minimum_rows=1
    )
    exporter.export_wiki_database(
        target, tags_database=sources[0], aliases_database=sources[1], minimum_rows=1
    )
    assert [p.read_bytes() for p in sources] == before
    assert target.is_file()


def test_force_update_then_repeat_uses_shared_success_dates(tmp_path):
    sources = normal_catalogues(tmp_path / "sources")
    calls = []

    def tags(cursor, *_):
        calls.append(("tags", cursor))
        return [{"id": 9, "name": "new_tag", "count": 0, "type": 0}] if cursor < 9 else []

    def aliases(pid, _):
        calls.append(("aliases", pid))
        return html_page(ALIASES[:4])

    local_export(
        tmp_path / "wiki.db", sources, force_update=True, tag_fetcher=tags, alias_fetcher=aliases
    )
    first = list(calls)
    assert {call[0] for call in calls} == {"tags", "aliases"}
    local_export(tmp_path / "wiki.db", sources, tag_fetcher=tags, alias_fetcher=aliases)
    assert calls == first
    # Even on the next forced run, mixed-case/plus/percent names in the stored
    # checkpoint must match the normal synchronizer without a full fallback.
    messages = []
    local_export(
        tmp_path / "wiki.db",
        sources,
        force_update=True,
        tag_fetcher=tags,
        alias_fetcher=aliases,
        progress=messages.append,
    )
    assert not any("réconciliation complète" in message for message in messages)


@pytest.mark.parametrize("missing", [(True, False), (False, True), (True, True)])
def test_missing_normal_catalogues_are_initialized_in_the_configured_paths(tmp_path, missing):
    sources = normal_catalogues(tmp_path / "sources")
    for path, absent in zip(sources, missing):
        if absent:
            path.unlink()
    calls = []

    def tags(cursor, *_):
        calls.append(cursor)
        return TAGS if cursor == 0 else []

    local_export(tmp_path / "wiki.db", sources, tag_fetcher=tags, maximum_id_fetcher=lambda *_: 8)
    assert all(path.is_file() for path in sources)
    assert bool(calls) == missing[0]
    if missing[0]:
        assert calls == [0, 8, 8]
    for kind, path in zip(("tags", "aliases"), sources):
        status = exporter.catalogue_status(path, kind, datetime.now(UTC))
        assert status.fresh
        assert status.successful_at
    assert not any(p.name.startswith(".gelbooru-wiki-") for p in tmp_path.iterdir())


@pytest.mark.parametrize("kind", ["tags", "aliases"])
@pytest.mark.parametrize(
    "value", [None, "not-a-date", "2026-10-09T12:00:00", "2999-01-01T00:00:00+00:00"]
)
def test_unknown_invalid_naive_and_future_dates_are_conservatively_stale(tmp_path, kind, value):
    sources = normal_catalogues(tmp_path / "sources")
    path = sources[0 if kind == "tags" else 1]
    table = "import_state" if kind == "tags" else "alias_sync_state"
    key = "last_successful_update_at" if kind == "tags" else "last_successful_sync_at"
    with closing(sqlite3.connect(path)) as connection, connection:
        if value is None:
            for date_key in (key, "completed_at", "last_full_sync", "last_incremental_sync"):
                connection.execute(f"DELETE FROM {table} WHERE key=?", (date_key,))
        else:
            connection.execute(
                f"UPDATE {table} SET value=? WHERE key=?",
                (value if kind == "tags" else json.dumps(value), key),
            )
    assert not exporter.catalogue_status(path, kind, datetime.now(UTC)).fresh
    calls = []

    def tags(cursor, *_):
        calls.append("tags")
        return []

    def aliases(*_):
        calls.append("aliases")
        return html_page(ALIASES[:4])

    local_export(
        tmp_path / "wiki.db",
        sources,
        tag_fetcher=tags,
        maximum_id_fetcher=lambda *_: 8,
        alias_fetcher=aliases,
    )
    assert set(calls) == {kind}


@pytest.mark.parametrize("hours,fresh", [(23.999, True), (24, False), (24.001, False)])
def test_24_hour_boundary_uses_timestamp_not_file_mtime(tmp_path, hours, fresh):
    sources = normal_catalogues(tmp_path / "sources")
    now = datetime.now(UTC)
    with closing(sqlite3.connect(sources[0])) as connection, connection:
        connection.execute(
            "UPDATE import_state SET value=? WHERE key='last_successful_update_at'",
            ((now - timedelta(hours=hours)).isoformat(),),
        )
    assert exporter.catalogue_status(sources[0], "tags", now).fresh is fresh


@pytest.mark.parametrize("kind", ["tags", "aliases"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_failed_or_interrupted_force_sync_keeps_old_dates_and_export(tmp_path, kind, interrupted):
    sources = normal_catalogues(tmp_path / "sources")
    target = tmp_path / "wiki.db"
    local_export(target, sources)
    before = target.read_bytes()
    previous = exporter.catalogue_status(
        sources[0 if kind == "tags" else 1], kind, datetime.now(UTC)
    ).successful_at

    def fail(*_):
        if interrupted:
            raise KeyboardInterrupt
        raise OSError("fixture network failure")

    with pytest.raises((OSError, KeyboardInterrupt)):
        if kind == "tags":
            local_export(target, sources, force_update=True, tag_fetcher=fail)
        else:
            # The tag sync succeeds first; the alias failure must remain independent.
            local_export(target, sources, force_update=True, alias_fetcher=fail)
    failed = exporter.catalogue_status(sources[0 if kind == "tags" else 1], kind, datetime.now(UTC))
    assert failed.successful_at == previous
    assert not failed.fresh
    assert target.read_bytes() == before
    assert not any(p.name.startswith(".gelbooru-wiki-") for p in tmp_path.iterdir())


def test_wal_snapshots_include_committed_rows_and_merge_never_writes_sources(tmp_path, monkeypatch):
    sources = normal_catalogues(tmp_path / "sources")
    with (
        closing(sqlite3.connect(sources[0])) as tag_writer,
        closing(sqlite3.connect(sources[1])) as alias_writer,
    ):
        for connection in (tag_writer, alias_writer):
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA wal_autocheckpoint=0")
        tag_writer.execute("UPDATE tags SET post_count=123 WHERE id=4")
        tag_writer.commit()
        GelbooruAliasRepository(sources[1]).upsert(
            AliasRelation("wal_old", "character", "active", 0, 10), alias_writer
        )
        alias_writer.commit()
        assert all(Path(str(path) + "-wal").stat().st_size > 0 for path in sources)
        before = [(p.read_bytes(), Path(str(p) + "-wal").read_bytes()) for p in sources]
        actual_build = exporter._build_export

        def verify_copy(staging, tag_copy, observation, metadata):
            assert tag_copy not in sources and tag_copy.parent.name.startswith(".gelbooru-wiki-")
            assert (tag_copy.parent / "aliases.db").is_file()
            original = sqlite3.connect

            def guarded(path, *args, **kwargs):
                value = str(path)
                assert not any(
                    str(source) in value or source.as_uri() in value for source in sources
                )
                return original(path, *args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(sqlite3, "connect", guarded)
                return actual_build(staging, tag_copy, observation, metadata)

        monkeypatch.setattr(exporter, "_build_export", verify_copy)
        local_export(tmp_path / "wiki.db", sources)
        assert [(p.read_bytes(), Path(str(p) + "-wal").read_bytes()) for p in sources] == before
        with closing(sqlite3.connect(tmp_path / "wiki.db")) as connection:
            assert connection.execute("SELECT post_count FROM tags WHERE id=4").fetchone() == (123,)
            assert connection.execute(
                "SELECT canonical_tag FROM aliases WHERE alias='wal_old'"
            ).fetchone() == ("character",)
            assert connection.execute("SELECT post_count FROM tags WHERE id=1").fetchone() == (0,)


def test_source_operation_lock_refuses_export_before_any_network_or_modification(tmp_path):
    from booruflow.infrastructure.gelbooru_aliases import catalog_operation_lock

    sources = normal_catalogues(tmp_path / "sources")
    before = [p.read_bytes() for p in sources]
    with catalog_operation_lock(sources[0]), pytest.raises(RuntimeError, match="already running"):
        local_export(tmp_path / "wiki.db", sources)
    assert [p.read_bytes() for p in sources] == before
    assert not (tmp_path / "wiki.db").exists()


def test_incremental_scope_preserves_historical_counters_and_full_sync_date(tmp_path):
    sources = normal_catalogues(tmp_path / "sources", tag_age=25)
    with closing(sqlite3.connect(sources[0])) as connection:
        full_date = connection.execute(
            "SELECT value FROM import_state WHERE key='completed_at'"
        ).fetchone()
    local_export(tmp_path / "wiki.db", sources)
    with closing(sqlite3.connect(sources[0])) as connection:
        assert connection.execute("SELECT post_count FROM tags WHERE id=3").fetchone() == (30,)
        assert (
            connection.execute("SELECT value FROM import_state WHERE key='completed_at'").fetchone()
            == full_date
        )
        assert connection.execute("SELECT name FROM tags WHERE id=9").fetchone() == ("new_tag",)


def test_pending_only_sync_is_not_a_new_catalogue_success(tmp_path):
    sources = normal_catalogues(tmp_path / "sources")
    from booruflow.infrastructure.gelbooru_aliases import GelbooruAliasSynchronizer

    before = exporter.catalogue_status(sources[1], "aliases", datetime.now(UTC)).successful_at
    GelbooruAliasSynchronizer(
        sources[1], lambda *_: html_page(ALIASES[:4]), delay=0
    ).revalidate_pending()
    after = exporter.catalogue_status(sources[1], "aliases", datetime.now(UTC)).successful_at
    assert after == before


def test_cli_resolves_configured_legacy_relative_and_portable_paths_without_saving(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "wiki_paths_cli", ROOT / "tools/export_wiki_db.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = tmp_path / "config"
    config.mkdir()
    path = config / "booruflow_settings.json"
    for settings, expected in [
        (
            {"gelbooru_database": "legacy/tags.db"},
            (tmp_path / "legacy/tags.db", tmp_path / "data/databases/gelbooru_aliases.db"),
        ),
        (
            {
                "gelbooru_tag_database": "@app/data/databases/gelbooru_tags.db",
                "gelbooru_alias_database": "@app/data/databases/gelbooru_aliases.db",
            },
            (
                tmp_path / "data/databases/gelbooru_tags.db",
                tmp_path / "data/databases/gelbooru_aliases.db",
            ),
        ),
    ]:
        path.write_text(json.dumps(settings))
        before = path.read_bytes()
        assert module.load_catalogue_paths(tmp_path) == expected
        assert path.read_bytes() == before


def test_incompatible_existing_tags_are_never_silently_rebuilt(tmp_path):
    sources = normal_catalogues(tmp_path / "sources", tag_age=25)
    with closing(sqlite3.connect(sources[0])) as connection, connection:
        connection.execute("DELETE FROM import_state WHERE key='after_id'")
    messages = []
    with pytest.raises(ValueError, match="reconstruction complète"):
        local_export(tmp_path / "wiki.db", sources, progress=messages.append)
    assert not any("initialisation complète" in message for message in messages)
    with closing(sqlite3.connect(sources[0])) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tags").fetchone() == (8,)


def test_snapshot_failure_preserves_export_and_removes_partial_copies(tmp_path, monkeypatch):
    sources = normal_catalogues(tmp_path / "sources")
    target = tmp_path / "wiki.db"
    local_export(target, sources)
    before = target.read_bytes()

    def fail_copy(source, destination):
        destination.write_bytes(b"incomplete snapshot")
        raise OSError("fixture disk error")

    monkeypatch.setattr(exporter, "snapshot_catalogue", fail_copy)
    with pytest.raises(OSError, match="disk error"):
        local_export(target, sources)
    assert target.read_bytes() == before
    assert not any(p.name.startswith(".gelbooru-wiki-") for p in tmp_path.iterdir())


def test_alias_checkpoint_loss_uses_announced_normal_full_reconciliation(tmp_path):
    sources = normal_catalogues(tmp_path / "sources", alias_age=25)
    repository = GelbooruAliasRepository(sources[1])
    repository.set_state({"checkpoint": []})
    messages = []
    local_export(tmp_path / "wiki.db", sources, progress=messages.append)
    assert any("réconciliation complète nécessaire" in message for message in messages)
    status = exporter.catalogue_status(sources[1], "aliases", datetime.now(UTC))
    assert status.fresh
    assert status.state["last_successful_sync_mode"] == "last_full_sync"


def test_normal_updaters_do_not_date_invalid_remote_responses_as_successful(tmp_path, monkeypatch):
    sources = normal_catalogues(tmp_path / "sources")
    from booruflow.infrastructure.gelbooru_aliases import GelbooruAliasSynchronizer
    from booruflow.infrastructure.gelbooru_tag_importer import update_database

    previous = [
        exporter.catalogue_status(path, kind, datetime.now(UTC)).successful_at
        for path, kind in zip(sources, ("tags", "aliases"))
    ]

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            return b"{}"

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(ValueError, match="no tag collection"):
        update_database(sources[0], "12345", "test-secret", retries=0, progress=lambda _: None)
    with pytest.raises(ValueError, match="invalid alias"):
        GelbooruAliasSynchronizer(
            sources[1], lambda *_: "<html>login</html>", delay=0
        ).incremental()
    for index, (path, kind) in enumerate(zip(sources, ("tags", "aliases"))):
        status = exporter.catalogue_status(path, kind, datetime.now(UTC))
        assert not status.fresh
        assert status.successful_at == previous[index]


def test_full_cli_local_export_runs_without_qt_site_packages_credentials_or_network(
    tmp_path, monkeypatch
):
    sources = normal_catalogues(tmp_path / "sources")
    config = tmp_path / "config"
    config.mkdir()
    (config / "booruflow_settings.json").write_text(
        json.dumps(
            {
                "gelbooru_tag_database": str(sources[0]),
                "gelbooru_alias_database": str(sources[1]),
            }
        )
    )
    monkeypatch.delenv("GELBOORU_USER_ID", raising=False)
    monkeypatch.delenv("GELBOORU_API_KEY", raising=False)
    code = (
        "import runpy,sys,urllib.request; from pathlib import Path; from functools import partial; "
        "urllib.request.urlopen=lambda *_a,**_kw: (_ for _ in ()).throw(AssertionError('network')); "
        f"m=runpy.run_path({str(ROOT / 'tools/export_wiki_db.py')!r}); "
        f"m['main'].__globals__['ROOT']=Path({str(tmp_path)!r}); "
        "g=m['main'].__globals__; g['export_wiki_database']=partial(g['export_wiki_database'],minimum_rows=1); "
        "assert m['main']([])==0; "
        "assert not any(n.startswith(('PySide6','PyQt','booruflow.runtime')) for n in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", code], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "var/exports/gelbooru_wiki_tags.db").is_file()


def test_recent_but_truncated_source_preserves_previous_export_without_network(tmp_path):
    sources = normal_catalogues(tmp_path / "sources")
    target = tmp_path / "wiki.db"
    local_export(target, sources)
    before = target.read_bytes()
    with pytest.raises(ValueError, match="expected at least 100,000"):
        exporter.export_wiki_database(target, tags_database=sources[0], aliases_database=sources[1])
    assert target.read_bytes() == before
    assert not any(p.name.startswith(".gelbooru-wiki-") for p in tmp_path.iterdir())


@pytest.mark.parametrize("marker", ["sync_in_progress", "initial_in_progress"])
def test_recent_alias_success_with_unfinished_checkpoint_is_not_fresh(tmp_path, marker):
    sources = normal_catalogues(tmp_path / "sources")
    repository = GelbooruAliasRepository(sources[1])
    before = repository.state()["checkpoint"]
    repository.set_state({marker: True})
    status = exporter.catalogue_status(sources[1], "aliases", datetime.now(UTC))
    assert status.successful_at is not None
    assert not status.fresh
    assert status.state["checkpoint"] == before


def test_cli_reports_redacted_exception_chain_and_frame_locations(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location(
        "export_wiki_db", ROOT / "tools/export_wiki_db.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    secret, user_id = "private+api/key", "private-user-id"
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "load_configuration", lambda root: (user_id, secret, ()))
    monkeypatch.setattr(module, "load_catalogue_paths", lambda root: (root / "t.db", root / "a.db"))

    def fail(*args, **kwargs):
        invisible_local = "LOCAL_VALUE_MUST_NOT_APPEAR"
        assert invisible_local
        try:
            raise OSError(f"request failed: {quote_plus(secret)} {user_id}")
        except OSError as cause:
            raise ValueError(f"catalogue failed: {secret}") from cause

    monkeypatch.setattr(module, "export_wiki_database", fail)
    assert module.main(["--output", str(tmp_path / "wiki.db")]) == 1
    diagnostic = capsys.readouterr().err
    assert "Échec de l'export" in diagnostic
    assert "Traceback" in diagnostic and "in fail" in diagnostic
    assert "OSError: request failed:" in diagnostic
    assert "ValueError: catalogue failed:" in diagnostic
    assert "[redacted]" in diagnostic
    for forbidden in (secret, quote_plus(secret), user_id, "LOCAL_VALUE_MUST_NOT_APPEAR"):
        assert forbidden not in diagnostic
