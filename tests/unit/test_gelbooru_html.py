import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files

import pytest

from booruflow.application.entity_profiles import EntityProfileAnalyzer
from booruflow.domain.auto_organize import PostMetadata
from booruflow.infrastructure.entity_profile_repository import EntityProfileRepository
from booruflow.infrastructure.gelbooru_client import GelbooruDapiClient, fetch_page, normalize_posts
from booruflow.infrastructure.gelbooru_html_migration import migrate_image_source_cache
from booruflow.infrastructure.gelbooru_normalization import normalize_post, post_tags
from booruflow.infrastructure.gelbooru_tagging import payload_posts
from booruflow.infrastructure.image_sources import GelbooruPostProvider
from booruflow.infrastructure.post_metadata_cache import PostMetadataCache
from tests.unit.test_gelbooru_client import FakeResponse

CASES = [("foo&#039;s", "foo's"), ("foo&#39;s", "foo's"), ("foo&#x27;s", "foo's"),
         ("foo&amp;bar", "foo&bar"), ("foo&quot;s", 'foo"s'), ("caf&eacute;", "café"),
         ("&gt;_&lt;", ">_<"), ("a&hearts;b", "a♥b")]


@pytest.mark.parametrize("escaped,decoded", CASES)
@pytest.mark.parametrize("shape", ["list", "single", "group"])
def test_common_ingestion_decodes_fields_lists_and_category_keys(escaped, decoded, shape):
    raw = {"id": 1, "tags": escaped, "tag_string_copyright": escaped,
           "_tag_categories": {escaped: "copyright"}, "source": "https://test/?a&amp;b"}
    payload = [raw] if shape == "list" else {"post": raw if shape == "single" else [raw]}
    result = normalize_posts(payload)[0]
    assert result["tags"] == result["tag_string_copyright"] == decoded
    assert result["_tag_categories"] == {decoded: "copyright"}
    assert result["source"] == raw["source"]
    assert raw["tags"] == escaped
    assert post_tags(result) == [decoded]
    assert post_tags({"tags": [escaped, decoded]}) == [decoded]


def test_no_double_unescape_across_transport_client_tagging_and_dict_copies(monkeypatch):
    raw = {"id": 1, "tags": "literal&amp;#039;s literal&amp;amp; &amp;not;"}
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse({"post": [raw]}))
    result = GelbooruDapiClient().search_posts("foo's")[0]
    expected = ["literal&#039;s", "literal&amp;", "&not;"]
    for copied in (result, dict(result), json.loads(json.dumps(result)), normalize_post(result), payload_posts([result])[0]):
        assert post_tags(copied) == expected
        assert post_tags(normalize_post(copied)) == expected
    custom = GelbooruDapiClient(page_fetcher=lambda *_args: ([raw], 1)).fetch_post(1)
    assert post_tags(custom) == expected


def test_query_with_real_apostrophe_is_unchanged_at_transport_and_client(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    queries = []
    def open_request(request, **_kwargs):
        queries.append(parse_qs(urlsplit(request.full_url).query)["tags"][0])
        return FakeResponse({"post": [{"id": 1, "tags": "foo&#039;s"}]})
    monkeypatch.setattr("urllib.request.urlopen", open_request)
    query = "mahou_shoujo_lyrical_nanoha_a's"
    assert fetch_page(query, 0, 100, "", "")[0][0]["tags"] == "foo's"
    GelbooruDapiClient().search_posts(query)
    assert queries == [query, query]


def test_decoded_provider_lookup_and_entity_refresh_persist_once(tmp_path):
    database = tmp_path / "tags.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE tags(id INTEGER,name TEXT,post_count INTEGER,category INTEGER)")
        connection.execute("INSERT INTO tags VALUES(1,?,10,3)", ("foo's",))
    seen = []
    def lookup(names):
        seen.append(names)
        return {"foo's": "copyright"}
    provider = GelbooruPostProvider(category_lookup=lookup)
    raw = {"id": 1, "tags": "entity foo&#039;s foo's foo&#x27;s", "file_url": "https://test/1.jpg"}
    normalized = provider.normalize_search_post(raw)
    assert {tag.name: tag.category for tag in normalized.tags} == {"entity": None, "foo's": "copyright"}
    assert all("foo's" in names and "foo&#039;s" not in names for names in seen)
    cache = PostMetadataCache(tmp_path / "metadata.sqlite")
    with EntityProfileRepository(tmp_path / "profiles.sqlite") as repository:
        profile = repository.create("entity", "entity foo's")
        client = GelbooruDapiClient(page_fetcher=lambda *_args: ([raw], 1))
        analyzer = EntityProfileAnalyzer(repository, client, cache, database, throttle_seconds=0)
        first = analyzer.refresh(profile, 1)
        second = analyzer.refresh(profile, 1)
        assert (first.total_posts, second.total_posts, second.new_posts) == (1, 1, 0)
        assert repository.tags(profile)[0]["tag"] == "foo's"
        assert repository.tags(profile)[0]["category"] == "copyright"
        assert repository.tags(profile)[0]["occurrences"] == 1
        assert len(repository.matching_posts(profile, "foo's")) == 1
        assert cache.get("gelbooru", "1").categories["foo's"] == "copyright"
        assert cache.get("gelbooru", "1").tags == ("entity", "foo's")
    cache.close()


