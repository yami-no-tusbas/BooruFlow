import sqlite3
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from booruflow.application.entity_profiles import EntityProfileAnalyzer, merge_profile_suggestions
from booruflow.domain.auto_organize import PostMetadata
from booruflow.domain.entity_profiles import EntitySuggestion, activation_tags, automatic_exclusion
from booruflow.infrastructure.entity_profile_repository import EntityProfileRepository
from booruflow.infrastructure.post_metadata_cache import PostMetadataCache


@pytest.fixture
def dataset(tmp_path):
    with EntityProfileRepository(tmp_path / "entities.sqlite") as repository:
        cache = PostMetadataCache(tmp_path / "metadata.sqlite")
        yield repository, cache
        cache.close()


def metadata(post_id, tags, categories=None):
    return PostMetadata("gelbooru", str(post_id), tuple(tags.split()), categories or {},
                        extra={"preview_url": f"https://test.invalid/{post_id}.jpg"})


def analyzer(repository, cache, pages, **kwargs):
    calls = []

    def search(query, *, limit, page):
        calls.append((query, limit, page))
        value = pages[page] if page < len(pages) else []
        if isinstance(value, Exception):
            raise value
        return value

    return EntityProfileAnalyzer(repository, SimpleNamespace(search_posts=search), cache,
                                 throttle_seconds=0, retry_delays=(), **kwargs), calls


@pytest.mark.parametrize("source_category,expected", [
    ("character", {"hero", "franchise"}), ("copyright", {"hero", "franchise"}),
    ("artist", {"hero", "franchise", "illustrator"}),
    ("general", {"hero", "franchise", "illustrator"}),
])
def test_directional_suggestions_filter_without_changing_evidence(dataset, source_category, expected):
    repository, _cache = dataset
    profile = repository.create("source", "source")
    repository.add_posts(profile, [metadata(1, "source illustrator hero franchise anonymous", {
        "illustrator": "artist", "hero": "character", "franchise": "copyright", "anonymous": "artist",
    })])
    for row in repository.tags(profile):
        repository.set_status(profile, row["tag"], "keep")
    before = repository.tags(profile)
    assert {suggestion.tag for suggestion in repository.suggestions(
        {"source"}, {"source": source_category})} == expected
    assert repository.tags(profile) == before
    assert repository.seen_ids(profile) == {"1"}
    assert not ({"illustrator", "anonymous"} & set(repository.exclusions()))


@pytest.mark.parametrize("tag", [
    "1girl", "2girls", "6+girls", "1boy", "3boys", "6+boys", "1other", "2others", "6+others",
    "1futa", "2futa", "2futas", "6+futa", "6+futas", "solo", "solo_focus",
    "multiple_girls", "multiple_boys", "multiple_others", "multiple_futa", "multiple_futas",
])
def test_composition_family_is_automatically_excluded(tag):
    assert automatic_exclusion(tag, "general")


def test_composition_and_meta_do_not_hide_visual_attributes_or_destroy_counts(dataset):
    repository, _cache = dataset
    profile = repository.create("source", "source")
    useful = {"hairclip", "tattoo", "jacket", "scythe", "hair_ornament", "brooch", "scar", "footwear"}
    noise = {"1girl", "2girls", "1boy", "solo", "multiple_girls", "highres"}
    repository.add_posts(profile, [metadata(1, " ".join({"source"} | useful | noise), {"highres": 5})])
    for tag in useful | noise:
        repository.set_status(profile, tag, "keep")
    before = repository.tags(profile)
    assert {suggestion.tag for suggestion in repository.suggestions({"source"})} == useful
    assert repository.tags(profile) == before
    assert len(repository.matching_posts(profile, "1girl")) == 1
    assert all(not automatic_exclusion(tag, "general") for tag in useful)


def test_witness_limit_remains_one_hundred(dataset):
    repository, _cache = dataset
    profile = repository.create("source", "source")
    repository.add_posts(profile, [metadata(i, "source hairclip") for i in range(1, 106)])
    witnesses = repository.matching_posts(profile, "hairclip")
    assert len(witnesses) == 100
    assert witnesses[0]["post_id"] == "105" and witnesses[-1]["post_id"] == "6"


