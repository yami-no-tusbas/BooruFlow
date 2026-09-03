"""External localization catalogs."""

from .catalog import LanguageCatalog
from .legacy_logs import translate_legacy_log

__all__ = ["LanguageCatalog", "translate_legacy_log"]
