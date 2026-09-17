from booruflow.application.entity_types import ENTITY_TYPES, EntityType, entity_type
from booruflow.infrastructure.booru_cache import BooruCache
from legacy.booru_cache import BooruCache as LegacyBooruCache
from legacy.entity_types import (
    ENTITY_TYPES as LEGACY_ENTITY_TYPES,
)
from legacy.entity_types import (
    EntityType as LegacyEntityType,
)
from legacy.entity_types import (
    entity_type as legacy_entity_type,
)


def test_legacy_entity_type_module_reexports_the_migrated_objects():
    assert LEGACY_ENTITY_TYPES is ENTITY_TYPES
    assert LegacyEntityType is EntityType
    assert legacy_entity_type is entity_type


def test_legacy_cache_module_reexports_the_migrated_cache():
    assert LegacyBooruCache is BooruCache


def test_migrated_cache_initializes_a_valid_database(tmp_path):
    cache = BooruCache(tmp_path / "cache.sqlite", "gelbooru:artists")
    try:
        assert cache.connection.execute("PRAGMA quick_check(1)").fetchone()[0] == "ok"
    finally:
        cache.close()