def test_create_and_reopen_keeps_dataset_identity_and_editable_query(tmp_path):
    path = tmp_path / "entities.sqlite"
    with EntityProfileRepository(path) as repository:
        profile = repository.create("bardiche", "bardiche")
        assert repository.create("bardiche", "  bardiche  ") == profile
        other = repository.create("bardiche", "bardiche fate_testarossa")
        assert profile != other
        assert repository.profile(profile)["sample_count"] == 0
        repository.exclude("smile", False)
    with EntityProfileRepository(path) as reopened:
        assert len(reopened.profiles("bardiche")) == 2
        assert "smile" not in reopened.exclusions()
        assert reopened.profile(profile)["created_at"]


def test_concurrent_initialization_and_counting_share_unique_ids(tmp_path):
    path = tmp_path / "entities.sqlite"
    def sample(_index):
        with EntityProfileRepository(path) as repository:
            profile = repository.create("bardiche", "bardiche")
            return repository.add_posts(profile, [metadata(i, "bardiche staff") for i in range(1, 11)])
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(sample, range(2)))
    assert sum(counts) == 10
    with EntityProfileRepository(path) as repository:
        assert repository.profiles()[0]["sample_count"] == 10
        assert repository.tags(repository.profiles()[0]["id"])[0]["occurrences"] == 10


def test_refresh_decisions_and_global_exclusions_survive_reopening(tmp_path):
    path = tmp_path / "entities.sqlite"
    with EntityProfileRepository(path) as repository:
        profile = repository.create("object", "object")
        repository.add_posts(profile, [metadata(1, "object hairclip staff smile")])
        repository.set_status(profile, "hairclip", "keep")
        repository.set_status(profile, "staff", "ignore")
        repository.exclude("smile", False)
    with EntityProfileRepository(path) as repository:
        repository.add_posts(profile, [metadata(1, "object staff"), metadata(2, "object hairclip staff smile")])
        rows = {row["tag"]: row for row in repository.tags(profile)}
        assert rows["hairclip"]["status"] == "keep"
        assert rows["staff"]["status"] == "ignore"
        assert rows["staff"]["occurrences"] == 2
        assert not rows["smile"]["excluded"]


def test_counters_distinct_tags_statuses_exclusions_and_directional_relations(dataset):
    repository, _cache = dataset
    profile = repository.create("bardiche", "bardiche")
    posts = [metadata(1, "bardiche hairclip smile fate_testarossa nanoha hairclip",
                      {"fate_testarossa": "4", "nanoha": "copyright"}),
             metadata(2, "bardiche weapon smile fate_testarossa", {"fate_testarossa": "character"})]
    assert repository.add_posts(profile, posts) == 2
    assert repository.add_posts(profile, posts) == 0
    tags = {row["tag"]: row for row in repository.tags(profile)}
    assert "bardiche" not in tags
    assert tags["hairclip"]["occurrences"] == 1
    assert tags["hairclip"]["frequency"] == .5
    assert tags["fate_testarossa"]["frequency"] == 1
    assert tags["fate_testarossa"]["kind"] == "relation"
    assert tags["nanoha"]["kind"] == "relation"
    assert tags["weapon"]["kind"] == "attribute"
    assert tags["smile"]["excluded"]
    assert not tags["hairclip"]["excluded"]
    repository.set_status(profile, "hairclip", "keep")
    repository.set_status(profile, "weapon", "ignore")
    repository.set_status(profile, "smile", "keep")
    repository.add_posts(profile, [metadata(3, "bardiche weapon hairclip smile")])
    tags = {row["tag"]: row for row in repository.tags(profile)}
    assert tags["hairclip"]["status"] == "keep"
    assert tags["weapon"]["status"] == "ignore"
    assert tags["hairclip"]["occurrences"] == 2
    assert {row.tag for row in repository.suggestions({"bardiche"})} == {"hairclip"}
    assert repository.suggestions({"fate_testarossa"}) == []
    assert [row["post_id"] for row in repository.matching_posts(profile, "hairclip")] == ["3", "1"]
    assert repository.profile(profile)["sample_count"] == 3


