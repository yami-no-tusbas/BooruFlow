"""Export coherent snapshots of BooruFlow catalogues, refreshing only stale sources."""

from __future__ import annotations

import json
import os
import sqlite3
import traceback
from collections import Counter
from collections.abc import Callable
from contextlib import ExitStack, closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote, quote_plus

from booruflow.domain.booru_sites import resolve_category
from booruflow.infrastructure import gelbooru_tag_importer as tags_importer
from booruflow.infrastructure.gelbooru_aliases import (
    ALIAS_URL,
    AliasPage,
    AliasRelation,
    GelbooruAliasSynchronizer,
    catalog_operation_lock,
    fetch_alias_html,
    pager_geometry,
    parse_alias_page,
)

EXPORT_KIND = "booruflow-gelbooru-wiki"
SCHEMA_VERSION = "1"
ALLOWED_CATEGORIES = tuple(
    resolve_category("gelbooru", role) for role in ("general", "artist", "copyright", "character")
)
SCHEMA = """
CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    post_count INTEGER NOT NULL CHECK(post_count >= 0),
    category INTEGER NOT NULL CHECK(category IN (0,1,3,4)),
    ambiguous INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE aliases (
    alias TEXT PRIMARY KEY,
    canonical_tag TEXT NOT NULL REFERENCES tags(name),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status='active'),
    chain_length INTEGER NOT NULL CHECK(chain_length > 0)
);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX idx_tags_category_count ON tags(category, post_count DESC);
CREATE INDEX idx_tags_post_count ON tags(post_count DESC);
CREATE INDEX idx_aliases_canonical ON aliases(canonical_tag);
"""


@dataclass(frozen=True, slots=True)
class WikiExportSummary:
    destination: Path
    tags: int
    aliases: int
    rejected_aliases: dict[str, int]


def redact(message: object, *secrets: str) -> str:
    value = str(message)
    for secret in secrets:
        if secret:
            for encoded in {secret, quote(secret, safe=""), quote_plus(secret)}:
                value = value.replace(encoded, "[redacted]")
    return value


def format_export_error(error: BaseException, *secrets: str) -> str:
    """Show exception chains and frame locations, never source text or local values."""
    lines: list[str] = []
    seen: set[int] = set()

    def append(current: BaseException) -> None:
        if id(current) in seen:
            return
        seen.add(id(current))
        previous = current.__cause__
        if previous is None and not current.__suppress_context__:
            previous = current.__context__
        if previous is not None:
            append(previous)
            lines.append("Caused by / during handling:")
        lines.append("Traceback (most recent call last):")
        for frame, line_number in traceback.walk_tb(current.__traceback__):
            lines.append(
                f'  File "{frame.f_code.co_filename}", line {line_number}, '
                f"in {frame.f_code.co_name}"
            )
        lines.append(f"{type(current).__name__}: {current}")

    append(error)
    return redact("\n".join(lines), *secrets)