@pytest.mark.parametrize("escaped,decoded", CASES)
def test_standard_entities_survive_entity_and_cache_reopen_without_duplicate_counts(tmp_path, escaped, decoded):
    path = tmp_path / "profiles.sqlite"
    cache_path = tmp_path / "metadata.sqlite"
    cache = PostMetadataCache(cache_path)
    with EntityProfileRepository(path) as repository:
        profile = repository.create("entity", "entity")
        client = GelbooruDapiClient(page_fetcher=lambda *_args: ([{
            "id": 1, "tags": f"entity {escaped} {decoded}", "_tag_categories": {escaped: "general"},
        }], 1))
        analyzer = EntityProfileAnalyzer(repository, client, cache, throttle_seconds=0)
        assert analyzer.refresh(profile, 1).total_posts == 1
    cache.close()
    with EntityProfileRepository(path) as repository:
        assert repository.profile(profile)["sample_count"] == 1
        rows = repository.tags(profile)
        assert len(rows) == 1
        assert (rows[0]["tag"], rows[0]["occurrences"], rows[0]["category"]) == (decoded, 1, "general")
    cache = PostMetadataCache(cache_path)
    assert set(cache.get("gelbooru", "1").tags) == {"entity", decoded}
    assert cache.get("gelbooru", "1").categories[decoded] == "general"
    cache.close()


def legacy_profiles(path):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(files("booruflow.infrastructure.schema").joinpath("entity_profiles_v1.sql").read_text("utf-8"))
    return connection


def seed_profile(connection, pid, entity, query, posts, statuses):
    connection.execute("INSERT INTO profiles VALUES(?,?,?, ?,0,'completed','old','new')",
                       (pid, entity, query, len(posts)))
    counts = {}
    for post_id, tags in posts.items():
        connection.execute("INSERT INTO profile_posts VALUES(?,?,?)", (pid, post_id, f"preview:{post_id}"))
        for tag in set(tags):
            connection.execute("INSERT INTO profile_post_tags VALUES(?,?,?)", (pid, post_id, tag))
            if tag != entity:
                counts[tag] = counts.get(tag, 0) + 1
    for tag, count in counts.items():
        connection.execute("INSERT INTO recurring_tags VALUES(?,?,'unknown','attribute',?,?)",
                           (pid, tag, count, statuses.get(tag, "review")))


def test_legacy_tag_merge_unions_witnesses_and_archives_conflicting_decisions(tmp_path):
    path = tmp_path / "profiles.sqlite"
    connection = legacy_profiles(path)
    seed_profile(connection, 1, "source", "source", {
        "1": ["source", "foo&#039;s", "foo's"], "2": ["source", "foo&#39;s"],
        "3": ["source", "foo's"]}, {"foo&#039;s": "keep", "foo's": "ignore"})
    connection.execute("INSERT INTO global_exclusions VALUES('foo&#039;s')")
    connection.execute("INSERT INTO tagging_profile_decisions VALUES(7, 'foo&#039;s', 'accepted', 8)")
    connection.execute("INSERT INTO tagging_profile_decisions VALUES(7, ?, 'rejected', NULL)", ("foo's",))
    connection.commit(); connection.close()
    with EntityProfileRepository(path) as repository:
        assert repository.profile(1)["sample_count"] == 3
        assert repository.seen_ids(1) == {"1", "2", "3"}
        row = repository.tags(1)[0]
        assert (row["tag"], row["occurrences"], row["status"], row["excluded"]) == ("foo's", 3, "ignore", 1)
        assert len(repository.tags(1)) == 1
        assert len(repository.matching_posts(1, "foo's")) == 3
        assert repository.review_decisions(7)["foo's"]["decision"] == "rejected"
        archived = [json.loads(row[0]) for row in repository.connection.execute(
            "SELECT row_json FROM gelbooru_html_archive WHERE table_name='recurring_tags'")]
        assert {row["status"] for row in archived} == {"review", "keep", "ignore"}
        assert len(list(repository.connection.execute("SELECT * FROM gelbooru_html_conflicts"))) == 2
        assert repository.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    with EntityProfileRepository(path) as repository:
        assert repository.tags(1)[0]["occurrences"] == 3


