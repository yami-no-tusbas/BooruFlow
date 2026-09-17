from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from booruflow.application.wiki_aliases import resolve_copyright_alias


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resolve likely Gelbooru copyright aliases from lightweight JSON posts."
    )
    parser.add_argument("tags", nargs="+", help="Copyright tags to inspect")
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "data/databases/g_tags_260810.db",
    )
    parser.add_argument("--samples", type=int, default=20)
    args = parser.parse_args()
    user_id = os.environ.get("GELBOORU_USER_ID", "").strip()
    api_key = os.environ.get("GELBOORU_API_KEY", "").strip()
    if not user_id or not api_key:
        raise SystemExit("GELBOORU_USER_ID and GELBOORU_API_KEY are required")
    results = [
        asdict(
            resolve_copyright_alias(
                tag, args.database, user_id, api_key, sample_size=args.samples
            )
        )
        for tag in args.tags
    ]
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
