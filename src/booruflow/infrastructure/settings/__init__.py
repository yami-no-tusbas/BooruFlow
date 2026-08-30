"""Machine-local configuration adapters."""

from .json_repository import JsonSettingsRepository
from .migration import migrate_blacklist_setting

__all__ = ["JsonSettingsRepository", "migrate_blacklist_setting"]