def test_legacy_profile_identity_collision_merges_datasets_without_counting_overlap(tmp_path):
    path = tmp_path / "profiles.sqlite"; connection = legacy_profiles(path)
    seed_profile(connection, 1, "foo&#039;s", "foo&#039;s solo", {
        "1": ["foo&#039;s", "hairclip"], "2": ["foo&#039;s", "tattoo"]}, {"hairclip": "keep"})
    seed_profile(connection, 2, "foo's", "foo's solo", {
        "2": ["foo's", "tattoo"], "3": ["foo's", "hairclip"]}, {"hairclip": "review"})
    connection.commit(); connection.close()
    with EntityProfileRepository(path) as repository:
        assert len(repository.profiles()) == 1
        assert repository.profile(2)["sample_count"] == 3
        assert repository.profile(2)["query"] == "foo's solo"
        tags = {row["tag"]: row for row in repository.tags(2)}
        assert set(tags) == {"hairclip", "tattoo"}
        assert tags["hairclip"]["occurrences"] == 2 and tags["hairclip"]["status"] == "keep"
        assert tags["tattoo"]["occurrences"] == 1


def test_one_pass_migrations_preserve_literal_entity_text_and_other_sites(tmp_path):
    path = tmp_path / "profiles.sqlite"; connection = legacy_profiles(path)
    seed_profile(connection, 1, "source", "source", {"1": ["literal&amp;#039;s"]}, {})
    connection.commit(); connection.close()
    for _ in range(2):
        with EntityProfileRepository(path) as repository:
            assert repository.tags(1)[0]["tag"] == "literal&#039;s"
    cache_path = tmp_path / "cache.sqlite"
    cache = PostMetadataCache(cache_path)
    cache.put(PostMetadata("gelbooru", "1", ("literal&amp;#039;s", "foo&#039;s", "foo's"), {"foo's": "copyright"}))
    cache.put(PostMetadata("e621", "1", ("foo&#039;s",)))
    # Simulate a cache created by the old writer, before the migration marker existed.
    cache.connection.execute("DELETE FROM gelbooru_html_migrations"); cache.connection.commit(); cache.close()
    for _ in range(2):
        cache = PostMetadataCache(cache_path)
        assert cache.get("gelbooru", "1").tags == ("foo's", "literal&#039;s")
        assert cache.get("gelbooru", "1").categories["foo's"] == "copyright"
        assert cache.get("e621", "1").tags == ("foo&#039;s",)
        cache.close()


def test_concurrent_legacy_open_does_not_decode_twice(tmp_path):
    path = tmp_path / "profiles.sqlite"; connection = legacy_profiles(path)
    seed_profile(connection, 1, "source", "source", {"1": ["literal&amp;#039;s"]}, {})
    connection.commit(); connection.close()
    def open_profile(_index):
        with EntityProfileRepository(path) as repository:
            return repository.tags(1)[0]["tag"], repository.profile(1)["sample_count"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(open_profile, range(2))) == [("literal&#039;s", 1), ("literal&#039;s", 1)]


