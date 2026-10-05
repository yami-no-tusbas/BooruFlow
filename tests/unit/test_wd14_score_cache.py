from __future__ import annotations

import json
import sqlite3
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import booruflow.infrastructure.image_analysis_repository as repository_module
from booruflow.domain.image_analysis import AnalysisItem, InputKind, SourceReference
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.wd14_score_cache import (
    WD14_CACHE_MIN_SCORE,
    WD14_CACHE_PRUNE_LIMIT,
    WD14_CACHE_TTL_DAYS,
    decode_wd14_scores,
    encode_wd14_scores,
    wd14_labels_hash,
)

LABELS = ("general", "1girl", "solo", "red eyes", "low", "zero")
SCORES = dict(zip(LABELS, (0.98, 0.10, 0.3, 0.8, 0.099999999, 0.0), strict=True))


def add_run(repository, post_id="1", *, model="model", version="v1", config="config"):
    item = repository.add_item(AnalysisItem(
        SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id=post_id),
    ), request_analysis=False)
    run = repository.begin_model_run(item, "wd14", model, version, config)
    return item, run


def cached(repository, **overrides):
    args = {"site": "gelbooru", "post_ids": ["1"], "model_name": "model",
            "model_version": "v1", "configuration_hash": "config", "labels": LABELS}
    args.update(overrides)
    return repository.wd14_vectors_for_remote_posts(**args)


def legacy_database(path, *, version=25, with_cache=True):
    # Build historical schemas directly, never open the user's application DB.
    schema = Path(repository_module.__file__).parent / "schema"
    with sqlite3.connect(path) as connection:
        for number in range(1, version + 1):
            connection.executescript(
                (schema / f"image_analysis_v{number}.sql").read_text(encoding="utf-8")
            )
        if not with_cache and version >= 23:
            connection.execute("DROP TABLE wd14_score_vectors")
    connection.close()


def test_exact_ordered_labels_identity_and_validation():
    assert wd14_labels_hash(LABELS) == wd14_labels_hash(tuple(LABELS))
    assert wd14_labels_hash(LABELS) != wd14_labels_hash(tuple(reversed(LABELS)))
    assert wd14_labels_hash(("ab", "c")) != wd14_labels_hash(("a", "bc"))
    assert wd14_labels_hash(("é",)) != wd14_labels_hash(("e",))
    for invalid in ((), ("x", "x"), ("",)):
        with pytest.raises(ValueError):
            wd14_labels_hash(invalid)


def test_sparse_float32_threshold_and_full_ram_vector():
    original = SCORES.copy()
    blob = encode_wd14_scores(SCORES, LABELS)
    assert SCORES == original
    assert WD14_CACHE_MIN_SCORE == 0.10
    assert blob[:8] == struct.pack("<4sI", b"WDS1", 4)
    assert len(blob) == 8 + 4 * 8
    assert list(struct.iter_unpack("<If", blob[8:])) == [
        (index, struct.unpack("<f", struct.pack("<f", SCORES[name]))[0])
        for index, name in enumerate(LABELS[:4])
    ]
    result = decode_wd14_scores(blob, LABELS)
    assert result == pytest.approx({key: SCORES[key] for key in LABELS[:4]})
    assert "low" not in result and "zero" not in result
    assert result["1girl"] != 0.10  # float32 quantization is explicit.
    assert decode_wd14_scores(encode_wd14_scores({"low": 0.01}, LABELS), LABELS) == {}


@pytest.mark.parametrize("blob", [
    b"", b"WDS1", struct.pack("<4sI", b"WDS2", 0),
    struct.pack("<4sI", b"WDS1", 1),
    struct.pack("<4sIIf", b"WDS1", 1, 99, 0.5),
    struct.pack("<4sIIf", b"WDS1", 1, 0, float("nan")),
    struct.pack("<4sIIf", b"WDS1", 1, 0, 0.099),
    struct.pack("<4sIIfIf", b"WDS1", 2, 0, 0.5, 0, 0.6),
])
def test_invalid_blobs_rejected(blob):
    with pytest.raises(ValueError):
        decode_wd14_scores(blob, LABELS)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_scores_rejected(score):
    with pytest.raises(ValueError):
        encode_wd14_scores({"solo": score}, LABELS)


