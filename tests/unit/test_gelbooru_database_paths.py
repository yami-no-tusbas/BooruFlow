import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from booruflow.application.database_paths import (
    gelbooru_alias_database,
    gelbooru_tag_database,
    migrate_database_settings,
)
from booruflow.application.tag_lookup import lookup_gelbooru_suggestions
from booruflow.infrastructure.gelbooru_aliases import (
    AliasRelation,
    GelbooruAliasRepository,
    inspect_alias_catalog,
    migrate_alias_catalog,
    resolve_gelbooru_alias,
)


def _tags(path: Path, name: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE tags(id INTEGER PRIMARY KEY,name TEXT,post_count INTEGER,category INTEGER,ambiguous INTEGER)"
        )
        connection.execute("INSERT INTO tags VALUES(1,?,1,0,0)", (name,))


def test_explicit_tag_database_wins_over_a_newer_neighbor(tmp_path: Path) -> None:
    selected = tmp_path / "g_tags_260810.db"
    newer = tmp_path / "g_tags_260902.db"
    _tags(selected, "selected_tag")
    _tags(newer, "newer_tag")
    settings = {"gelbooru_tag_database": str(selected)}

    assert gelbooru_tag_database(settings) == selected
    assert [row.value for row in lookup_gelbooru_suggestions(selected, None, "tag")] == [
        "selected_tag"
    ]


def test_missing_explicit_catalog_never_falls_back_to_a_neighbor(tmp_path: Path) -> None:
    missing = tmp_path / "g_tags_260810.db"
    _tags(tmp_path / "g_tags_260902.db", "newer_tag")

    assert gelbooru_tag_database({"gelbooru_tag_database": str(missing)}) == missing
    assert not missing.exists()


def test_alias_catalogue_diagnostic_is_read_only_for_a_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "gelbooru_aliases.db"

    status = inspect_alias_catalog(missing)

    assert status.available is False
    assert status.reason == "catalogue file is unavailable"
    assert not missing.exists()


def test_settings_migration_does_not_guess_a_tag_database(tmp_path: Path) -> None:
    old = tmp_path / "g_tags_260810.db"
    _tags(old, "old_tag")
    _tags(tmp_path / "g_tags_260902.db", "newer_tag")

    settings, changed = migrate_database_settings({"gelbooru_database": str(old)}, tmp_path)

    assert changed is True
    assert gelbooru_tag_database(settings) == old
    assert gelbooru_alias_database(settings) == tmp_path / "data" / "databases" / "gelbooru_aliases.db"


def test_explicit_legacy_alias_catalogue_migrates_without_touching_tag_catalogue(tmp_path: Path) -> None:
    old_tags = tmp_path / "g_tags_260810.db"
    _tags(old_tags, "qipao")
    old_aliases = GelbooruAliasRepository(old_tags)
    old_aliases.migrate()
    old_aliases.upsert(AliasRelation("china_dress", "qipao", "active"))
    alias_database = tmp_path / "gelbooru_aliases.db"

    assert migrate_alias_catalog(old_tags, alias_database) is True
    assert resolve_gelbooru_alias("china_dress", alias_database) == "qipao"
    assert migrate_alias_catalog(old_tags, alias_database) is False
    with sqlite3.connect(old_tags) as connection:
        assert connection.execute("SELECT name FROM tags").fetchone()[0] == "qipao"


def test_tag_database_change_does_not_change_alias_database(tmp_path: Path) -> None:
    aliases = tmp_path / "gelbooru_aliases.db"
    settings = {
        "gelbooru_tag_database": str(tmp_path / "g_tags_260810.db"),
        "gelbooru_alias_database": str(aliases),
    }
    settings["gelbooru_tag_database"] = str(tmp_path / "g_tags_260902.db")

    assert gelbooru_alias_database(settings) == aliases


def test_validated_update_activates_only_the_new_tag_database(tmp_path: Path) -> None:
    from booruflow.presentation.pyside6.main_window import MainWindow

    old = tmp_path / "g_tags_260810.db"
    new = tmp_path / "g_tags_260902.db"
    aliases = tmp_path / "gelbooru_aliases.db"
    settings = {
        "gelbooru_tag_database": str(old),
        "gelbooru_alias_database": str(aliases),
        "e621_database": "e621.db",
    }
    repository = SimpleNamespace(load=lambda: dict(settings), save=Mock())
    image_analysis = SimpleNamespace(apply_settings=Mock())
    tag_browser = SimpleNamespace(set_databases=Mock())
    window = SimpleNamespace(
        settings_repository=repository,
        options_page=SimpleNamespace(_settings={}),
        image_analysis_controller=image_analysis,
        tagging_page=SimpleNamespace(settings={}),
        review_page=SimpleNamespace(settings={}),
        tag_browser_page=tag_browser,
        wiki_page=SimpleNamespace(tag_database_path=None),
    )

    MainWindow._activate_database_path(window, "gelbooru", new)

    saved = repository.save.call_args.args[0]
    assert saved["gelbooru_tag_database"] == str(new)
    assert saved["gelbooru_alias_database"] == str(aliases)
    assert window.wiki_page.tag_database_path == new
    assert window.review_page.settings["gelbooru_tag_database"] == str(new)
