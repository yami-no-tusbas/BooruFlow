import hashlib
import io

import pytest
from PIL import Image

from booruflow.domain.image_analysis import AnalysisItem, InputKind, SourceReference
from booruflow.infrastructure.image_analysis_cache import ImageAnalysisCache
from booruflow.infrastructure.image_analysis_repository import ImageAnalysisRepository
from booruflow.infrastructure.image_sources import (
    ImageSourceError,
    ImageSourceService,
    NormalizedPost,
)


def png_bytes(color: str = "red") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (7, 5), color).save(output, format="PNG")
    return output.getvalue()


def put(cache: ImageAnalysisCache, key: str, data: bytes):
    return cache.put(
        key, data, site="gelbooru", post_id="42", source_kind="sample",
        mime_type="image/png", width=7, height=5, sha256=key,
    )


def test_blob_cache_persists_raw_bytes_and_metadata(tmp_path):
    path = tmp_path / "image_analysis_cache.db"
    data = png_bytes()
    key = hashlib.sha256(data).hexdigest()
    with ImageAnalysisCache(path, max_bytes=10_000) as cache:
        result = put(cache, key, data)
        assert result.stored and result.entry_count == 1
        assert not result.replaced
    with ImageAnalysisCache(path, max_bytes=10_000) as cache:
        entry = cache.get(key)
        assert entry is not None
        assert entry.data == data
        assert (entry.site, entry.post_id, entry.source_kind) == (
            "gelbooru", "42", "sample"
        )
        assert (entry.mime_type, entry.width, entry.height) == ("image/png", 7, 5)
        assert cache.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_lru_touch_and_upsert_control_eviction(tmp_path, monkeypatch):
    moments = iter(("1", "2", "3", "4", "5"))
    monkeypatch.setattr(
        "booruflow.infrastructure.image_analysis_cache.utc_now", lambda: next(moments)
    )
    a = hashlib.sha256(b"aaa").hexdigest()
    b = hashlib.sha256(b"bbb").hexdigest()
    c = hashlib.sha256(b"ccc").hexdigest()
    with ImageAnalysisCache(tmp_path / "cache.db", max_bytes=6) as cache:
        put(cache, a, b"aaa")
        put(cache, b, b"bbb")
        assert cache.get(a).data == b"aaa"
        result = put(cache, c, b"ccc")
        assert result.evicted == 1
        assert cache.contains(a) and cache.contains(c)
        assert not cache.contains(b)
        replaced = put(cache, a, b"aaa")
        assert replaced.replaced
        assert cache.stats()["logical_bytes"] == 6


def test_oversized_entry_is_refused_without_evicting_existing_data(tmp_path):
    with ImageAnalysisCache(tmp_path / "cache.db", max_bytes=4) as cache:
        small = hashlib.sha256(b"1234").hexdigest()
        large = hashlib.sha256(b"12345").hexdigest()
        put(cache, small, b"1234")
        result = put(cache, large, b"12345")
        assert not result.stored
        assert cache.get(small).data == b"1234"


def test_materialized_file_is_deleted_on_success_and_failure(tmp_path):
    data = png_bytes()
    key = hashlib.sha256(data).hexdigest()
    with ImageAnalysisCache(tmp_path / "cache.db") as cache:
        put(cache, key, data)
        with cache.materialize(key, suffix=".png") as path:
            success_path = path
            assert path.read_bytes() == data
        assert not success_path.exists()
        with pytest.raises(RuntimeError), cache.materialize(key, suffix=".png") as path:
            failure_path = path
            raise RuntimeError("analysis failed")
        assert not failure_path.exists()


def test_remote_source_creates_no_loose_image_and_survives_restart(tmp_path):
    data = png_bytes("blue")
    database = tmp_path / "state.sqlite"
    legacy = tmp_path / "image_analysis"
    with ImageAnalysisRepository(database) as repository:
        with ImageSourceService(
            repository, legacy, bytes_fetcher=lambda _url, _headers: data
        ) as sources:
            post = type("Provider", (), {"fetch_post": lambda _self, post_id: NormalizedPost(
                "gelbooru", post_id, "https://example/image.png", (), ()
            )})()
            item_id = sources.add_post(post, "42")
            item = repository.get_item(item_id)
            assert item.cached_path is None
            assert sources.cached_bytes(item) == data
        assert not legacy.exists()
        assert (tmp_path / "image_analysis_cache.db").is_file()
        assert not any(
            path.suffix.casefold() in {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
            for path in tmp_path.rglob("*") if path.is_file()
        )
    with ImageAnalysisRepository(database) as repository, ImageSourceService(
        repository, legacy
    ) as sources:
        assert sources.cached_bytes(repository.get_item(item_id)) == data


def test_blob_preview_wins_over_missing_stale_legacy_path(tmp_path):
    data = png_bytes("blue")
    digest = hashlib.sha256(data).hexdigest()
    missing = tmp_path / "image_analysis" / f"{digest}.png"
    with ImageAnalysisRepository(tmp_path / "state.sqlite") as repository, ImageSourceService(
        repository, tmp_path / "image_analysis"
    ) as sources:
        item = AnalysisItem(
            SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="42"),
            cached_path=missing, content_sha256=digest, mime_type="image/png",
            width=7, height=5,
        )
        put(sources.cache, digest, data)
        trace = {}
        assert sources.cached_bytes(item, trace=trace.__setitem__) == data
        assert trace["preview_source"] == "blob"
        assert not missing.exists()