def test_unknown_label_rejected_and_uint32_above_uint16():
    with pytest.raises(ValueError):
        encode_wd14_scores({"unknown": 0.5}, LABELS)
    labels = tuple(f"tag_{i}" for i in range(65538))
    blob = encode_wd14_scores({labels[-1]: 0.5}, labels)
    assert struct.unpack_from("<I", blob, 8)[0] == 65537
    assert decode_wd14_scores(blob, labels) == {labels[-1]: 0.5}


def test_shared_label_set_and_restart(tmp_path):
    path = tmp_path / "cache.sqlite"
    with ImageAnalysisRepository(path) as repo:
        for post in ("1", "2"):
            _, run = add_run(repo, post)
            repo.save_wd14_score_vector(run, SCORES, labels=LABELS)
            repo.complete_model_run(run)
        assert repo.connection.execute("SELECT count(*) FROM wd14_label_sets").fetchone()[0] == 1
        mapping = repo.connection.execute(
            "SELECT tag_index,tag_name FROM wd14_labels ORDER BY tag_index"
        ).fetchall()
        assert [tuple(row) for row in mapping] == list(enumerate(LABELS))
        assert {row[1]: row[0] for row in mapping}["solo"] == 2
        assert repo.connection.execute(
            "SELECT typeof(scores_blob) FROM wd14_score_vectors LIMIT 1"
        ).fetchone()[0] == "blob"
    with ImageAnalysisRepository(path) as reopened:
        assert cached(reopened)["1"] == pytest.approx({key: SCORES[key] for key in LABELS[:4]})
        assert reopened.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert reopened.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert reopened.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


def test_identity_separation_missing_scores_and_first_alias(tmp_path):
    with ImageAnalysisRepository(tmp_path / "cache.sqlite") as repo:
        _, run = add_run(repo)
        repo.save_wd14_score_vector(run, SCORES, labels=LABELS)
        repo.complete_model_run(run)
        for change in ({"model_name": "other"}, {"model_version": "v2"},
                       {"configuration_hash": "other"}, {"labels": LABELS[::-1]},
                       {"site": "e621"}):
            assert cached(repo, **change) == {}
        assert repo.wd14_scores_for_remote_posts(
            "gelbooru", ["1"], "model", "v1", "config", ("low",), labels=LABELS,
        ) == {}
        assert repo.wd14_scores_for_remote_posts(
            "gelbooru", ["1"], "model", "v1", "config", ("solo",), labels=LABELS,
        ) == pytest.approx({"1": 0.3})
        # Preserve full-vector first-match semantics across aliases.
        repo.save_wd14_score_vector(run, {"general": 0.01, "solo": 0.8}, labels=LABELS)
        assert repo.wd14_scores_for_remote_posts(
            "gelbooru", ["1"], "model", "v1", "config", ("general", "solo"), labels=LABELS,
        ) == {}
        for version, config, labels in (("v2", "config", LABELS),
                                         ("v1", "other", LABELS),
                                         ("v1", "config", LABELS[::-1])):
            _, other = add_run(repo, str(repo.connection.total_changes),
                               version=version, config=config)
            repo.save_wd14_score_vector(other, SCORES, labels=labels)
            repo.complete_model_run(other)
        assert repo.connection.execute("SELECT count(*) FROM wd14_label_sets").fetchone()[0] == 4


def test_corrupt_mapping_or_blob_is_cache_miss(tmp_path):
    with ImageAnalysisRepository(tmp_path / "cache.sqlite") as repo:
        _, run = add_run(repo)
        repo.save_wd14_score_vector(run, SCORES, labels=LABELS)
        repo.complete_model_run(run)
        repo.connection.execute("UPDATE wd14_score_vectors SET scores_blob=?", (b"bad",))
        repo.connection.commit()
        assert cached(repo) == {}
        repo.save_wd14_score_vector(run, SCORES, labels=LABELS)
        repo.connection.execute("UPDATE wd14_labels SET tag_name='changed' WHERE tag_index=0")
        repo.connection.commit()
        assert cached(repo) == {}


