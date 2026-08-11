"""Compatibility CLI for the safe Gelbooru tag database importer v2."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from booruflow.infrastructure.gelbooru_tag_importer import rebuild_database


def main() -> int:
    destination = Path(os.environ.get("GELBOORU_TAG_DB", "gelbooru_tags.db"))
    user_id = os.environ.get("GELBOORU_USER_ID", "")
    api_key = os.environ.get("GELBOORU_API_KEY", "")
    print("Gelbooru tag importer v2 — full safe rebuild with after_id")
    print(f"Destination: {destination.resolve()}")
    try:
        summary = rebuild_database(destination, user_id, api_key)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    print(
        f"Validated: {summary.rows:,} tags | max id {summary.maximum_id:,} | "
        f"zero counts {summary.zero_counts:,}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
