import json
import sqlite3

import pytest

from booruflow.application.curated_profiles import (
    configured_curated_profiles,
    load_curated_profiles,
)
from booruflow.domain.curated_profiles import parse_refinement_token, refinement_token
from booruflow.infrastructure.gelbooru_aliases import ensure_alias_schema


def test_virtual_tokens_preserve_arbitrary_real_tag_names():
    assert parse_refinement_token(refinement_token("concept:é", "type:a/b+%")) == (
        "concept:é", "type:a/b+%")


@pytest.fixture
def catalogue(tmp_path):
    database = tmp_path / "tags.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE tags(id INTEGER PRIMARY KEY, name TEXT, "
                           "post_count INTEGER, category INTEGER)")
        connection.executemany("INSERT INTO tags(name,post_count,category) VALUES(?,?,?)", [
            ("panties", 1000, 0), ("thong", 100, 0), ("white_panties", 100, 0),
            ("highleg_panties", 50, 0), ("rare", 24, 0), ("deprecated", 999, 6),
            ("censored", 1000, 0), ("bar_censor", 120, 0), ("gyaru", 1000, 0),
            ("tan", 200, 0), ("leopard_print_panties", 40, 0)])
    alias = tmp_path / "aliases.db"
    ensure_alias_schema(alias)
    with sqlite3.connect(alias) as connection:
        connection.executemany("INSERT INTO gelbooru_aliases "
            "(source_name,target_name,status,first_seen_at,observed_at,last_checked_at) "
            "VALUES(?,?,'active','now','now','now')", [
                ("old_thong", "thong"), ("old_panties", "panties"),
                ("cycle_a", "cycle_b"), ("cycle_b", "cycle_a")])
    return database, alias


def pack(directory, profiles=None, name="pack.json", version=1, pack_id="test"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps({"format_version": version, "pack": {"id": pack_id},
        "profiles": profiles if profiles is not None else [{"trigger": "old_panties", "top_count": 2,
            "groups": [
                {"id": "shape", "label": {"en": "Shape", "fr": "Forme"},
                 "tags": ["old_thong", "thong", "highleg_panties", "missing", "cycle_a"]},
                {"id": "color", "tags": ["white_panties", "thong", "rare", "deprecated"]}]}]}),
        encoding="utf-8")
    return path


def test_load_validation_alias_union_threshold_top_readonly(tmp_path, catalogue):
    tags, aliases = catalogue
    before = (tags.read_bytes(), aliases.read_bytes())
    directory = tmp_path / "packs"
    pack(directory)
    warnings = []
    profiles = load_curated_profiles([directory], tags, aliases, warn=warnings.append)
    profile = profiles["panties"]
    assert [tag.name for tag in profile.top] == ["thong", "white_panties"]
    assert set(profile.tags) == {"thong", "white_panties", "highleg_panties"}
    assert profile.groups[0].tags[0] is profile.groups[1].tags[0]
    assert profile.groups[0].label("fr_BE") == "Forme"
    assert profile.groups[0].label("de") == "Shape"
    assert profile.groups[1].label("fr") == "color"
    assert profiles.canonical("old_thong") == "thong"
    assert any("missing" in message for message in warnings)
    assert any("cycle" in message for message in warnings)
    assert before == (tags.read_bytes(), aliases.read_bytes())


@pytest.mark.parametrize("bad", [None, 17, {}, {"trigger": "missing", "groups": []},
                                  {"trigger": "panties", "top_count": -1, "groups": []}])
def test_invalid_sibling_and_file_survive(tmp_path, catalogue, bad):
    directory = tmp_path / "packs"
    pack(directory, name="good.json")
    pack(directory, [bad], name="bad-profile.json")
    (directory / "broken.json").write_text("{invalid", encoding="utf-8")
    (directory / "deep.json").write_text("[" * 2000 + "]" * 2000, encoding="utf-8")
    pack(directory, version=2, name="future.json")
    warnings = []
    assert "panties" in load_curated_profiles([directory], *catalogue, warn=warnings.append)
    assert any("unsupported format_version" in message for message in warnings)
    assert len(warnings) >= 3


def test_multiple_packs_union_override_settings_and_provenance(tmp_path, catalogue):
    bundled, user = tmp_path / "bundled", tmp_path / "user"
    pack(bundled, pack_id="core")
    pack(user, [{"trigger": "panties", "top_count": 1, "min_post_count": 75,
                 "groups": [{"id": "shape", "label": {"fr": "Silhouette"},
                             "tags": ["thong", "white_panties"]}]}], pack_id="user")
    profile = load_curated_profiles([bundled, user], *catalogue)["panties"]
    assert profile.top_count == 1 and profile.min_post_count == 75
    assert set(profile.tags) == {"thong", "white_panties"}
    assert profile.tags["thong"].packs == ("core", "user")
    assert len(profile.groups) == 2
    assert profile.groups[0].label("fr") == "Silhouette"
    assert profile.groups[0].label("en") == "Shape"


def test_empty_nested_groups_bad_tags_and_missing_database(tmp_path, catalogue):
    directory = tmp_path / "packs"
    pack(directory, [{"trigger": "panties", "groups": [
        {"id": "nested", "tags": ["thong"], "groups": []},
        {"id": "rare", "tags": ["rare", None, "group:virtual"]},
        {"id": "valid", "tags": ["thong"]}]}])
    warnings = []
    profiles = load_curated_profiles([directory], *catalogue, warn=warnings.append)
    assert [group.id for group in profiles["panties"].groups] == ["valid"]
    assert load_curated_profiles([directory], tmp_path / "absent.db") == {}
    assert not (tmp_path / "absent.db").exists()
    assert len(warnings) >= 3
    assert load_curated_profiles([directory], *catalogue, min_post_count=10000) == {}


def test_configured_portable_paths_defaults(tmp_path, catalogue):
    tags, aliases = catalogue
    pack(tmp_path / "resources/curated_tag_profiles")
    pack(tmp_path / "config/curated_tag_profiles", [{"trigger": "panties", "top_count": 1,
        "groups": [{"id": "extra", "tags": ["highleg_panties"]}]}], pack_id="user")
    profile = configured_curated_profiles(tmp_path, tmp_path, {
        "gelbooru_tag_database": str(tags), "gelbooru_alias_database": str(aliases)})["panties"]
    assert profile.top_count == 1
    assert profile.packs == ("test", "user")