def test_ttl_is_absolute_bounded_and_preserves_related_data(tmp_path, monkeypatch):
    now = datetime(2026, 9, 24, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(repository_module, "datetime", Clock)
    assert WD14_CACHE_TTL_DAYS == 30
    with ImageAnalysisRepository(tmp_path / "cache.sqlite") as repo:
        item, run = add_run(repo)
        repo.save_wd14_score_vector(run, SCORES, labels=LABELS)
        repo.save_tag_predictions(item, run, [("solo", "general", 0.8)])
        # Deliberately share a run to exercise every ON DELETE CASCADE dependency.
        blob = struct.pack("<ff", 0.25, 0.75)
        repo.connection.execute(
            "INSERT INTO embeddings VALUES(?,?,?,?,?,?)", (item, run, blob, 2, "float32", 1),
        )
        repo.connection.execute(
            """INSERT INTO object_detections(item_id,model_run_id,label,confidence,
                   x_min,y_min,x_max,y_max) VALUES(?,?,?,.9,0,0,1,1)""", (item, run, "object"),
        )
        repo.connection.execute("INSERT INTO image_statistics(item_id,model_run_id) VALUES(?,?)",
                                (item, run))
        repo.save_review_batch_entry(item, original_tags=["solo"], additions=["new"],
                                     removals=[], reviewed_final_tags=["solo", "new"])
        tables = ("model_runs", "embeddings", "tag_observations", "object_detections",
                  "image_statistics", "tagging_review_batch_entries")
        before = {table: [tuple(row) for row in repo.connection.execute(f"SELECT * FROM {table}")]
                  for table in tables}
        cutoff = now - timedelta(days=30)
        repo.connection.execute("UPDATE wd14_score_vectors SET created_at=?",
                                ((cutoff + timedelta(seconds=1)).isoformat(),))
        repo.connection.commit()
        assert cached(repo)
        assert repo.prune_wd14_score_vectors() == 0
        repo.connection.execute("UPDATE wd14_score_vectors SET created_at=?", (cutoff.isoformat(),))
        repo.connection.commit()
        assert cached(repo) == {}
        assert repo.prune_wd14_score_vectors() == 1
        assert before == {table: [tuple(row) for row in repo.connection.execute(f"SELECT * FROM {table}")]
                          for table in tables}
        assert repo.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        for index in range(WD14_CACHE_PRUNE_LIMIT + 3):
            _, other_run = add_run(repo, str(index + 2))
            repo.save_wd14_score_vector(other_run, SCORES, labels=LABELS)
            repo.complete_model_run(other_run)
        repo.connection.execute("UPDATE wd14_score_vectors SET created_at=?", (cutoff.isoformat(),))
        repo.connection.commit()
        assert repo.prune_wd14_score_vectors() == WD14_CACHE_PRUNE_LIMIT
        assert repo.connection.execute("SELECT count(*) FROM wd14_score_vectors").fetchone()[0] == 3


@pytest.mark.parametrize(("version", "with_cache"), [(22, False), (25, False), (25, True)])
def test_legacy_migration_without_reading_json(tmp_path, version, with_cache):
    path = tmp_path / "legacy.sqlite"
    legacy_database(path, version=version, with_cache=with_cache)
    connection = sqlite3.connect(path)
    if with_cache:
        # Invalid JSON proves there is no conversion; a large BLOB avoids allocating
        # any large Python string. SQL authorizer forbids even reading this column.
        connection.execute("INSERT INTO wd14_score_vectors VALUES(1,zeroblob(4000000),'old')")
        connection.commit()
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    reads = []

    def authorize(action, table, column, *_args):
        if action == sqlite3.SQLITE_READ and table == "wd14_score_vectors" and column == "scores_json":
            reads.append(column)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)
    repo = ImageAnalysisRepository.__new__(ImageAnalysisRepository)
    repo.connection = connection
    try:
        repo._migrate()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 26
        assert connection.execute("SELECT count(*) FROM wd14_score_vectors").fetchone()[0] == 0
        assert not reads
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_migration_rollback_keeps_old_cache(tmp_path):
    path = tmp_path / "legacy.sqlite"
    legacy_database(path)
    connection = sqlite3.connect(path)
    connection.execute("INSERT INTO wd14_score_vectors VALUES(1,'legacy','old')")
    connection.commit()
    connection.set_authorizer(
        lambda action, name, *_args: sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_CREATE_TABLE and name == "wd14_labels" else sqlite3.SQLITE_OK
    )
    repo = ImageAnalysisRepository.__new__(ImageAnalysisRepository)
    repo.connection = connection
    try:
        with pytest.raises(sqlite3.DatabaseError):
            repo._migrate()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 25
        assert connection.execute("SELECT scores_json FROM wd14_score_vectors").fetchone()[0] == "legacy"
    finally:
        connection.close()


