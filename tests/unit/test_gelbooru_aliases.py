import sqlite3
from pathlib import Path

import pytest

from booruflow.infrastructure.gelbooru_aliases import (
    AliasRelation,
    GelbooruAliasRepository,
    GelbooruAliasSynchronizer,
    catalog_operation_lock,
    ensure_alias_schema,
    fetch_alias_html,
    pager_geometry,
    parse_alias_page,
    resolve_gelbooru_alias,
    resolve_gelbooru_alias_with_diagnostic,
)


def alias_html(
    rows: list[tuple[str, str, str]], *, pids: tuple[int, ...] = (50, 100)
) -> str:
    body = []
    for source, target, status in rows:
        css = " class='pending-tag'" if status == "pending" else ""
        body.append(
            f"<tr{css}><td><a href='index.php?page=post&amp;s=list&amp;tags={source}'>x</a>"
            f" → <a href='index.php?page=post&amp;s=list&amp;tags={target}'>y</a></td></tr>"
        )
    pager = "".join(
        f"<a href='?page=alias&amp;s=list&amp;pid={pid}'>{pid}</a>" for pid in pids
    )
    return f"<html><table>{''.join(body)}</table><nav>{pager}</nav></html>"


def make_database(path: Path) -> GelbooruAliasRepository:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE tags(id INTEGER PRIMARY KEY,name TEXT)")
        connection.execute("INSERT INTO tags VALUES(1,'keep_me')")
    repository = GelbooruAliasRepository(path)
    repository.migrate()
    return repository


def test_migration_preserves_existing_tags_and_enforces_one_active_target(tmp_path: Path) -> None:
    database = tmp_path / "tags.db"
    repository = make_database(database)
    repository.upsert(AliasRelation("a", "b", "active"))
    repository.upsert(AliasRelation("a", "c", "active"))

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name FROM tags").fetchone()[0] == "keep_me"
        assert connection.execute(
            "SELECT target_name,status,missing_reason FROM gelbooru_aliases "
            "WHERE source_name='a' ORDER BY target_name"
        ).fetchall() == [("b", "missing", "target_changed"), ("c", "active", None)]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO gelbooru_aliases VALUES(?,?,?,?,?,?,?,?,?)",
                ("a", "d", "active", "x", "x", "x", None, 0, 0),
            )


def test_parser_uses_tag_links_and_pending_class() -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "gelbooru_aliases_page.html"
    page = parse_alias_page(fixture.read_text(encoding="utf-8"), 50)
    assert [(row.source_name, row.target_name, row.status) for row in page.relations] == [
        ("china_dress", "qipao", "active"),
        ("rose_weasley", "rose_granger-weasley", "pending"),
        ("foo_(bar)", "foo-bar", "active"),
    ]
    assert pager_geometry(page) == (23200, 50, 465)


def test_pager_absent_is_only_single_page_when_short_and_rejects_full_page() -> None:
    short = parse_alias_page(alias_html([("a", "b", "active")], pids=()))
    assert pager_geometry(short) == (0, 1, 1)
    full = parse_alias_page(alias_html(
        [(f"a{i}", f"b{i}", "active") for i in range(50)], pids=()
    ))
    with pytest.raises(ValueError, match="pager"):
        pager_geometry(full)


def test_resolver_follows_only_active_chains_and_detects_cycles(tmp_path: Path) -> None:
    database = tmp_path / "tags.db"
    repository = make_database(database)
    repository.upsert(AliasRelation("china_dress", "qipao", "active"))
    repository.upsert(AliasRelation("rose_weasley", "rose_granger-weasley", "pending"))
    repository.upsert(AliasRelation("a", "b", "active"))
    repository.upsert(AliasRelation("b", "c", "active"))

    assert resolve_gelbooru_alias("china_dress", database) == "qipao"
    assert resolve_gelbooru_alias("rose_weasley", database) == "rose_weasley"
    assert resolve_gelbooru_alias("a", database) == "c"

    repository.upsert(AliasRelation("c", "a", "active"))
    assert resolve_gelbooru_alias("a", database) == "a"
    assert resolve_gelbooru_alias_with_diagnostic("a", database) == ("a", "cycle")


