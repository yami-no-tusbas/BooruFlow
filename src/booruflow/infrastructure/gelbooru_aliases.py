"""Local Gelbooru alias catalogue, HTML parser and conservative synchronisation."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

ALIAS_URL = "https://gelbooru.com/index.php"
ALIAS_SCHEMA_VERSION = "1"
CHECKPOINT_VERSION = "1"
ALIAS_SCHEMA = """
CREATE TABLE IF NOT EXISTS gelbooru_aliases (
    source_name TEXT NOT NULL COLLATE NOCASE,
    target_name TEXT NOT NULL COLLATE NOCASE,
    status TEXT NOT NULL CHECK(status IN ('active','pending','missing')),
    first_seen_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    last_checked_at TEXT NOT NULL,
    missing_reason TEXT,
    source_page_pid INTEGER,
    source_order INTEGER,
    PRIMARY KEY(source_name,target_name)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_gelbooru_alias_active_source
ON gelbooru_aliases(source_name COLLATE NOCASE) WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_gelbooru_alias_status ON gelbooru_aliases(status);
CREATE TABLE IF NOT EXISTS alias_sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def normalize_alias_name(value: str) -> str:
    return urllib.parse.unquote_plus(str(value)).strip().replace(" ", "_").casefold()


@dataclass(frozen=True, slots=True)
class AliasRelation:
    source_name: str
    target_name: str
    status: str
    source_page_pid: int | None = None
    source_order: int | None = None

    @property
    def checkpoint_key(self) -> tuple[str, str, str]:
        return (
            normalize_alias_name(self.source_name),
            normalize_alias_name(self.target_name),
            self.status,
        )


@dataclass(frozen=True, slots=True)
class AliasPage:
    relations: tuple[AliasRelation, ...]
    pager_pids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class AliasSyncSummary:
    state: str
    active: int
    pending: int
    missing: int
    new: int = 0
    modified: int = 0
    checkpoint_size: int = 0
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AliasCatalogStatus:
    """Read-only health information for one explicitly configured catalogue."""

    path: Path | None
    available: bool
    active_aliases: int | None
    reason: str | None = None


def inspect_alias_catalog(path: Path | None) -> AliasCatalogStatus:
    """Inspect one path without creating, migrating, or selecting another DB."""
    if path is None:
        return AliasCatalogStatus(None, False, None, "no catalogue configured")
    database = Path(path)
    if not database.is_file():
        return AliasCatalogStatus(database, False, None, "catalogue file is unavailable")
    try:
        uri = f"file:{database.resolve().as_posix()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            required = {"gelbooru_aliases", "alias_sync_state"}
            if not required.issubset(tables):
                missing = ", ".join(sorted(required - tables))
                return AliasCatalogStatus(database, False, None, f"missing schema: {missing}")
            active = connection.execute(
                "SELECT COUNT(*) FROM gelbooru_aliases WHERE status='active'"
            ).fetchone()[0]
            return AliasCatalogStatus(database, True, int(active))
    except (OSError, sqlite3.Error) as exc:
        return AliasCatalogStatus(database, False, None, str(exc))


class AliasPageParser(HTMLParser):
    def __init__(self, pid: int = 0) -> None:
        super().__init__(convert_charrefs=True)
        self.pid = pid
        self._in_row = False
        self._pending = False
        self._row_tags: list[str] = []
        self._relations: list[AliasRelation] = []
        self._pager_pids: set[int] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        if tag.casefold() == "tr":
            self._in_row = True
            self._pending = "pending-tag" in classes
            self._row_tags = []
        elif self._in_row and "pending-tag" in classes:
            self._pending = True
        if tag.casefold() != "a":
            return
        href = values.get("href", "").replace("&amp;", "&")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        for raw_pid in query.get("pid", []):
            try:
                self._pager_pids.add(int(raw_pid))
            except ValueError:
                pass
        if self._in_row and query.get("tags"):
            self._row_tags.append(normalize_alias_name(query["tags"][0]))

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "tr" or not self._in_row:
            return
        if len(self._row_tags) >= 2:
            source, target = self._row_tags[:2]
            if source and target:
                self._relations.append(
                    AliasRelation(
                        source,
                        target,
                        "pending" if self._pending else "active",
                        self.pid,
                        len(self._relations),
                    )
                )
        self._in_row = False

    def result(self) -> AliasPage:
        return AliasPage(tuple(self._relations), tuple(sorted(self._pager_pids)))


def parse_alias_page(html: str, pid: int = 0) -> AliasPage:
    parser = AliasPageParser(pid)
    parser.feed(html)
    parser.close()
    return parser.result()


def pager_geometry(page: AliasPage) -> tuple[int, int, int]:
    positive = sorted({pid for pid in page.pager_pids if pid > 0})
    if not positive:
        if len(page.relations) < 50:
            return 0, max(1, len(page.relations)), 1
        raise ValueError("alias pager missing or page size indeterminate")
    steps = [b - a for a, b in zip((0, *positive), positive) if b > a]
    page_size = min(steps)
    last_pid = max(positive)
    if page_size <= 0 or last_pid % page_size:
        raise ValueError("incoherent alias pager")
    return last_pid, page_size, last_pid // page_size + 1


def ensure_alias_schema(database: Path | sqlite3.Connection) -> None:
    own = not isinstance(database, sqlite3.Connection)
    connection = sqlite3.connect(database) if own else database
    try:
        connection.executescript(ALIAS_SCHEMA)
        connection.execute(
            "INSERT OR REPLACE INTO alias_sync_state(key,value) VALUES('schema_version',?)",
            (ALIAS_SCHEMA_VERSION,),
        )
        connection.commit()
    finally:
        if own:
            connection.close()


def _validate_alias_invariants(connection: sqlite3.Connection) -> None:
    version = connection.execute(
        "SELECT value FROM alias_sync_state WHERE key='schema_version'"
    ).fetchone()
    if version is None or version[0] != ALIAS_SCHEMA_VERSION:
        raise RuntimeError("unsupported alias schema")
    duplicate = connection.execute(
        "SELECT source_name FROM gelbooru_aliases WHERE status='active' "
        "GROUP BY source_name COLLATE NOCASE HAVING COUNT(*)>1 LIMIT 1"
    ).fetchone()
    if duplicate:
        raise RuntimeError(f"multiple active alias targets for {duplicate[0]}")


def copy_alias_catalog(source: Path, staging: Path) -> None:
    """Copy aliases into a dedicated alias staging DB, or fail without activation."""
    target = sqlite3.connect(staging)
    try:
        ensure_alias_schema(target)
        if not source.is_file():
            return
        with source.open("rb") as stream:
            header = stream.read(16)
        if header != b"SQLite format 3\x00":
            return
        old = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
        try:
            tables = {
                row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not ({"gelbooru_aliases", "alias_sync_state"} & tables):
                return
            if not {"gelbooru_aliases", "alias_sync_state"}.issubset(tables):
                raise RuntimeError("partial alias schema in existing catalogue")
            source_version = old.execute(
                "SELECT value FROM alias_sync_state WHERE key='schema_version'"
            ).fetchone()
            if source_version is None or source_version[0] != ALIAS_SCHEMA_VERSION:
                raise RuntimeError("unsupported alias schema in existing catalogue")
            aliases = old.execute(
                "SELECT source_name,target_name,status,first_seen_at,observed_at,"
                "last_checked_at,missing_reason,source_page_pid,source_order "
                "FROM gelbooru_aliases"
            ).fetchall()
            sync_state = old.execute("SELECT key,value FROM alias_sync_state").fetchall()
        finally:
            old.close()
        target.execute("BEGIN IMMEDIATE")
        target.execute("DELETE FROM gelbooru_aliases")
        target.execute("DELETE FROM alias_sync_state")
        target.executemany("INSERT INTO gelbooru_aliases VALUES(?,?,?,?,?,?,?,?,?)", aliases)
        target.executemany("INSERT INTO alias_sync_state VALUES(?,?)", sync_state)
        _validate_alias_invariants(target)
        actual_aliases = target.execute("SELECT COUNT(*) FROM gelbooru_aliases").fetchone()[0]
        actual_state = target.execute("SELECT COUNT(*) FROM alias_sync_state").fetchone()[0]
        if (actual_aliases, actual_state) != (len(aliases), len(sync_state)):
            raise RuntimeError("incomplete alias catalogue copy")
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        target.close()


def migrate_alias_catalog(source: Path | None, destination: Path) -> bool:
    """Create the dedicated catalogue from one explicitly configured legacy DB.

    This migration never searches sibling files and never overwrites an already
    dedicated alias catalogue.  Its temporary file is atomically activated only
    after ``copy_alias_catalog`` has completed its validation.
    """
    if destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.migrating")
    try:
        copy_alias_catalog(source or Path(), staging)
        os.replace(staging, destination)
        return True
    except Exception:
        if staging.exists():
            staging.unlink()
        raise


@contextmanager
def catalog_operation_lock(database: Path) -> Iterator[None]:
    lock = database.with_name(database.name + ".operation.lock")
    descriptor: int | None = None
    for attempt in range(2):
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError as exc:
            try:
                owner = int(lock.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                owner = 0
            alive = True
            if owner > 0:
                try:
                    os.kill(owner, 0)
                except OSError:
                    alive = False
            if attempt == 0 and owner > 0 and not alive:
                lock.unlink(missing_ok=True)
                continue
            raise RuntimeError(f"catalogue operation already running: {database}") from exc
    if descriptor is None:
        raise RuntimeError(f"could not lock catalogue: {database}")
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)


class GelbooruAliasRepository:
    def __init__(self, database: Path) -> None:
        self.database = Path(database)

    def migrate(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        ensure_alias_schema(self.database)

    def state(self) -> dict[str, object]:
        self.migrate()
        connection = sqlite3.connect(self.database)
        try:
            result: dict[str, object] = {}
            for key, raw in connection.execute("SELECT key,value FROM alias_sync_state"):
                try:
                    result[key] = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    result[key] = raw
            return result
        finally:
            connection.close()

    def set_state(
        self, values: dict[str, object], connection: sqlite3.Connection | None = None
    ) -> None:
        own = connection is None
        db = sqlite3.connect(self.database) if own else connection
        try:
            db.executemany(
                "INSERT OR REPLACE INTO alias_sync_state(key,value) VALUES(?,?)",
                [(key, json.dumps(value, ensure_ascii=False)) for key, value in values.items()],
            )
            if own:
                db.commit()
        finally:
            if own:
                db.close()

    def upsert(
        self, relation: AliasRelation, connection: sqlite3.Connection | None = None
    ) -> tuple[int, int]:
        own = connection is None
        db = sqlite3.connect(self.database) if own else connection
        timestamp = now_iso()
        try:
            existing = db.execute(
                "SELECT status,target_name FROM gelbooru_aliases "
                "WHERE source_name=? AND target_name=?",
                (relation.source_name, relation.target_name),
            ).fetchone()
            modified = int(existing is not None and existing[0] != relation.status)
            if relation.status in {"active", "pending"}:
                changed = db.execute(
                    "UPDATE gelbooru_aliases SET status='missing',missing_reason='target_changed',"
                    "last_checked_at=? WHERE source_name=? AND target_name<>? "
                    "AND status IN ('active','pending')",
                    (timestamp, relation.source_name, relation.target_name),
                ).rowcount
                modified += changed
            db.execute(
                "INSERT INTO gelbooru_aliases(source_name,target_name,status,first_seen_at,"
                "observed_at,last_checked_at,missing_reason,source_page_pid,source_order) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source_name,target_name) DO UPDATE SET "
                "status=excluded.status,observed_at=excluded.observed_at,"
                "last_checked_at=excluded.last_checked_at,missing_reason=NULL,"
                "source_page_pid=excluded.source_page_pid,source_order=excluded.source_order",
                (
                    relation.source_name,
                    relation.target_name,
                    relation.status,
                    timestamp,
                    timestamp,
                    timestamp,
                    None,
                    relation.source_page_pid,
                    relation.source_order,
                ),
            )
            if own:
                db.commit()
            return int(existing is None), modified
        finally:
            if own:
                db.close()

    def mark_missing(self, source: str, target: str, reason: str) -> None:
        self.migrate()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "UPDATE gelbooru_aliases SET status='missing',missing_reason=?,last_checked_at=? "
                "WHERE source_name=? AND target_name=?",
                (reason, now_iso(), source, target),
            )

    def active_target(self, name: str) -> str | None:
        if not self.database.is_file():
            return None
        connection = sqlite3.connect(f"file:{self.database.resolve().as_posix()}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT target_name FROM gelbooru_aliases WHERE source_name=? AND status='active'",
                (normalize_alias_name(name),),
            ).fetchone()
            return str(row[0]) if row else None
        except sqlite3.OperationalError:
            return None
        finally:
            connection.close()

    def active_sources_matching(self, text: str, *, limit: int = 20) -> list[str]:
        """Return bounded active alias sources for autocomplete, read-only."""
        if not self.database.is_file():
            return []
        query = normalize_alias_name(text)
        if not query:
            return []
        try:
            connection = sqlite3.connect(
                f"file:{self.database.resolve().as_posix()}?mode=ro", uri=True
            )
            try:
                rows = connection.execute(
                    "SELECT source_name FROM gelbooru_aliases "
                    "WHERE status='active' AND source_name LIKE ? ESCAPE '\\' "
                    "ORDER BY source_name COLLATE NOCASE LIMIT ?",
                    (
                        f"%{query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')}%",
                        max(1, min(limit, 100)),
                    ),
                ).fetchall()
                return [str(row[0]) for row in rows]
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            return []

    def pending(self) -> list[AliasRelation]:
        self.migrate()
        connection = sqlite3.connect(self.database)
        try:
            return [
                AliasRelation(*row)
                for row in connection.execute(
                    "SELECT source_name,target_name,status,source_page_pid,source_order "
                    "FROM gelbooru_aliases WHERE status='pending' ORDER BY source_name"
                )
            ]
        finally:
            connection.close()

    def counts(self) -> dict[str, int]:
        self.migrate()
        result = {status: 0 for status in ("active", "pending", "missing")}
        with closing(sqlite3.connect(self.database)) as connection:
            result.update(
                {
                    str(status): int(count)
                    for status, count in connection.execute(
                        "SELECT status,COUNT(*) FROM gelbooru_aliases GROUP BY status"
                    )
                }
            )
        return result

    def checkpoint(self, size: int = 20) -> list[tuple[str, str, str]]:
        self.migrate()
        with closing(sqlite3.connect(self.database)) as connection:
            rows = connection.execute(
                "SELECT source_name,target_name,status FROM gelbooru_aliases "
                "WHERE status<>'missing' ORDER BY source_page_pid DESC,source_order DESC LIMIT ?",
                (size,),
            ).fetchall()
        return [tuple(map(str, row)) for row in reversed(rows)]


def resolve_gelbooru_alias_with_diagnostic(
    name: str, database: Path, *, maximum_depth: int = 16
) -> tuple[str, str | None]:
    repository = GelbooruAliasRepository(database)
    original = normalize_alias_name(name)
    current = original
    visited: set[str] = set()
    for _depth in range(maximum_depth):
        if current in visited:
            return original, "cycle"
        visited.add(current)
        target = repository.active_target(current)
        if target is None:
            return current, None
        current = normalize_alias_name(target)
    return original, "maximum_depth"


def resolve_gelbooru_alias(name: str, database: Path, *, maximum_depth: int = 16) -> str:
    """Resolve active aliases only; return the original name on unsafe chains."""
    resolved, diagnostic = resolve_gelbooru_alias_with_diagnostic(
        name, database, maximum_depth=maximum_depth
    )
    return normalize_alias_name(name) if diagnostic else resolved


def fetch_alias_html(pid: int = 0, search: str = "", *, retries: int = 3) -> str:
    parameters = {"page": "alias", "s": "list", "pid": pid}
    if search:
        parameters["search"] = search
    request = urllib.request.Request(
        f"{ALIAS_URL}?{urllib.parse.urlencode(parameters)}",
        headers={"User-Agent": "BooruFlow-GelbooruAliasImporter/1.0", "Accept": "text/html"},
    )
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except (OSError, urllib.error.URLError, TimeoutError):
            if attempt >= retries:
                raise
            time.sleep(min(8, 2**attempt))
    raise RuntimeError("unreachable")


class GelbooruAliasSynchronizer:
    def __init__(
        self,
        database: Path,
        fetcher: Callable[[int, str], str] = fetch_alias_html,
        *,
        delay: float = 0.25,
        checkpoint_size: int = 20,
        minimum_overlap: int = 10,
        maximum_back_pages: int = 200,
        progress: Callable[[str], None] = print,
        stopped: Callable[[], bool] = lambda: False,
    ) -> None:
        self.repository = GelbooruAliasRepository(database)
        self.fetcher = fetcher
        self.delay = delay
        self.checkpoint_size = checkpoint_size
        self.minimum_overlap = minimum_overlap
        self.maximum_back_pages = maximum_back_pages
        self.progress = progress
        self.stopped = stopped

    def _page(self, pid: int, search: str = "") -> AliasPage:
        if self.stopped():
            raise InterruptedError("alias synchronization stopped")
        html = self.fetcher(pid, search)
        if self.delay:
            time.sleep(self.delay)
        return parse_alias_page(html, pid)

    def _summary(self, state: str, new: int = 0, modified: int = 0) -> AliasSyncSummary:
        counts = self.repository.counts()
        checkpoint = self.repository.state().get("checkpoint", [])
        return AliasSyncSummary(
            state,
            counts["active"],
            counts["pending"],
            counts["missing"],
            new,
            modified,
            len(checkpoint) if isinstance(checkpoint, list) else 0,
        )

    def initial_import(self) -> AliasSyncSummary:
        with catalog_operation_lock(self.repository.database):
            self.repository.migrate()
            first = self._page(0)
            last_pid, page_size, page_count = pager_geometry(first)
            state = self.repository.state()
            next_pid = int(state.get("initial_next_pid", 0) or 0)
            new = modified = 0
            pages: dict[int, AliasPage] = {0: first}
            for pid in range(next_pid, last_pid + 1, page_size):
                page = pages.get(pid) or self._page(pid)
                with closing(sqlite3.connect(self.repository.database)) as connection, connection:
                    for relation in page.relations:
                        created, changed = self.repository.upsert(relation, connection)
                        new += created
                        modified += changed
                    self.repository.set_state(
                        {
                            "initial_in_progress": True,
                            "initial_next_pid": pid + page_size,
                            "observed_last_pid": last_pid,
                            "observed_page_count": page_count,
                            "page_size": page_size,
                        },
                        connection,
                    )
            self._finish_sync("last_full_sync", last_pid, page_count, page_size)
            return self._summary("completed", new, modified)

    def _finish_sync(
        self, timestamp_key: str, last_pid: int, page_count: int, page_size: int
    ) -> None:
        checkpoint = self.repository.checkpoint(self.checkpoint_size)
        self.repository.set_state(
            {
                "checkpoint_version": CHECKPOINT_VERSION,
                "captured_at": now_iso(),
                "observed_last_pid": last_pid,
                "observed_page_count": page_count,
                "page_size": page_size,
                "checkpoint": checkpoint,
                timestamp_key: now_iso(),
                "initial_in_progress": False,
                "initial_next_pid": 0,
            }
        )

    @staticmethod
    def _find_overlap(
        observed: Sequence[AliasRelation], checkpoint: Sequence[Sequence[str]], minimum: int
    ) -> int | None:
        known = [tuple(map(str, row)) for row in checkpoint]
        if len(known) < 2:
            return None
        required = min(minimum, len(known))
        keys = [row.checkpoint_key for row in observed]
        for known_start in range(len(known) - required + 1):
            needle = known[known_start : known_start + required]
            for start in range(len(keys) - required + 1):
                if keys[start : start + required] == needle:
                    return start + required
        return None

    def incremental(self) -> AliasSyncSummary:
        with catalog_operation_lock(self.repository.database):
            self.repository.migrate()
            state = self.repository.state()
            checkpoint = state.get("checkpoint")
            if not isinstance(checkpoint, list) or len(checkpoint) < 2:
                return self._summary("initial_import_required")
            first = self._page(0)
            last_pid, page_size, page_count = pager_geometry(first)
            previous_last = int(state.get("observed_last_pid", 0) or 0)
            if last_pid < previous_last:
                return self._summary("pagination_incoherent")
            pages: dict[int, AliasPage] = {0: first}
            overlap_pid: int | None = None
            after = 0
            pid = last_pid
            for _index in range(self.maximum_back_pages):
                page = pages.get(pid)
                if page is None:
                    page = self._page(pid)
                    pages[pid] = page
                match = self._find_overlap(page.relations, checkpoint, self.minimum_overlap)
                if match is not None:
                    overlap_pid, after = pid, match
                    break
                if pid == 0:
                    break
                pid = max(0, pid - page_size)
            if overlap_pid is None:
                return self._summary("overlap_not_found")
            observed: list[AliasRelation] = list(pages[overlap_pid].relations[after:])
            for current in range(overlap_pid + page_size, last_pid + 1, page_size):
                page = pages.get(current)
                if page is None:
                    page = self._page(current)
                    pages[current] = page
                observed.extend(page.relations)
            new = modified = 0
            with closing(sqlite3.connect(self.repository.database)) as connection, connection:
                for relation in observed:
                    created, changed = self.repository.upsert(relation, connection)
                    new += created
                    modified += changed
            self._finish_sync("last_incremental_sync", last_pid, page_count, page_size)
            return self._summary("completed", new, modified)

    def revalidate_pending(self) -> AliasSyncSummary:
        with catalog_operation_lock(self.repository.database):
            self.repository.migrate()
            first = self._page(0)
            last_pid, page_size, page_count = pager_geometry(first)
            pending = self.repository.pending()
            if len(pending) >= page_count:
                return self._summary("full_reconciliation_recommended")
            new = modified = 0
            for old in pending:
                matches = [
                    row
                    for row in self._page(0, old.source_name).relations
                    if normalize_alias_name(row.source_name)
                    == normalize_alias_name(old.source_name)
                ]
                if not matches:
                    self.repository.mark_missing(
                        old.source_name, old.target_name, "source_not_found"
                    )
                    modified += 1
                    continue
                relation = next((row for row in matches if row.status == "active"), None)
                relation = relation or next(
                    (row for row in matches if row.target_name == old.target_name), matches[0]
                )
                created, changed = self.repository.upsert(relation)
                new += created
                modified += changed
            self._finish_sync("last_incremental_sync", last_pid, page_count, page_size)
            return self._summary("completed", new, modified)

    def full_reconciliation(self) -> AliasSyncSummary:
        with catalog_operation_lock(self.repository.database):
            self.repository.migrate()
            first = self._page(0)
            last_pid, page_size, page_count = pager_geometry(first)
            observed = list(first.relations)
            for pid in range(page_size, last_pid + 1, page_size):
                observed.extend(self._page(pid).relations)
            new = modified = 0
            timestamp = now_iso()
            with closing(sqlite3.connect(self.repository.database)) as connection, connection:
                connection.execute(
                    "UPDATE gelbooru_aliases SET status='missing',missing_reason='source_not_found',"
                    "last_checked_at=? WHERE status<>'missing'",
                    (timestamp,),
                )
                for relation in observed:
                    created, changed = self.repository.upsert(relation, connection)
                    new += created
                    modified += changed
                _validate_alias_invariants(connection)
            self._finish_sync("last_full_sync", last_pid, page_count, page_size)
            return self._summary("completed", new, modified)