def test_migration_preserves_runs_observations_embeddings_and_publish_queue(tmp_path):
    path = tmp_path / "legacy.sqlite"
    legacy_database(path)
    repo = ImageAnalysisRepository.__new__(ImageAnalysisRepository)
    repo.connection = sqlite3.connect(path)
    repo.connection.row_factory = sqlite3.Row
    repo.connection.execute("PRAGMA foreign_keys=ON")
    try:
        item, run = add_run(repo)
        repo.save_tag_predictions(item, run, [("solo", "general", 0.9)])
        repo.save_review_batch_entry(item, original_tags=["solo"], additions=["new"],
                                     removals=[], reviewed_final_tags=["solo", "new"])
        embedding = struct.pack("<ff", 0.3, 0.7)
        repo.connection.execute("INSERT INTO embeddings VALUES(?,?,?,?,?,?)",
                                (item, run, embedding, 2, "float32", 1))
        repo.connection.execute("INSERT INTO wd14_score_vectors VALUES(?,?,?)",
                                (run, json.dumps(SCORES), "2026-09-24T00:00:00+00:00"))
        repo.connection.commit()
        tables = ("model_runs", "tag_observations", "embeddings", "tagging_review_batch_entries")
        before = {table: [tuple(row) for row in repo.connection.execute(f"SELECT * FROM {table}")]
                  for table in tables}
        repo._migrate()
        assert before == {table: [tuple(row) for row in repo.connection.execute(f"SELECT * FROM {table}")]
                          for table in tables}
        assert repo.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        repo.close()


def test_opening_database_does_not_prune_expired_cache(tmp_path):
    path = tmp_path / "cache.sqlite"
    with ImageAnalysisRepository(path) as repo:
        _, run = add_run(repo)
        repo.save_wd14_score_vector(run, SCORES, labels=LABELS)
        repo.complete_model_run(run)
        repo.connection.execute(
            "UPDATE wd14_score_vectors SET created_at='2000-01-01T00:00:00+00:00'"
        )
        repo.connection.commit()
    with ImageAnalysisRepository(path) as repo:
        assert cached(repo) == {}
        assert repo.connection.execute("SELECT count(*) FROM wd14_score_vectors").fetchone()[0] == 1


def test_concurrent_migration_and_cache_writers(tmp_path):
    path = tmp_path / "legacy.sqlite"
    legacy_database(path)
    # Production databases already use WAL. Concurrent first-ever journal-mode
    # changes exercise an unrelated SQLite initialization race.
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.close()
    gate = threading.Barrier(4)

    def write(index):
        gate.wait(timeout=10)
        with ImageAnalysisRepository(path) as repo:
            _, run = add_run(repo, str(index))
            repo.save_wd14_score_vector(run, SCORES, labels=LABELS)
            repo.complete_model_run(run)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(1, 5)))
    with ImageAnalysisRepository(path) as repo:
        assert len(cached(repo, post_ids=["1", "2", "3", "4"])) == 4
        assert repo.connection.execute("SELECT count(*) FROM wd14_label_sets").fetchone()[0] == 1
        assert repo.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert repo.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_payload_size_reduction_for_10861_outputs():
    labels = tuple(f"synthetic_descriptive_label_{index}" for index in range(10861))
    scores = {name: (0.75 if index < 24 else 0.000123456789)
              for index, name in enumerate(labels)}
    old_bytes = len(json.dumps(scores, separators=(",", ":")).encode())
    sparse_bytes = len(encode_wd14_scores(scores, labels))
    assert sparse_bytes == 200
    assert old_bytes / sparse_bytes > 1000
