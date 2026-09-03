"""Compatibility wrapper for the migrated SQLite cache."""

from booruflow.infrastructure.booru_cache import BooruCache, is_fresh, utc_now

__all__ = ["BooruCache", "is_fresh", "utc_now"]
