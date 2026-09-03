"""Compatibility CLI for the safe Gelbooru tag database importer v2."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from booruflow.infrastructure.gelbooru_tag_importer import rebuild_database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconstruit prudemment la base locale des tags Gelbooru.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        required=True,
        help="Base SQLite de destination explicitement sélectionnée.",
    )
    return parser.parse_args()


def main() -> int:
    destination = parse_args().db
    user_id = os.environ.get("GELBOORU_USER_ID", "")
    api_key = os.environ.get("GELBOORU_API_KEY", "")
    print("Gelbooru tag importer v2 — full safe rebuild with after_id")
    print(f"Destination: {destination.resolve()}")
    try:
        summary = rebuild_database(destination, user_id, api_key)
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports importer failures
        print(f"ERROR: {exc}")
        return 1
    print(
        f"Validated: {summary.rows:,} tags | max id {summary.maximum_id:,} | "
        f"zero counts {summary.zero_counts:,}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
