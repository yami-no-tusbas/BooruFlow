import sqlite3
from pathlib import Path

from booruflow.application.tag_canonicalization import canonicalize_new_gelbooru_tag
from booruflow.application.tag_lookup import TagLookupSuggestion, lookup_gelbooru_suggestions
from booruflow.infrastructure.gelbooru_aliases import (
    AliasRelation,
    GelbooruAliasRepository,
    ensure_alias_schema,
)


def _catalog(tag_database: Path, alias_database: Path) -> GelbooruAliasRepository:
    with sqlite3.connect(tag_database) as connection:
        connection.execute(
            "CREATE TABLE tags(id INTEGER PRIMARY KEY,name TEXT,post_count INTEGER,category INTEGER,ambiguous INTEGER)"
        )
        connection.execute("INSERT INTO tags VALUES(1,'qipao',100,0,0)")
    ensure_alias_schema(alias_database)
    return GelbooruAliasRepository(alias_database)


def test_new_tag_canonicalization_uses_only_safe_active_aliases(tmp_path: Path) -> None:
    database = tmp_path / "tags.db"
    alias_database = tmp_path / "aliases.db"
    aliases = _catalog(database, alias_database)
    aliases.upsert(AliasRelation("china_dress", "qipao", "active"))
    aliases.upsert(AliasRelation("rose_weasley", "rose_granger-weasley", "pending"))
    aliases.upsert(AliasRelation("missing_alias", "missing_target", "missing"))
    aliases.upsert(AliasRelation("a", "b", "active"))
    aliases.upsert(AliasRelation("b", "c", "active"))

    assert canonicalize_new_gelbooru_tag(" China Dress ", alias_database).canonical_name == "qipao"
    assert canonicalize_new_gelbooru_tag("foo", alias_database).canonical_name == "foo"
    assert canonicalize_new_gelbooru_tag("rose_weasley", alias_database).canonical_name == "rose_weasley"
    assert canonicalize_new_gelbooru_tag("missing_alias", alias_database).canonical_name == "missing_alias"
    assert canonicalize_new_gelbooru_tag("a", alias_database).canonical_name == "c"
    aliases.upsert(AliasRelation("c", "a", "active"))
    assert canonicalize_new_gelbooru_tag("a", alias_database).canonical_name == "a"
    assert canonicalize_new_gelbooru_tag("foo", tmp_path / "missing.db").canonical_name == "foo"


def test_alias_autocomplete_returns_canonical_value_and_ignores_pending(tmp_path: Path) -> None:
    database = tmp_path / "tags.db"
    alias_database = tmp_path / "aliases.db"
    aliases = _catalog(database, alias_database)
    aliases.upsert(AliasRelation("china_dress", "qipao", "active"))
    aliases.upsert(AliasRelation("rose_weasley", "qipao", "pending"))

    assert lookup_gelbooru_suggestions(database, alias_database, "china") == [
        TagLookupSuggestion("qipao", "china_dress")
    ]
    assert lookup_gelbooru_suggestions(database, alias_database, "rose") == []
