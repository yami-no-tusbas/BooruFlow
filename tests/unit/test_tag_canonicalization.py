import sqlite3
from pathlib import Path

from booruflow.application.tag_canonicalization import canonicalize_new_gelbooru_tag
from booruflow.application.tag_lookup import (
    TagLookupSuggestion,
    exact_tag,
    lookup_gelbooru_suggestions,
)
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


def test_autocomplete_ranks_exact_then_prefixes_by_canonical_popularity(tmp_path: Path) -> None:
    database = tmp_path / "tags.db"
    aliases_path = tmp_path / "aliases.db"
    aliases = _catalog(database, aliases_path)
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO tags VALUES(?,?,?,0,0)",
            [
                (2, "armor", 5),
                (3, "armor_z", 999),
                (4, "armor_a", 5),
                (5, "armored_core", 1000),
                (6, "artery_gear", 1000),
                (7, "sword", 5),
            ],
        )
    aliases.upsert(AliasRelation("armor_alias", "artery_gear", "active"))
    aliases.upsert(AliasRelation("armor", "qipao", "active"))
    aliases.upsert(AliasRelation("armor_girls", "artery_gear", "active"))

    rows = lookup_gelbooru_suggestions(database, aliases_path, "armor")
    assert rows[0] == TagLookupSuggestion("qipao", "armor")
    assert [row.value for row in rows[1:]] == [
        "artery_gear", "armored_core", "armor_z", "armor_a"
    ]
    assert all(row.value != "armor" for row in rows)
    assert lookup_gelbooru_suggestions(database, aliases_path, "sword")[0].value == "sword"


def test_evangelion_alias_is_unique_and_uses_canonical_count_category_and_ranking(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tags.db"
    aliases_path = tmp_path / "aliases.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE tags(id INTEGER PRIMARY KEY,name TEXT,post_count INTEGER,"
            "category INTEGER,ambiguous INTEGER)"
        )
        connection.executemany(
            "INSERT INTO tags VALUES(?,?,?,?,0)",
            [
                (1, "evangelion", 121, 0),
                (2, "neon_genesis_evangelion", 54_000, 3),
                (3, "evangelion:_3.0_you_can_(not)_redo", 3_000, 3),
                (4, "evangelion:_3.0+1.0_thrice_upon_a_time", 3_000, 3),
                (5, "evangelion:_2.0_you_can_(not)_advance", 2_000, 3),
                (6, "evangelion_(mecha)", 355, 0),
            ],
        )
    ensure_alias_schema(aliases_path)
    GelbooruAliasRepository(aliases_path).upsert(
        AliasRelation("evangelion", "neon_genesis_evangelion", "active")
    )

    exact_alias = lookup_gelbooru_suggestions(database, aliases_path, "evangelion")
    assert exact_alias[0] == TagLookupSuggestion(
        "neon_genesis_evangelion", "evangelion", 3, 54_000
    )
    assert exact_alias[0].category == 3
    assert exact_alias[0].post_count == 54_000
    assert [row.value for row in exact_alias].count("neon_genesis_evangelion") == 1
    assert all(not (row.value == "evangelion" and row.alias_source is None) for row in exact_alias)
    assert [row.value for row in exact_alias[1:]] == [
        "evangelion:_3.0+1.0_thrice_upon_a_time",
        "evangelion:_3.0_you_can_(not)_redo",
        "evangelion:_2.0_you_can_(not)_advance",
        "evangelion_(mecha)",
    ]

    partial = lookup_gelbooru_suggestions(database, aliases_path, "evangeli")
    assert partial[0].alias_source == "evangelion"
    assert partial[0].post_count == 54_000
    canonical = lookup_gelbooru_suggestions(
        database, aliases_path, "neon_genesis_evangelion"
    )
    assert canonical[0].value == "neon_genesis_evangelion"
    assert canonical[0].alias_source is None


def test_autocomplete_excludes_rare_and_deprecated_tags_but_exact_lookup_remains_manual(tmp_path: Path) -> None:
    database = tmp_path / "tags.db"
    aliases_path = tmp_path / "aliases.db"
    _catalog(database, aliases_path)
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO tags VALUES(?,?,?,?,?)",
            [
                (2, "rare_zero", 0, 0, 0),
                (3, "rare_one", 1, 0, 0),
                (4, "rare_four", 4, 0, 0),
                (5, "common_five", 5, 0, 0),
                (6, "common_hundred", 100, 0, 0),
                (7, "deprecated_common", 10_000, 6, 0),
            ],
        )

    values = {
        row.value
        for row in lookup_gelbooru_suggestions(database, aliases_path, "rare_")
    }
    assert values == set()
    values = {
        row.value
        for row in lookup_gelbooru_suggestions(database, aliases_path, "common_")
    }
    assert values == {"common_five", "common_hundred"}
    assert lookup_gelbooru_suggestions(database, aliases_path, "deprecated_") == []
    assert exact_tag("gelbooru", database, "rare_one").name == "rare_one"
