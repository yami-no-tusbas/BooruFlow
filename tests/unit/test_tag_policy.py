import sqlite3

from booruflow.application.tag_lookup import exact_tag, lookup_tags
from booruflow.application.tag_policy import is_deprecated


def test_deprecated_is_site_aware_and_conservative() -> None:
    assert is_deprecated("gelbooru", 6)
    assert not is_deprecated("gelbooru", 5)
    assert not is_deprecated("e621", 6)
    assert not is_deprecated("unknown", None)


def test_shared_lookup_filters_only_confirmed_site_deprecated_rows(tmp_path) -> None:
    database = tmp_path / "tags.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE tags(id INTEGER, name TEXT, post_count INTEGER, category INTEGER)"
    )
    connection.executemany(
        "INSERT INTO tags VALUES(?,?,?,?)",
        [(1, "meta_tag", 30, 5), (2, "deprecated_tag", 20, 6), (3, "general_tag", 10, 0)],
    )
    connection.commit()
    connection.close()

    assert [row.name for row in lookup_tags("gelbooru", database, "tag")] == [
        "meta_tag", "general_tag"
    ]
    assert exact_tag("gelbooru", database, "deprecated_tag") is None
    assert exact_tag("e621", database, "deprecated_tag").name == "deprecated_tag"