def test_initial_import_checkpoints_each_page_and_resumes(tmp_path: Path) -> None:
    database = tmp_path / "tags.db"
    make_database(database)
    pages = {
        0: alias_html([("a", "b", "active"), ("c", "d", "pending")], pids=(2,)),
        2: alias_html([("e", "f", "active")], pids=(2,)),
    }
    calls: list[int] = []

    def interrupted(pid: int, _search: str) -> str:
        calls.append(pid)
        if pid == 2:
            raise InterruptedError
        return pages[pid]

    sync = GelbooruAliasSynchronizer(database, interrupted, delay=0)
    with pytest.raises(InterruptedError):
        sync.initial_import()
    assert GelbooruAliasRepository(database).state()["initial_next_pid"] == 2

    resumed_calls: list[int] = []
    summary = GelbooruAliasSynchronizer(
        database, lambda pid, _search: resumed_calls.append(pid) or pages[pid], delay=0
    ).initial_import()
    assert resumed_calls == [0, 2]
    assert summary.state == "completed"
    assert (summary.active, summary.pending, summary.missing) == (2, 1, 0)


def _seed_checkpoint(repository: GelbooruAliasRepository, relations: list[AliasRelation]) -> None:
    for relation in relations:
        repository.upsert(relation)
    checkpoint = [list(row.checkpoint_key) for row in relations[-20:]]
    repository.set_state({
        "checkpoint": checkpoint,
        "checkpoint_version": "1",
        "observed_last_pid": 50,
        "page_size": 50,
    })


def test_incremental_walks_back_from_tail_and_imports_newer_pages(tmp_path: Path) -> None:
    database = tmp_path / "tags.db"
    repository = make_database(database)
    known = [AliasRelation(f"known{i}", f"target{i}", "active", 50, i) for i in range(12)]
    _seed_checkpoint(repository, known)
    pages = {
        0: alias_html([], pids=(50, 100)),
        100: alias_html([("new2", "target", "active")], pids=(50, 100)),
        50: alias_html(
            [(row.source_name, row.target_name, row.status) for row in known]
            + [("new1", "target", "active")], pids=(50, 100)
        ),
    }
    calls: list[int] = []
    summary = GelbooruAliasSynchronizer(
        database, lambda pid, _search: calls.append(pid) or pages[pid], delay=0
    ).incremental()
    assert calls[:3] == [0, 100, 50]
    assert summary.state == "completed"
    assert repository.active_target("new1") == "target"
    assert repository.active_target("new2") == "target"


def test_incremental_never_falls_back_silently_when_overlap_is_absent(tmp_path: Path) -> None:
    database = tmp_path / "tags.db"
    repository = make_database(database)
    _seed_checkpoint(repository, [
        AliasRelation(f"known{i}", f"target{i}", "active", 50, i) for i in range(12)
    ])
    pages = {
        0: alias_html([], pids=(50,)),
        50: alias_html([("other", "target", "active")], pids=(50,)),
    }
    summary = GelbooruAliasSynchronizer(
        database, lambda pid, _search: pages[pid], delay=0, maximum_back_pages=3
    ).incremental()
    assert summary.state == "overlap_not_found"
    assert repository.active_target("other") is None


def test_pending_exact_search_ignores_partial_result_and_marks_missing(tmp_path: Path) -> None:
    database = tmp_path / "tags.db"
    repository = make_database(database)
    repository.upsert(AliasRelation("rose", "old", "pending"))
    pages = {
        (0, ""): alias_html([], pids=(50, 100)),
        (0, "rose"): alias_html([("rosehip", "new", "active")], pids=()),
    }
    summary = GelbooruAliasSynchronizer(
        database, lambda pid, search: pages[(pid, search)], delay=0
    ).revalidate_pending()
    assert summary.missing == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT status,missing_reason FROM gelbooru_aliases WHERE source_name='rose'"
        ).fetchone() == ("missing", "source_not_found")


