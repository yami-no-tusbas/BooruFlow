import sqlite3
from pathlib import Path

from booruflow.application.wiki_aliases import infer_copyright_alias, resolve_copyright_alias


def database(tmp_path: Path) -> Path:
    path = tmp_path / "tags.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE tags(name TEXT PRIMARY KEY, category INTEGER)")
        connection.executemany(
            "INSERT INTO tags VALUES (?, ?)",
            [
                ("shingeki_no_kyojin", 3),
                ("marvel", 3),
                ("dc_comics", 3),
                ("eren_yeager", 4),
            ],
        )
    return path


def test_unique_common_copyright_resolves_alias_despite_crossovers(tmp_path: Path):
    posts = [
        {"tags": "shingeki_no_kyojin eren_yeager marvel"},
        {"tags": "shingeki_no_kyojin eren_yeager dc_comics"},
        {"tags": "shingeki_no_kyojin eren_yeager"},
    ]
    result = infer_copyright_alias("attack_on_titan", posts, database(tmp_path))
    assert result.status == "alias"
    assert result.canonical_tag == "shingeki_no_kyojin"
    assert result.sampled_posts == 3


def test_multiple_common_copyrights_remain_ambiguous(tmp_path: Path):
    posts = [{"tags": "shingeki_no_kyojin marvel"}] * 3
    result = infer_copyright_alias("attack_on_titan", posts, database(tmp_path))
    assert result.status == "ambiguous"
    assert result.common_copyrights == ("marvel", "shingeki_no_kyojin")


def test_requested_tag_on_posts_is_not_treated_as_alias(tmp_path: Path):
    posts = [{"tags": "attack_on_titan shingeki_no_kyojin"}] * 3
    result = infer_copyright_alias("attack_on_titan", posts, database(tmp_path))
    assert result.status == "requested_tag_present"


def test_resolver_passes_credentials_without_logging_them(tmp_path: Path):
    calls = []

    def fetcher(query, page, limit, user_id, api_key):
        calls.append((query, page, limit, user_id, api_key))
        return ([{"tags": "shingeki_no_kyojin"}] * 3, 30)

    result = resolve_copyright_alias(
        "attack_on_titan", database(tmp_path), "123", "secret", fetcher=fetcher
    )
    assert result.canonical_tag == "shingeki_no_kyojin"
    assert calls == [("attack_on_titan", 0, 20, "123", "secret")]
