from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from booruflow.application.tagging import TaggingRequest
from booruflow.domain.booru_sites import category_name, site_definition
from booruflow.infrastructure.e621_tagging import E621TaggingScanner, normalize_e621_post


def _post(post_id: int, *, species: str = "wolf") -> dict:
    return {
        "id": post_id,
        "tags": {
            "general": ["solo"], "artist": ["some_artist"],
            "species": [species], "meta": ["digital_media"], "lore": ["male_(lore)"],
        },
        "preview": {"url": f"https://static1.e621.net/preview/{post_id}.jpg"},
        "file": {"url": f"https://static1.e621.net/data/{post_id}.jpg"},
    }


def test_site_scoped_category_maps_keep_numeric_five_distinct() -> None:
    assert category_name("e621", 2) == "contributor"
    assert category_name("e621", 5) == "species"
    assert category_name("e621", 7) == "meta"
    assert category_name("e621", 8) == "lore"
    assert category_name("gelbooru", 5) == "meta"
    assert category_name("gelbooru", 6) == "deprecated"
    assert category_name("e621", "metadata") == "meta"
    assert category_name("e621", 5) != category_name("gelbooru", 5)


def test_e621_post_normalization_preserves_tags_categories_and_images() -> None:
    post = normalize_e621_post(_post(42))
    assert post["tags"].split() == [
        "solo", "some_artist", "wolf", "digital_media", "male_(lore)"
    ]
    assert post["_tag_categories"]["wolf"] == 5
    assert post["_tag_categories"]["digital_media"] == 7
    assert post["_tag_categories"]["male_(lore)"] == 8
    assert post["preview_url"].endswith("/42.jpg")
    assert post["file_url"].endswith("/42.jpg")


def test_authenticated_post_provider_reuses_e621_client(monkeypatch) -> None:
    from booruflow.infrastructure.image_sources import E621PostProvider

    captured = {}

    class Client:
        def __init__(self, username, api_key):
            captured.update(username=username, api_key=api_key)

        def request_json(self, path, parameters):
            captured.update(path=path, parameters=parameters)
            return {"post": _post(42)}

    monkeypatch.setattr("booruflow.infrastructure.e621_client.E621Client", Client)
    post = E621PostProvider("wolf_user", "secret-key").fetch_post("42")
    assert captured == {
        "username": "wolf_user", "api_key": "secret-key",
        "path": "/posts/42.json", "parameters": {},
    }
    assert post.site == "e621"
    assert post.post_id == "42"


def test_e621_scanner_uses_numeric_page_semantics_and_advances() -> None:
    calls = []

    class Client:
        def fetch_posts(self, **kwargs):
            calls.append(kwargs)
            return [_post(10)] * 100 if kwargs["page"] == 3 else []

    request = TaggingRequest("wolf", 2, 3, 0, 20, 5, 8, "e621")
    posts, examined, next_page, reached_end = E621TaggingScanner(Client()).scan(request)
    assert [call["page"] for call in calls] == [3, 4]
    assert all(call["tags"] == "wolf" and call["limit"] == 100 for call in calls)
    assert examined == 100
    assert len(posts) == 100
    assert next_page == 5
    assert reached_end is True


def test_site_definitions_route_browser_and_saved_database_keys() -> None:
    e621 = site_definition("e621")
    gelbooru = site_definition("gelbooru")
    assert e621.database_setting_key == "e621_database"
    assert gelbooru.database_setting_key == "gelbooru_tag_database"
    assert e621.post_url(42) == "https://e621.net/posts/42"
    assert e621.search_url("gray wolf") == "https://e621.net/posts?tags=gray+wolf"
    assert e621.account_url == "https://e621.net/users/home"
    assert gelbooru.post_url(42).endswith("page=post&s=view&id=42")