def _readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def check_output(destination: Path, protected_paths: tuple[Path, ...] = ()) -> None:
    """Refuse production paths and replacement of files that are not our exports."""
    if destination.is_symlink():
        raise ValueError("The output must not be a symbolic link")
    resolved = destination.resolve()
    if resolved in {path.resolve() for path in protected_paths}:
        raise ValueError("The output is a protected BooruFlow database")
    if resolved.suffix.lower() != ".db":
        raise ValueError("The output filename must end in .db")
    if any(Path(str(resolved) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("The output has SQLite sidecars; close its readers/writers first")
    if not resolved.exists():
        return
    if not resolved.is_file() or resolved.stat().st_nlink > 1:
        raise ValueError("The output must be an ordinary file with no hard links")
    try:
        with closing(_readonly(resolved)) as connection:
            marker = connection.execute(
                "SELECT value FROM metadata WHERE key='export_kind'"
            ).fetchone()
            if marker != (EXPORT_KIND,):
                raise ValueError("Refusing to replace a file that is not a BooruFlow Wiki export")
    except sqlite3.Error as exc:
        raise ValueError("Refusing to replace an unrecognized existing database") from exc


class _AliasObservation:
    """Validate pages before import; retain active conflicts before upsert can hide them."""

    def __init__(self) -> None:
        self.first: AliasPage | None = None
        self.last_pid = 0
        self.page_size = 0
        self.graph: dict[str, str] = {}
        self.blocked: set[str] = set()
        self.spellings: dict[str, str] = {}
        self.signatures: set[tuple] = set()
        self.pending = 0

    def parse(self, text: str, pid: int) -> AliasPage:
        # parse_qs already decodes URL escaping once. Do not lowercase, unquote again
        # (which corrupts literal '+' and '%'), or turn spaces into underscores.
        page = parse_alias_page(text, pid, name_normalizer=lambda name: name)
        if not page.relations:
            raise ValueError(f"Empty or invalid alias page at pid={pid}")
        if self.first is None:
            self.first = page
            self.last_pid, self.page_size, _count = pager_geometry(page)
        if pid > self.last_pid or pid % self.page_size:
            raise ValueError("Unexpected alias page offset")
        expected = self.page_size
        if len(page.relations) > expected or (
            pid < self.last_pid and len(page.relations) != expected
        ):
            raise ValueError(f"Incomplete alias page at pid={pid}")
        if self.last_pid and max((*page.pager_pids, pid)) != self.last_pid:
            raise ValueError("Alias pagination changed during download")
        signature = tuple((r.source_name, r.target_name, r.status) for r in page.relations)
        if signature in self.signatures:
            raise ValueError("Repeated alias page; pagination may have been ignored")
        self.signatures.add(signature)
        for relation in page.relations:
            self.add(relation)
        return page

    def add(self, relation: AliasRelation) -> None:
        if relation.status != "active":
            self.pending += int(relation.status == "pending")
            return
        source, target = relation.source_name, relation.target_name
        if not source or not target:
            raise ValueError("An active alias has an empty name or target")
        previous = self.graph.get(source)
        if previous is not None and previous != target:
            self.blocked.add(source)
        self.graph[source] = target
        spelling = self.spellings.setdefault(source.casefold(), source)
        if spelling != source:
            self.blocked.update((spelling, source))

    def verify_first(self, text: str) -> None:
        page = parse_alias_page(text, 0, name_normalizer=lambda name: name)
        if page != self.first:
            raise ValueError("Alias listing changed during download; retry a fresh export")


def _resolve_aliases(
    connection: sqlite3.Connection,
    observation: _AliasObservation,
) -> tuple[list[tuple[str, str, str, int]], Counter]:
    graph, blocked = observation.graph, observation.blocked
    memo: dict[str, tuple[str | None, int, str]] = {}
    rejected: Counter = Counter()
    rows = []
    for source in graph:
        current = source
        trail: list[str] = []
        visited: set[str] = set()
        while True:
            if current in memo:
                canonical, depth, reason = memo[current]
                break
            if current in blocked:
                canonical, depth, reason = None, 0, "ambiguous"
                break
            if current in visited:
                canonical, depth, reason = None, 0, "cycle"
                break
            if current not in graph:
                valid = connection.execute(
                    "SELECT name FROM tags WHERE name=?", (current,)
                ).fetchone()
                canonical, depth, reason = (
                    (current, 0, "") if valid else (None, 0, "excluded_or_missing")
                )
                break
            visited.add(current)
            trail.append(current)
            current = graph[current]
        for node in reversed(trail):
            depth += 1
            memo[node] = canonical, depth, reason
        if canonical is None:
            rejected[reason] += 1
        else:
            rows.append((source, canonical, "active", memo[source][1]))
    return rows, rejected


def _build_export(
    staging: Path,
    tag_database: Path,
    observation: _AliasObservation,
    metadata: dict[str, str],
) -> WikiExportSummary:
    with closing(_readonly(tag_database)) as source:
        if source.execute(
            "SELECT name FROM tags GROUP BY name COLLATE NOCASE HAVING COUNT(*)>1 LIMIT 1"
        ).fetchone():
            raise ValueError("Tag catalogue contains ambiguous canonical names; export refused")
        if source.execute(
            "SELECT 1 FROM tags WHERE trim(name)='' OR name IS NULL LIMIT 1"
        ).fetchone():
            raise ValueError("Tag catalogue contains an empty canonical name")
        with closing(sqlite3.connect(staging)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(SCHEMA)
            with connection:
                connection.executemany(
                    "INSERT INTO tags VALUES(?,?,?,?,?)",
                    source.execute(
                        "SELECT id,name,post_count,category,ambiguous FROM tags "
                        "WHERE category IN (0,1,3,4)"
                    ),
                )
                # Active alias sources cannot also be usable canonical entries.
                connection.executemany(
                    "DELETE FROM tags WHERE name=?", ((name,) for name in observation.graph)
                )
                count = connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
                categories = {
                    row[0] for row in connection.execute("SELECT DISTINCT category FROM tags")
                }
                if not count or categories != set(ALLOWED_CATEGORIES):
                    raise ValueError("Export lacks tags or one of the four required categories")
                rows, rejected = _resolve_aliases(connection, observation)
                if not rows:
                    raise ValueError("No valid active alias survived; export refused")
                connection.executemany("INSERT INTO aliases VALUES(?,?,?,?)", rows)
                metadata.update(
                    {
                        "export_kind": EXPORT_KIND,
                        "schema_version": SCHEMA_VERSION,
                        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                        "tag_count": str(count),
                        "alias_count": str(len(rows)),
                        "pending_alias_count": str(observation.pending),
                        "rejected_aliases": json.dumps(dict(rejected), sort_keys=True),
                    }
                )
                connection.executemany("INSERT INTO metadata VALUES(?,?)", metadata.items())
            validate_export(connection)
    return WikiExportSummary(staging, count, len(rows), dict(rejected))


def validate_export(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("Wiki export failed SQLite integrity_check")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Wiki export contains invalid foreign keys")
    invalid = connection.execute(
        "SELECT 1 FROM aliases a LEFT JOIN tags t ON t.name=a.canonical_tag "
        "WHERE t.name IS NULL OR EXISTS(SELECT 1 FROM tags WHERE name=a.alias) "
        "OR EXISTS(SELECT 1 FROM aliases WHERE alias=a.canonical_tag) LIMIT 1"
    ).fetchone()
    if invalid:
        raise ValueError("Wiki export contains an orphan, collision, or unresolved alias chain")
    for table, key in (("tags", "tag_count"), ("aliases", "alias_count")):
        count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        stored = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        if count <= 0 or stored != (str(count),):
            raise ValueError(f"Wiki export has an invalid {table} count")


@dataclass(frozen=True, slots=True)
class CatalogueStatus:
    path: Path
    kind: str
    count: int
    state: dict[str, object]
    successful_at: str | None
    fresh: bool


def _timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def catalogue_status(path: Path, kind: str, now: datetime) -> CatalogueStatus:
    """Read producer metadata only: file mtime is never proof of a successful sync."""
    if not path.exists():
        return CatalogueStatus(path, kind, 0, {}, None, False)
    state_table = "import_state" if kind == "tags" else "alias_sync_state"
    data_table = "tags" if kind == "tags" else "gelbooru_aliases"
    with closing(_readonly(path)) as connection:
        count = int(connection.execute(f"SELECT COUNT(*) FROM {data_table}").fetchone()[0])
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        state = (
            dict(connection.execute(f"SELECT key,value FROM {state_table}"))
            if state_table in tables
            else {}
        )
    if kind == "aliases":
        for key, value in state.items():
            try:
                state[key] = json.loads(value)
            except (ValueError, TypeError):
                pass
        keys = (
            ("last_successful_sync_at",)
            if "last_successful_sync_at" in state
            else (
                "last_full_sync",
                "last_incremental_sync",
            )
        )
        complete = not state.get("sync_in_progress") and not state.get("initial_in_progress")
    else:
        keys = (
            ("last_successful_update_at",)
            if "last_successful_update_at" in state
            else ("completed_at",)
        )
        complete = state.get("completed") == "1" and state.get("update_in_progress", "0") == "0"
    values = [state[key] for key in keys if key in state]
    parsed = [_timestamp(value) for value in values]
    # Invalid, naive or future dates are unknown, never inferred from file timestamps.
    successful = max(parsed) if parsed and all(value is not None for value in parsed) else None
    fresh = bool(
        complete
        and count > 0
        and successful
        and timedelta(0) <= now - successful < timedelta(hours=24)
    )
    return CatalogueStatus(
        path, kind, count, state, successful.isoformat() if successful else None, fresh
    )


def snapshot_catalogue(source: Path, destination: Path) -> None:
    """SQLite backup includes committed WAL data and never copies sidecar files."""
    if source.resolve() == destination.resolve() or destination.exists():
        raise ValueError("Snapshot destination must be a distinct new file")
    with closing(_readonly(source)) as reader, closing(sqlite3.connect(destination)) as writer:
        reader.backup(writer, pages=256)
        if writer.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Invalid SQLite catalogue snapshot")


def _synchronize_tags(
    database: Path,
    user_id: str,
    api_key: str,
    *,
    progress: Callable[[str], None],
    tag_fetcher: Callable | None,
    maximum_id_fetcher: Callable | None,
    minimum_rows: int,
    retries: int,
) -> None:
    initial = not database.is_file()
    if not user_id.strip() or not api_key.strip():
        raise ValueError("Gelbooru credentials are required to synchronize the tag catalogue")
    progress(
        "Tags : initialisation complète nécessaire (opération longue)"
        if initial
        else "Tags : mise à jour incrémentale des nouveaux ID ; compteurs historiques non rafraîchis"
    )
    database.parent.mkdir(parents=True, exist_ok=True)
    maximum = (maximum_id_fetcher or partial(tags_importer.fetch_maximum_id, strict=True))(
        user_id, api_key
    )
    if maximum <= 0:
        raise ValueError("Invalid or empty Gelbooru maximum-ID response")
    response_metadata: dict[str, object] = {}
    fetch = tag_fetcher or partial(
        tags_importer.fetch_page, strict=True, response_metadata=response_metadata
    )
    seen = 0
    declared_count: int | None = None

    def checked(cursor: int, uid: str, key: str) -> list[dict]:
        nonlocal seen, declared_count
        values = fetch(cursor, uid, key)
        if not isinstance(values, list):
            raise TypeError("Invalid Gelbooru tag response")
        if declared_count is None and "count" in response_metadata:
            declared_count = int(response_metadata["count"])
            if declared_count < 0:
                raise ValueError("Invalid Gelbooru declared tag count")
        previous = cursor
        for value in values:
            if not isinstance(value, dict) or not value.get("name"):
                raise ValueError("Malformed Gelbooru tag row")
            identifier = int(value["id"])
            if identifier <= previous:
                raise ValueError("Gelbooru tag IDs are not strictly ascending")
            previous = identifier
            if "type" not in value and "category" not in value:
                raise ValueError("Tag category is missing")
            if "count" not in value and "post_count" not in value:
                raise ValueError("Tag post count is missing")
            if int(value.get("count", value.get("post_count"))) < 0:
                raise ValueError("Negative tag post count")
        seen += len(values)
        if not values:
            if cursor < maximum:
                raise ValueError("Tag download ended before the reference maximum ID")
            if declared_count is not None and seen < declared_count:
                raise ValueError("Imported fewer tags than the source declared; incomplete source")
        return values

    if initial:
        tags_importer.rebuild_database(
            database,
            user_id,
            api_key,
            fetcher=checked,
            progress=progress,
            minimum_rows=minimum_rows,
            required_tags=(),
            retries=retries,
            maximum_id=maximum,
        )
    else:
        try:
            tags_importer.update_database(
                database,
                user_id,
                api_key,
                fetcher=checked,
                progress=progress,
                retries=retries,
                maximum_id=maximum,
            )
        except tags_importer.DatabaseUpdateUnavailable as exc:
            raise ValueError(
                "Le catalogue Tags ne permet pas la mise à jour incrémentale. "
                "Une reconstruction complète via BooruFlow est nécessaire (opération longue)."
            ) from exc


def _synchronize_aliases(
    database: Path,
    *,
    progress: Callable[[str], None],
    alias_fetcher: Callable | None,
    delay: float,
) -> None:
    initial = not database.is_file()
    database.parent.mkdir(parents=True, exist_ok=True)
    observation = _AliasObservation()
    fetch_aliases = alias_fetcher or fetch_alias_html

    def checked(text: str, pid: int) -> AliasPage:
        page = observation.parse(text, pid)
        if observation.blocked:
            raise ValueError("Ambiguous active aliases in remote pages; synchronization refused")
        if pid == observation.last_pid:
            observation.verify_first(fetch_aliases(0, ""))
        return page

    synchronizer = GelbooruAliasSynchronizer(
        database,
        alias_fetcher or fetch_alias_html,
        delay=delay,
        progress=progress,
        page_parser=checked,
    )
    if initial:
        progress("Alias : initialisation complète nécessaire")
        result = synchronizer.initial_import()
    else:
        progress("Alias : mise à jour incrémentale")
        result = synchronizer.incremental()
        if result.state in {
            "initial_import_required",
            "overlap_not_found",
            "pagination_incoherent",
        }:
            progress(
                f"Alias : {result.state} ; réconciliation complète nécessaire (opération longue)"
            )
            observation = _AliasObservation()
            result = synchronizer.full_reconciliation()
    if result.state != "completed":
        raise ValueError(f"Alias synchronization did not complete: {result.state}")


def _alias_snapshot_observation(database: Path) -> _AliasObservation:
    observation = _AliasObservation()
    with closing(_readonly(database)) as connection:
        for source, target, status in connection.execute(
            "SELECT source_name,target_name,status FROM gelbooru_aliases"
        ):
            if status not in {"active", "pending", "missing"}:
                raise ValueError("Invalid stored alias status")
            observation.add(AliasRelation(source, target, status))
    return observation


def export_wiki_database(
    destination: Path,
    user_id: str = "",
    api_key: str = "",
    *,
    tags_database: Path,
    aliases_database: Path,
    force_update: bool = False,
    protected_paths: tuple[Path, ...] = (),
    tag_fetcher: Callable | None = None,
    maximum_id_fetcher: Callable | None = None,
    alias_fetcher: Callable | None = None,
    minimum_rows: int = 100_000,
    retries: int = 6,
    alias_delay: float = 0.25,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    progress: Callable[[str], None] = print,
) -> WikiExportSummary:
    """Update normal catalogues only when stale, then merge their SQLite snapshots."""
    tags_database, aliases_database = tags_database.resolve(), aliases_database.resolve()
    if tags_database == aliases_database:
        raise ValueError("Tags and aliases must use separate configured catalogues")
    protected_paths = (*protected_paths, tags_database, aliases_database)
    check_output(destination, protected_paths)
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = lambda message: progress(redact(message, api_key, user_id))
    started_at = now().isoformat(timespec="seconds")
    with catalog_operation_lock(destination):
        check_output(destination, protected_paths)
        for kind, path in (("tags", tags_database), ("aliases", aliases_database)):
            path.parent.mkdir(parents=True, exist_ok=True)
            with catalog_operation_lock(path):
                status = catalogue_status(path, kind, now())
            label = "Tags" if kind == "tags" else "Alias"
            state = (
                "absent"
                if not path.is_file()
                else "récent"
                if status.fresh
                else "mise à jour nécessaire"
            )
            report(
                f"{label} : {state} | disponibles {status.count:,} | réussite {status.successful_at or 'inconnue'}"
            )
            if status.fresh and not force_update:
                continue
            if force_update:
                report(f"{label} : actualisation forcée")
            if kind == "tags":
                _synchronize_tags(
                    path,
                    user_id,
                    api_key,
                    progress=report,
                    tag_fetcher=tag_fetcher,
                    maximum_id_fetcher=maximum_id_fetcher,
                    minimum_rows=minimum_rows,
                    retries=retries,
                )
            else:
                _synchronize_aliases(
                    path, progress=report, alias_fetcher=alias_fetcher, delay=alias_delay
                )
            updated = catalogue_status(path, kind, now())
            if not updated.fresh:
                raise ValueError(f"{label} : synchronisation sans date de réussite valide")
            report(
                f"{label} : actualisé | disponibles {updated.count:,} | réussite {updated.successful_at}"
            )
        with TemporaryDirectory(prefix=".gelbooru-wiki-", dir=destination.parent) as directory:
            temporary = Path(directory)
            # Normal writers use these same locks. Recheck metadata under the locks
            # before making both snapshots; never hold a nested lock during sync.
            with ExitStack() as stack:
                for source in sorted((tags_database, aliases_database)):
                    stack.enter_context(catalog_operation_lock(source))
                snapshots = {}
                for kind, source in (("tags", tags_database), ("aliases", aliases_database)):
                    if not catalogue_status(source, kind, now()).fresh:
                        raise ValueError(
                            f"{kind} catalogue changed or synchronization was interrupted before snapshot"
                        )
                    copy = temporary / f"{kind}.db"
                    snapshot_catalogue(source, copy)
                    copied_status = catalogue_status(copy, kind, now())
                    if not copied_status.fresh:
                        raise ValueError(f"{kind} snapshot has no valid successful synchronization")
                    snapshots[kind] = copied_status
            tag_copy, alias_copy = snapshots["tags"], snapshots["aliases"]
            if tag_copy.count < minimum_rows:
                raise ValueError(
                    f"Only {tag_copy.count:,} source tags; expected at least {minimum_rows:,}"
                )
            report(f"Copies SQLite validées | tags {tag_copy.count:,} | alias {alias_copy.count:,}")
            report("Début de la fusion des copies et de la validation")
            observation = _alias_snapshot_observation(alias_copy.path)
            metadata = {
                "source_tags_url": tags_importer.API_URL + "?page=dapi&s=tag&q=index&json=1",
                "source_aliases_url": ALIAS_URL + "?page=alias&s=list",
                "source_started_at": started_at,
                "source_finished_at": now().isoformat(timespec="seconds"),
                "source_remote_modified_at": "unavailable",
                "source_tags_database": str(tags_database),
                "source_aliases_database": str(aliases_database),
                "source_tags_sync_at": tag_copy.successful_at,
                "source_aliases_sync_at": alias_copy.successful_at,
                "source_tags_sync_mode": str(
                    tag_copy.state.get("last_successful_update_mode", "legacy_unknown")
                ),
                "source_aliases_sync_mode": str(
                    alias_copy.state.get("last_successful_sync_mode", "legacy_unknown")
                ),
                "source_tags_full_sync_at": str(tag_copy.state.get("completed_at", "unavailable")),
                "source_aliases_full_sync_at": str(
                    alias_copy.state.get("last_full_sync", "unavailable")
                ),
                "source_tag_checkpoint": str(tag_copy.state.get("after_id", "unavailable")),
                "source_tags_imported": str(tag_copy.count),
                "source_aliases_available": str(alias_copy.count),
                "source_alias_pages": str(
                    alias_copy.state.get("observed_page_count", "unavailable")
                ),
                "source_snapshot_method": "sqlite_backup",
                "tag_importer_version": tags_importer.IMPORT_VERSION,
            }
            result = _build_export(temporary / "export.db", tag_copy.path, observation, metadata)
            with result.destination.open("rb+") as stream:
                os.fsync(stream.fileno())
            check_output(destination, protected_paths)
            os.replace(result.destination, destination)
            report(f"Fin de la fusion | tags {result.tags:,} | alias {result.aliases:,}")
            report(f"Alias actifs exclus : {json.dumps(result.rejected_aliases, sort_keys=True)}")
            report(f"Export : {destination}")
            return WikiExportSummary(
                destination, result.tags, result.aliases, result.rejected_aliases
            )
