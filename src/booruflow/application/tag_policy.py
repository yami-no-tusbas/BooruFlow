"""Conservative site-aware tag eligibility rules."""

from __future__ import annotations


def is_deprecated(site: str, category: int | str | None) -> bool:
    """Return true only for a status confirmed by the site's local schema."""
    return site.casefold() == "gelbooru" and str(category) == "6"

