from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from booruflow.application.tag_autocomplete import (
    AutocompleteRequest,
    TagAutocompleteService,
    TagSuggestion,
    parse_autocomplete_text,
)
from booruflow.infrastructure.gelbooru_aliases import (
    AliasRelation,
    GelbooruAliasRepository,
    ensure_alias_schema,
)
from booruflow.presentation.pyside6.tag_autocomplete import TagAutocompleteCoordinator


def _database(path: Path, rows: list[tuple[int, str, int, int, int]]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE tags(id INTEGER PRIMARY KEY,name TEXT,post_count INTEGER,"
            "category INTEGER,ambiguous INTEGER)"
        )
        connection.execute("CREATE INDEX idx_tags_name ON tags(name)")
        connection.executemany("INSERT INTO tags VALUES(?,?,?,?,?)", rows)


def test_prefixes_are_parsed_without_losing_negative_intent() -> None:
    parsed = parse_autocomplete_text("-char:sab")
    assert parsed.text == "sab"
    assert parsed.negative is True
    assert parsed.category_role == "character"
    assert parsed.matched_prefix == "char:"
    assert parse_autocomplete_text("rating:general").category_role is None


def test_site_explicit_service_applies_semantic_categories_and_policy(tmp_path: Path) -> None:
    gelbooru = tmp_path / "gelbooru.sqlite"
    e621 = tmp_path / "e621.sqlite"
    aliases_path = tmp_path / "aliases.sqlite"
    _database(
        gelbooru,
        [
            (1, "saber", 100, 4, 0),
            (2, "sable", 100, 0, 0),
            (3, "rare_character", 1, 4, 0),
            (4, "deprecated_tag", 10_000, 6, 0),
        ],
    )
    _database(
        e621,
        [(1, "sabertooth", 50, 5, 0), (2, "saber", 50, 4, 0)],
    )
    ensure_alias_schema(aliases_path)
    GelbooruAliasRepository(aliases_path).upsert(
        AliasRelation("saber_alter", "saber", "active")
    )
    service = TagAutocompleteService(
        {"gelbooru": gelbooru, "e621": e621},
        {"gelbooru": aliases_path, "e621": None},
    )

    character = service.suggestions(
        AutocompleteRequest("search", 1, "gelbooru", "char:sab")
    )
    assert [item.canonical_name for item in character] == ["saber"]
    assert character[0].category == "character"
    assert character[0].matched_prefix == "char:"
    assert service.suggestions(
        AutocompleteRequest("search", 2, "gelbooru", "rare_character")
    )[0].post_count == 1
    assert service.suggestions(
        AutocompleteRequest("search", 3, "gelbooru", "deprecated_tag")
    ) == []
    alias = service.suggestions(
        AutocompleteRequest("search", 4, "gelbooru", "saber_alter")
    )
    assert alias[0].alias_source == "saber_alter"
    assert alias[0].canonical_name == "saber"
    assert alias[0].category == "character"
    species = service.suggestions(
        AutocompleteRequest("builder", 1, "e621", "spec:sab")
    )
    assert [item.canonical_name for item in species] == ["sabertooth"]
    assert service.suggestions(
        AutocompleteRequest("missing", 1, "gelbooru", "sa")
    ) == []


def test_shared_service_exposes_only_decorated_alias_with_canonical_metadata(
    tmp_path: Path,
) -> None:
    gelbooru = tmp_path / "gelbooru.sqlite"
    aliases_path = tmp_path / "aliases.sqlite"
    _database(
        gelbooru,
        [
            (1, "evangelion", 121, 0, 0),
            (2, "neon_genesis_evangelion", 54_000, 3, 0),
            (3, "evangelion_(mecha)", 355, 0, 0),
        ],
    )
    ensure_alias_schema(aliases_path)
    GelbooruAliasRepository(aliases_path).upsert(
        AliasRelation("evangelion", "neon_genesis_evangelion", "active")
    )
    service = TagAutocompleteService(
        {"gelbooru": gelbooru}, {"gelbooru": aliases_path}
    )

    suggestions = service.suggestions(
        AutocompleteRequest("shared-field", 1, "gelbooru", "evangelion")
    )
    assert suggestions[0] == TagSuggestion(
        "gelbooru",
        "neon_genesis_evangelion",
        "neon_genesis_evangelion",
        "copyright",
        54_000,
        "evangelion",
    )
    assert all(item.canonical_name != "evangelion" for item in suggestions)


def test_coordinator_ignores_stale_owner_generation_and_site() -> None:
    app = QApplication.instance() or QApplication([])
    service = TagAutocompleteService({})
    coordinator = TagAutocompleteCoordinator(service)
    spy = QSignalSpy(coordinator.suggestions_ready)
    old = AutocompleteRequest("field-a", 1, "gelbooru", "sab")
    current = AutocompleteRequest("field-a", 2, "e621", "sab")
    other = AutocompleteRequest("field-b", 1, "gelbooru", "sab")
    coordinator._state = {"field-a": (2, "e621"), "field-b": (1, "gelbooru")}
    result = [TagSuggestion("e621", "saber", "saber", "character", 10)]

    coordinator._completed(old, result)
    coordinator._completed(current, result)
    coordinator._completed(other, result)

    assert spy.count() == 2
    assert spy.at(0)[0] == "field-a"
    assert spy.at(1)[0] == "field-b"
    assert app is not None
