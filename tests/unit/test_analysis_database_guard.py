from pathlib import Path
from unittest.mock import Mock

import pytest

from booruflow.infrastructure.image_analysis_cache import ImageAnalysisCache
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from tests.conftest import (
    _ACTIVE_CACHE_DATABASE,
    _ACTIVE_DATABASE,
    _ACTIVE_ENTITY_DATABASE,
    _ACTIVE_METADATA_DATABASE,
    guard_sqlite_connect,
)


@pytest.mark.parametrize("database", [
    _ACTIVE_DATABASE,
    _ACTIVE_CACHE_DATABASE,
    _ACTIVE_ENTITY_DATABASE,
    _ACTIVE_METADATA_DATABASE,
    str(_ACTIVE_DATABASE),
    bytes(str(_ACTIVE_DATABASE), "utf-8"),
    _ACTIVE_DATABASE.parent / ".." / "state" / "image_analysis.sqlite",
    _ACTIVE_DATABASE.as_uri(),
    _ACTIVE_DATABASE.as_uri() + "?mode=ro&immutable=1",
    Path("var/state/image_analysis.sqlite"),
])
def test_guard_rejects_active_database_without_calling_sqlite(database):
    # The delegate is a mock, never sqlite3.connect: even a broken guard in
    # this regression test cannot open, migrate or write the actual database.
    delegate = Mock()
    blocked = []
    guarded = guard_sqlite_connect(delegate, blocked)
    with pytest.raises(RuntimeError, match="temporary"):
        guarded(database)
    delegate.assert_not_called()
    assert len(blocked) == 1


def test_guard_allows_temporary_database_and_forwards_options(tmp_path):
    delegate = Mock()
    blocked = []
    guarded = guard_sqlite_connect(delegate, blocked)
    database = tmp_path / "image_analysis.sqlite"
    assert guarded(database, timeout=2.5) is delegate.return_value
    delegate.assert_called_once_with(database, timeout=2.5)
    assert not blocked


def test_repository_environment_guard_blocks_subprocess_style_access(monkeypatch):
    monkeypatch.setenv("BOORUFLOW_TEST_BLOCKED_ANALYSIS_DATABASE", str(_ACTIVE_DATABASE))
    with pytest.raises(RuntimeError, match="temporary"):
        ImageAnalysisRepository(_ACTIVE_DATABASE)


def test_image_cache_environment_guard_blocks_before_sqlite_or_directory_access(monkeypatch):
    monkeypatch.setenv(
        "BOORUFLOW_TEST_BLOCKED_IMAGE_CACHE_DATABASE", str(_ACTIVE_CACHE_DATABASE)
    )
    with pytest.raises(RuntimeError, match="temporary"):
        ImageAnalysisCache(_ACTIVE_CACHE_DATABASE)