def test_failed_migration_rolls_back_names_counts_archive_and_version(tmp_path):
    path = tmp_path / "profiles.sqlite"; connection = legacy_profiles(path)
    seed_profile(connection, 1, "source", "source", {"1": ["foo&#039;s"]}, {"foo&#039;s": "keep"})
    connection.execute("CREATE TRIGGER block_repair BEFORE INSERT ON recurring_tags BEGIN SELECT RAISE(ABORT,'test'); END")
    connection.commit(); connection.close()
    with pytest.raises(sqlite3.IntegrityError):
        EntityProfileRepository(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT tag,status,occurrences FROM recurring_tags").fetchone() == ("foo&#039;s", "keep", 1)
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name='gelbooru_html_archive'").fetchall()


def test_legacy_migration_recovers_local_category_after_decoding(tmp_path):
    path = tmp_path / "profiles.sqlite"; connection = legacy_profiles(path)
    seed_profile(connection, 1, "source", "source", {"1": ["foo&#039;s"]}, {})
    connection.commit(); connection.close()
    catalogue = tmp_path / "tags.sqlite"
    with sqlite3.connect(catalogue) as connection:
        connection.execute("CREATE TABLE tags(id INTEGER,name TEXT,post_count INTEGER,category INTEGER)")
        connection.execute("INSERT INTO tags VALUES(1,?,3,3)", ("foo's",))
    with EntityProfileRepository(path, catalogue) as repository:
        row = repository.tags(1)[0]
        assert (row["tag"], row["category"], row["kind"], row["occurrences"]) == ("foo's", "copyright", "relation", 1)


def test_image_source_repair_merges_keys_and_decisions_preserving_observations(tmp_path):
    from booruflow.domain.image_analysis import (
        AnalysisItem,
        InputKind,
        ObservationSource,
        SourceReference,
        SourceTag,
    )
    from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
    repository = ImageAnalysisRepository(tmp_path / "analysis.sqlite")
    item = repository.add_item(AnalysisItem(SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="1")), (
        SourceTag("foo&#039;s", ObservationSource.GELBOORU), SourceTag("foo's", ObservationSource.GELBOORU, "copyright"),
        SourceTag("foo&#039;s", ObservationSource.E621), SourceTag("literal&amp;#039;s", ObservationSource.GELBOORU),
    ))
    repository.set_existing_tag_decision(item, "foo&#039;s", "keep")
    repository.set_existing_tag_decision(item, "foo's", "remove")
    with repository.connection:
        repository.connection.execute("""INSERT INTO tag_observations(item_id,tag_name,source,confidence,created_at)
            VALUES(?,?,'wd14',.8,'old')""", (item, "foo&#039;s"))
    repository.cache_post_metadata("gelbooru", "1", "https://test/original.jpg", (
        SourceTag("foo&#039;s", ObservationSource.GELBOORU), SourceTag("foo's", ObservationSource.GELBOORU, "copyright")), ())
    repository.cache_post_metadata("e621", "1", "https://test/e621.jpg", (SourceTag("foo&#039;s", ObservationSource.E621),), ())
    before = [tuple(row) for row in repository.connection.execute("SELECT * FROM tag_observations")]
    for _ in range(2):
        migrate_image_source_cache(repository.connection)
        rows = repository.source_tags(item)
        assert {tag.name for tag in rows if tag.source == "gelbooru"} == {"foo's", "literal&#039;s"}
        assert {tag.name for tag in rows if tag.source == "e621"} == {"foo&#039;s"}
    assert repository.existing_tag_decision(item, "foo's") == "remove"
    assert [tuple(row) for row in repository.connection.execute("SELECT * FROM tag_observations")] == before
    cached = repository.cached_post_metadata("gelbooru", "1")
    assert json.loads(cached["tags_json"]) == [{"name": "foo's", "category": "copyright"}]
    assert json.loads(repository.cached_post_metadata("e621", "1")["tags_json"])[0]["name"] == "foo&#039;s"
    assert len(repository.connection.execute("SELECT * FROM gelbooru_html_conflicts").fetchall()) == 1
    assert repository.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert repository.connection.execute("PRAGMA user_version").fetchone()[0] == 27
    repository.close()


def test_repair_tool_simulates_then_backs_up_without_overwriting(tmp_path):
    from tools.repair_gelbooru_html import repair
    path = tmp_path / "var/state/entity_profiles.sqlite"
    path.parent.mkdir(parents=True)
    connection = legacy_profiles(path)
    seed_profile(connection, 1, "source", "source's", {"1": ["foo&#039;s"]}, {"foo&#039;s": "keep"})
    connection.commit(); connection.close()
    dry = repair(tmp_path)
    assert dry["simulation"]["entity_profiles"]["after"]["samples"] == {1: 1}
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT tag FROM recurring_tags").fetchone()[0] == "foo&#039;s"
    applied = repair(tmp_path, apply=True)
    from pathlib import Path
    backup = Path(applied["backup_directory"]) / "entity_profiles.sqlite"
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT tag FROM recurring_tags").fetchone()[0] == "foo&#039;s"
    with EntityProfileRepository(path) as repository:
        assert repository.tags(1)[0]["tag"] == "foo's"
        assert repository.tags(1)[0]["status"] == "keep"
        assert repository.profile(1)["query"] == "source's"
