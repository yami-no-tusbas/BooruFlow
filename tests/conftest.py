"""Keep automated SQLite tests away from the workspace's real analysis data."""

import os
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

import pytest

_ACTIVE_DATABASE = Path(__file__).resolve().parents[1] / "var/state/image_analysis.sqlite"
_ACTIVE_CACHE_DATABASE = Path(__file__).resolve().parents[1] / "var/cache/image_analysis_cache.db"


def guard_sqlite_connect(original_connect, blocked):
    """Reject the active path before delegating; this guard never opens a file."""
    def guarded_connect(database, *args, **kwargs):
        value = os.fsdecode(database)
        if value.startswith("file:"):
            value = url2pathname(urlsplit(value).path)
        if Path(value).resolve() in {
            _ACTIVE_DATABASE.resolve(), _ACTIVE_CACHE_DATABASE.resolve(),
        }:
            blocked.append(value)
            raise RuntimeError("Tests must use a temporary image-analysis database")
        return original_connect(database, *args, **kwargs)

    return guarded_connect


@pytest.fixture(autouse=True)
def protect_active_analysis_database(monkeypatch):
    blocked = []
    monkeypatch.setenv("BOORUFLOW_TEST_BLOCKED_ANALYSIS_DATABASE", str(_ACTIVE_DATABASE))
    monkeypatch.setenv(
        "BOORUFLOW_TEST_BLOCKED_IMAGE_CACHE_DATABASE", str(_ACTIVE_CACHE_DATABASE)
    )
    guarded_connect = guard_sqlite_connect(sqlite3.connect, blocked)
    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
    monkeypatch.setattr(sqlite3.dbapi2, "connect", guarded_connect)
    yield
    assert not blocked, "A test attempted to open the real image-analysis database"