def test_attributes_can_keep_multiple_forms_and_copyright_profiles(dataset):
    repository, _cache = dataset
    obj = repository.create("bardiche", "bardiche")
    repository.add_posts(obj, [metadata(1, "bardiche staff scythe sword")])
    for tag in ("staff", "scythe", "sword"):
        repository.set_status(obj, tag, "keep")
    assert len(repository.suggestions({"bardiche"})) == 3
    copyright = repository.create("nanoha", "nanoha")
    repository.add_posts(copyright, [metadata(1, "nanoha fate_testarossa", {"fate_testarossa": "character"})])
    repository.set_status(copyright, "fate_testarossa", "keep")
    assert repository.suggestions({"nanoha"})[0].category == "character"


def test_refresh_restarts_at_page_zero_and_skips_seen_even_after_reordering(dataset):
    repository, cache = dataset
    profile = repository.create("motoko", "motoko solo")
    run, calls = analyzer(repository, cache, [[{"id": i, "tags": "motoko short_hair"} for i in range(1, 101)]])
    first = run.refresh(profile, 100)
    assert (first.new_posts, first.total_posts) == (100, 100)
    run, calls = analyzer(repository, cache, [
        [{"id": 101, "tags": "motoko hairclip"}] + [{"id": i, "tags": "motoko short_hair"} for i in range(1, 100)],
        [{"id": 100, "tags": "motoko short_hair"}, {"id": 102, "tags": "motoko hairclip"}],
    ])
    result = run.refresh(profile, 2)
    assert [row[2] for row in calls] == [0, 1]
    assert (result.new_posts, result.total_posts) == (2, 102)
    assert repository.profile(profile)["last_run_new"] == 2
    tags = {row["tag"]: row for row in repository.tags(profile)}
    assert tags["short_hair"]["occurrences"] == 100
    assert tags["hairclip"]["occurrences"] == 2
    assert set(cache.get("gelbooru", "101").tags) == {"motoko", "hairclip"}


def test_duplicate_ids_inside_page_do_not_consume_requested_quota(dataset):
    repository, cache = dataset
    profile = repository.create("object", "object")
    run, _calls = analyzer(repository, cache, [[
        {"id": 1, "tags": "object staff"}, {"id": 1, "tags": "object staff"},
        {"id": 2, "tags": "object sword"}]])
    assert run.refresh(profile, 2).total_posts == 2


def test_failed_refresh_resumes_from_committed_page(dataset):
    repository, cache = dataset
    profile = repository.create("object", "object")
    page = [{"id": i, "tags": "object staff"} for i in range(1, 101)]
    run, _calls = analyzer(repository, cache, [page, RuntimeError("broken")])
    with pytest.raises(RuntimeError, match="broken"):
        run.refresh(profile, 101)
    assert repository.profile(profile)["last_run_state"] == "failed"
    assert repository.profile(profile)["sample_count"] == 100
    run, _calls = analyzer(repository, cache, [page, [{"id": 101, "tags": "object staff"}]])
    assert run.refresh(profile, 1).total_posts == 101
    assert repository.tags(profile)[0]["occurrences"] == 101


def test_post_and_counters_rollback_together(dataset):
    repository, _cache = dataset
    profile = repository.create("object", "object")
    repository.connection.executescript("""
    CREATE TRIGGER fail_count BEFORE INSERT ON recurring_tags
    WHEN NEW.tag='broken' BEGIN SELECT RAISE(ABORT,'test interruption'); END;
    """)
    with pytest.raises(sqlite3.IntegrityError):
        repository.add_posts(profile, [metadata(1, "object staff"), metadata(2, "object broken")])
    assert repository.seen_ids(profile) == set()
    assert repository.profile(profile)["sample_count"] == 0
    assert repository.tags(profile) == []


def test_cancellation_and_exhaustion_are_honest(dataset):
    repository, cache = dataset
    profile = repository.create("object", "object")
    run, calls = analyzer(repository, cache, [[]])
    result = run.refresh(profile, 100)
    assert result.state == "exhausted" and result.total_posts == 0
    cancelled = Event(); cancelled.set(); calls.clear()
    result = run.refresh(profile, 100, cancelled=cancelled)
    assert result.state == "cancelled" and calls == []