def test_preview_falls_back_to_existing_legacy_path_when_blob_is_absent(tmp_path):
    data = png_bytes("green")
    digest = hashlib.sha256(data).hexdigest()
    legacy = tmp_path / "image_analysis"
    legacy.mkdir()
    path = legacy / f"{digest}.png"
    path.write_bytes(data)
    with ImageAnalysisRepository(tmp_path / "state.sqlite") as repository, ImageSourceService(
        repository, legacy
    ) as sources:
        item = AnalysisItem(
            SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="43"),
            cached_path=path, content_sha256=digest, mime_type="image/png",
            width=7, height=5,
        )
        trace = {}
        assert sources.cached_bytes(item, trace=trace.__setitem__) == data
        assert trace["preview_source"] == "legacy_path"


def test_preview_with_no_blob_or_valid_path_is_unavailable(tmp_path):
    digest = hashlib.sha256(b"missing").hexdigest()
    with ImageAnalysisRepository(tmp_path / "state.sqlite") as repository, ImageSourceService(
        repository, tmp_path / "image_analysis"
    ) as sources:
        item = AnalysisItem(
            SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="44"),
            cached_path=tmp_path / "missing.png", content_sha256=digest,
            mime_type="image/png", width=7, height=5,
        )
        trace = {}
        with pytest.raises(ImageSourceError, match="source image unavailable"):
            sources.cached_bytes(item, trace=trace.__setitem__)
        assert trace["preview_source"] == "unavailable"


def test_legacy_migration_commits_blob_then_clears_path_and_deletes_file(tmp_path):
    data = png_bytes()
    digest = hashlib.sha256(data).hexdigest()
    legacy = tmp_path / "image_analysis"
    legacy.mkdir()
    path = legacy / f"{digest}.png"
    path.write_bytes(data)
    with ImageAnalysisRepository(tmp_path / "state.sqlite") as repository:
        item_id = repository.add_item(AnalysisItem(
            SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="42"),
            cached_path=path, content_sha256=digest, mime_type="image/png",
            width=7, height=5,
        ), request_analysis=True)
        with ImageSourceService(repository, legacy) as sources:
            report = sources.migrate_legacy_cache()
            assert report["imported"] == report["deleted"] == 1
            assert sources.cached_bytes(repository.get_item(item_id)) == data
        assert not path.exists()
        assert repository.get_item(item_id).cached_path is None


def test_legacy_migration_failure_preserves_path_and_database_reference(tmp_path, monkeypatch):
    data = png_bytes()
    digest = hashlib.sha256(data).hexdigest()
    legacy = tmp_path / "image_analysis"
    legacy.mkdir()
    path = legacy / f"{digest}.png"
    path.write_bytes(data)
    with ImageAnalysisRepository(tmp_path / "state.sqlite") as repository:
        item_id = repository.add_item(AnalysisItem(
            SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="42"),
            cached_path=path, content_sha256=digest, mime_type="image/png",
            width=7, height=5,
        ), request_analysis=True)
        with ImageSourceService(repository, legacy) as sources:
            monkeypatch.setattr(sources.cache, "put", lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("synthetic write failure")
            ))
            report = sources.migrate_legacy_cache()
        assert report["failed"] == 1
        assert path.read_bytes() == data
        assert repository.get_item(item_id).cached_path == path


def test_legacy_migration_replays_unlink_after_committed_database_update(tmp_path):
    data = png_bytes()
    digest = hashlib.sha256(data).hexdigest()
    legacy = tmp_path / "image_analysis"
    legacy.mkdir()
    path = legacy / f"{digest}.png"
    path.write_bytes(data)
    with ImageAnalysisRepository(tmp_path / "state.sqlite") as repository:
        with ImageSourceService(repository, legacy) as sources:
            put(sources.cache, digest, data)
            report = sources.migrate_legacy_cache()
        assert report["deleted"] == 1
        assert not path.exists()


def test_legacy_migration_cancellation_leaves_unprocessed_files(tmp_path):
    legacy = tmp_path / "image_analysis"
    legacy.mkdir()
    data = png_bytes()
    digest = hashlib.sha256(data).hexdigest()
    path = legacy / f"{digest}.png"
    path.write_bytes(data)
    with ImageAnalysisRepository(tmp_path / "state.sqlite") as repository:
        repository.add_item(AnalysisItem(
            SourceReference(InputKind.GELBOORU_POST, site="gelbooru", post_id="42"),
            cached_path=path, content_sha256=digest, mime_type="image/png",
            width=7, height=5,
        ), request_analysis=False)
        with ImageSourceService(repository, legacy) as sources:
            report = sources.migrate_legacy_cache(cancelled=lambda: True)
        assert report["imported"] == report["deleted"] == 0
        assert report["remaining"] == 1
        assert path.read_bytes() == data