@pytest.mark.parametrize(
    ("remote_target", "remote_status", "expected"),
    [
        ("old", "pending", [("old", "pending", None)]),
        ("old", "active", [("old", "active", None)]),
        ("new", "active", [
            ("new", "active", None), ("old", "missing", "target_changed")
        ]),
    ],
)
def test_pending_exact_revalidation_status_and_target_changes(
    tmp_path: Path,
    remote_target: str,
    remote_status: str,
    expected: list[tuple[str, str, str | None]],
) -> None:
    database = tmp_path / "tags.db"
    repository = make_database(database)
    repository.upsert(AliasRelation("source", "old", "pending"))
    pages = {
        (0, ""): alias_html([], pids=(50, 100)),
        (0, "source"): alias_html([("source", remote_target, remote_status)], pids=()),
    }
    GelbooruAliasSynchronizer(
        database, lambda pid, search: pages[(pid, search)], delay=0
    ).revalidate_pending()
    with sqlite3.connect(database) as connection:
        actual = connection.execute(
            "SELECT target_name,status,missing_reason FROM gelbooru_aliases "
            "WHERE source_name='source' ORDER BY target_name"
        ).fetchall()
    connection.close()
    assert actual == expected


def test_pending_threshold_recommends_full_without_searches(tmp_path: Path) -> None:
    database = tmp_path / "tags.db"
    repository = make_database(database)
    for index in range(2):
        repository.upsert(AliasRelation(f"p{index}", "target", "pending"))
    calls: list[tuple[int, str]] = []
    summary = GelbooruAliasSynchronizer(
        database,
        lambda pid, search: calls.append((pid, search)) or alias_html([], pids=(1,)),
        delay=0,
    ).revalidate_pending()
    assert summary.state == "full_reconciliation_recommended"
    assert calls == [(0, "")]


def test_full_reconciliation_keeps_history_and_marks_disappeared(tmp_path: Path) -> None:
    database = tmp_path / "tags.db"
    repository = make_database(database)
    repository.upsert(AliasRelation("a", "b", "active"))
    repository.upsert(AliasRelation("gone", "old", "active"))
    page = alias_html([("a", "c", "active")], pids=())
    summary = GelbooruAliasSynchronizer(
        database, lambda _pid, _search: page, delay=0
    ).full_reconciliation()
    assert summary.state == "completed"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT target_name,status,missing_reason FROM gelbooru_aliases "
            "WHERE source_name='a' ORDER BY target_name"
        ).fetchall() == [("b", "missing", "source_not_found"), ("c", "active", None)]
        assert connection.execute(
            "SELECT status FROM gelbooru_aliases WHERE source_name='gone'"
        ).fetchone()[0] == "missing"


def test_catalogue_lock_refuses_concurrent_writer_without_touching_db(tmp_path: Path) -> None:
    database = tmp_path / "tags.db"
    make_database(database)
    before = database.read_bytes()
    with (
        catalog_operation_lock(database),
        pytest.raises(RuntimeError, match="already running"),
        catalog_operation_lock(database),
    ):
        pass
    assert database.read_bytes() == before


def test_catalogue_lock_recovers_a_stale_process_lock(tmp_path: Path) -> None:
    database = tmp_path / "tags.db"
    make_database(database)
    lock = database.with_name(database.name + ".operation.lock")
    lock.write_text("2147483647", encoding="ascii")
    with catalog_operation_lock(database):
        assert lock.is_file()
    assert not lock.exists()


def test_schema_helper_accepts_connection(tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / "tags.db") as connection:
        ensure_alias_schema(connection)
        assert connection.execute(
            "SELECT value FROM alias_sync_state WHERE key='schema_version'"
        ).fetchone()[0] == "1"


def test_network_fetch_is_get_only_with_timeout_and_encoded_search(monkeypatch) -> None:
    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"<html></html>"

    def open_request(request, timeout):
        captured.append((request, timeout))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    fetch_alias_html(50, "foo_(bar)", retries=0)
    request, timeout = captured[0]
    assert request.get_method() == "GET"
    assert timeout == 30
    assert "search=foo_%28bar%29" in request.full_url