def test_cancellation_after_one_page_keeps_counts(dataset):
    repository, cache = dataset
    profile = repository.create("object", "object")
    cancelled = Event()
    run, _calls = analyzer(repository, cache, [[{"id": i, "tags": "object staff"} for i in range(1, 101)]])
    result = run.refresh(profile, 200, cancelled=cancelled, progress=lambda _event: cancelled.set())
    assert result.state == "cancelled" and result.new_posts == 100


def test_repeated_full_page_stops_safely(dataset):
    repository, cache = dataset
    profile = repository.create("object", "object")
    page = [{"id": i, "tags": "object staff"} for i in range(1, 101)]
    run, _calls = analyzer(repository, cache, [page, page])
    with pytest.raises(RuntimeError, match="repeated"):
        run.refresh(profile, 101)
    assert repository.profile(profile)["sample_count"] == 100


def test_categories_from_read_only_local_db_and_shared_cache(dataset, tmp_path):
    repository, cache = dataset
    database = tmp_path / "tags.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE tags(id INTEGER PRIMARY KEY,name TEXT,post_count INT,category INT,ambiguous INT)")
        connection.execute("INSERT INTO tags VALUES(1,'fate_testarossa',10,4,0)")
    before = database.read_bytes()
    cache.put(metadata(1, "bardiche fate_testarossa nanoha", {"nanoha": "copyright"}))
    profile = repository.create("bardiche", "bardiche")
    run, calls = analyzer(repository, cache, [[{"id": 1, "tags": "bardiche fate_testarossa nanoha"}]], tag_database=database)
    run.refresh(profile, 1)
    assert all(row["kind"] == "relation" for row in repository.tags(profile))
    assert database.read_bytes() == before
    assert len(calls) == 1  # No single-post fetch, category API, or image fetch.


def test_transient_retry_and_cancel_during_backoff(dataset):
    repository, cache = dataset
    profile = repository.create("object", "object")
    calls = []
    def search(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError("https://gelbooru.com", 503, "busy", {}, None)
        return [{"id": 1, "tags": "object staff"}]
    run = EntityProfileAnalyzer(repository, SimpleNamespace(search_posts=search), cache,
                                throttle_seconds=0, retry_delays=(0,))
    assert run.refresh(profile, 1).new_posts == 1
    assert len(calls) == 2
    calls.clear()
    cancelled = Event()
    assert run.refresh(profile, 2, cancelled=cancelled,
                       progress=lambda _event: cancelled.set()).state == "cancelled"
    assert len(calls) == 1


def test_no_recursive_activation_and_separate_scores(dataset):
    repository, _cache = dataset
    fate = repository.create("fate_testarossa", "fate_testarossa")
    bardiche = repository.create("bardiche", "bardiche")
    repository.add_posts(fate, [metadata(1, "fate_testarossa bardiche", {"bardiche": "character"})])
    repository.add_posts(bardiche, [metadata(1, "bardiche weapon")])
    repository.set_status(fate, "bardiche", "keep")
    repository.set_status(bardiche, "weapon", "keep")
    derived = repository.suggestions(activation_tags({"fate_testarossa"}, set()))
    assert {suggestion.tag for suggestion in derived} == {"bardiche"}
    assert {suggestion.tag for suggestion in repository.suggestions(
        activation_tags({"fate_testarossa"}, set(), [item.tag for item in derived]))} == {"bardiche"}
    assert {suggestion.tag for suggestion in repository.suggestions(
        activation_tags({"fate_testarossa"}, {"bardiche"}))} == {"bardiche", "weapon"}
    rows = {"weapon": {"id": 10, "tag": "weapon", "confidence": "0.810", "match": "WD14", "decision": "unreviewed"}}
    merge_profile_suggestions(rows, [EntitySuggestion(bardiche, "bardiche", "weapon", "general", .64)])
    assert rows["weapon"]["confidence"] == "0.810"
    assert rows["weapon"]["profile_scores"][0]["frequency"] == .64
    assert "64.0%" in rows["weapon"]["match"]
    rows["weapon"]["wd14_score"] = .81
    merge_profile_suggestions(rows, [EntitySuggestion(bardiche, "bardiche", "weapon", "general", .64)],
                              decisions={"weapon": {"observation_id": 10, "decision": "accepted"}})
    assert rows["weapon"]["confidence"] == "0.810"
