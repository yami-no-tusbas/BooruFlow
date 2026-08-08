from tag_taxonomy_db import TaxonomyDatabase


def test_relational_memberships_allow_the_same_tag_in_multiple_branches(tmp_path):
    database = TaxonomyDatabase(tmp_path / "taxonomy.sqlite", "e621")
    database.sync_from_document(
        {"Mammal": ["hybrid"], "Fish": ["hybrid"]}, {}, [], []
    )
    count = database.connection.execute(
        "SELECT COUNT(*) FROM memberships WHERE tag_name='hybrid'"
    ).fetchone()[0]
    assert count == 2
    assert database.integrity() == "ok"
    database.close()


def test_category_can_hold_manual_tags_and_subcategories(tmp_path):
    database = TaxonomyDatabase(tmp_path / "mixed.sqlite", "gelbooru")
    database.sync_from_document(
        {
            "Weapons": {
                "Firearms": {
                    "__tags__": ["firearm"],
                    "Rifle": {"__manual__": True, "Bolt-action": ["Arisaka"]},
                }
            }
        },
        {}, [], [],
    )
    rows = database.connection.execute(
        "SELECT tag_name FROM memberships ORDER BY tag_name"
    ).fetchall()
    assert rows == [("Arisaka",), ("firearm",)]
    assert database.integrity() == "ok"
    database.close()