def test_remote_ids_are_distinct_and_e621_review_with_changes_is_publishable(tmp_path: Path) -> None:
    from booruflow.domain.image_analysis import (
        AnalysisItem,
        InputKind,
        PublishState,
        SourceReference,
    )
    from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository

    repository = ImageAnalysisRepository(tmp_path / "analysis.sqlite")
    gel_id = repository.add_item(AnalysisItem(
        SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="42")
    ))
    e621_id = repository.add_item(AnalysisItem(
        SourceReference(InputKind.E621_POST, site="e621", post_id="42")
    ))
    assert gel_id != e621_id
    assert repository.item_by_remote_source("gelbooru", "42").id == gel_id
    assert repository.item_by_remote_source("e621", "42").id == e621_id
    state = repository.save_review_batch_entry(
        e621_id, original_tags=["wolf"], additions=["solo"], removals=[],
        reviewed_final_tags=["wolf", "solo"],
    )
    assert state is PublishState.PENDING_PUBLISH
    entry = repository.batch_entry(e621_id)
    assert (entry["site"], entry["post_id"]) == ("e621", "42")
    assert repository.reviewed_remote_post_ids("e621") == {42}
    assert [row["item_id"] for row in repository.list_batch_entries(PublishState.PENDING_PUBLISH)] == [e621_id]
    repository.close()


def test_e621_review_without_delta_stays_local_and_needs_no_publish(tmp_path: Path) -> None:
    from booruflow.domain.image_analysis import (
        AnalysisItem,
        InputKind,
        PublishState,
        SourceReference,
    )
    from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository

    repository = ImageAnalysisRepository(tmp_path / "analysis.sqlite")
    item_id = repository.add_item(
        AnalysisItem(SourceReference(InputKind.E621_POST, site="e621", post_id="42"))
    )
    state = repository.save_review_batch_entry(
        item_id, original_tags=["wolf"], additions=[], removals=[],
        reviewed_final_tags=["wolf"],
    )
    assert state is PublishState.REVIEWED
    assert repository.list_batch_entries(PublishState.PENDING_PUBLISH) == []
    repository.close()


def test_e621_review_uses_e621_database_categories_and_wd14(tmp_path: Path) -> None:
    from booruflow.domain.image_analysis import (
        DecisionState,
        ObservationSource,
        SourceTag,
        TagObservation,
    )
    from booruflow.presentation.pyside6.tagging_controller import TaggingController

    database = tmp_path / "e621_tags260810.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE tags(id INTEGER PRIMARY KEY,name TEXT,post_count INTEGER,category INTEGER,ambiguous INTEGER)"
    )
    connection.executemany(
        "INSERT INTO tags(name,post_count,category,ambiguous) VALUES(?,100,?,0)",
        [("wolf", 5), ("blue_hair", 0)],
    )
    connection.commit(); connection.close()
    observation = TagObservation(
        "blue_hair", ObservationSource.WD14, 0.9, DecisionState.UNREVIEWED, category="general"
    )
    captured = []
    repository = SimpleNamespace(
        item_by_remote_source=lambda site, post_id: SimpleNamespace(
            id=9, state=SimpleNamespace(value="ready_for_review"), cached_path=None,
            last_error=None,
        ),
        source_tags=lambda _item_id: (SourceTag("wolf", ObservationSource.E621, "species"),),
        observations=lambda _item_id: [(7, observation)],
        tag_mapping=lambda *_args: None,
        tag_review_summary=lambda _item_id, originals: {
            "removals": [], "final_tags": sorted(originals),
        },
    )
    fake = SimpleNamespace(
        catalog=SimpleNamespace(text=lambda key, **_kwargs: key),
        image_analysis=SimpleNamespace(
            repository=repository, settings={"e621_database": str(database)}
        ),
        current_post_id=42, current_post=normalize_e621_post(_post(42)),
        page=SimpleNamespace(
            active_site="e621", show_local_review=lambda *args: captured.append(args),
            set_reanalyze_available=lambda *_args, **_kwargs: None,
        ),
        _log=lambda *_args, **_kwargs: None,
        _worker_pending_label=lambda: None,
    )
    fake._tag_database = lambda: TaggingController._tag_database(fake)
    fake._local_tag_rows = lambda names: TaggingController._local_tag_rows(fake, names)
    TaggingController.refresh_local_review(fake)
    rows = {row["tag"]: row for row in captured[0][3]}
    assert rows["wolf"]["category"] == "tagging.category.species"
    assert rows["wolf"]["category_id"] == "species"
    assert rows["blue_hair"]["category"] == "tagging.category.general"
    assert rows["blue_hair"]["decision"] == "unreviewed"
