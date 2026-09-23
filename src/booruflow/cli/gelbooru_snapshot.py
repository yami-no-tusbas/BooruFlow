"""Install an explicitly configured database snapshot and resume updates."""

import argparse
import os
import sys
from pathlib import Path

from booruflow.application.database_snapshots import incremental_tags, install_snapshot
from booruflow.infrastructure.gelbooru_aliases import GelbooruAliasSynchronizer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-url", required=True)
    parser.add_argument("--kind", required=True, choices=("tags", "aliases"))
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    try:
        entry = install_snapshot(
            args.manifest_url, args.kind, args.db,
            progress=lambda name, done, total: print(
                f"DOWNLOAD {name} {done} {total}", flush=True
            ),
        )
        print(f"Snapshot installed: {args.kind}", flush=True)
        if args.kind == "tags":
            user_id, api_key = os.getenv("GELBOORU_USER_ID", ""), os.getenv("GELBOORU_API_KEY", "")
            if user_id and api_key:
                incremental_tags(args.db, entry["last_tag_id"], user_id, api_key,
                                 progress=lambda line: print(line, flush=True))
        else:
            result = GelbooruAliasSynchronizer(
                args.db, progress=lambda line: print(line, flush=True)
            ).incremental()
            if result.state != "completed":
                raise RuntimeError(f"Alias incremental update: {result.state}")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports all safe failures
        message = str(exc)
        for secret in (os.getenv("GELBOORU_API_KEY", ""), os.getenv("GELBOORU_USER_ID", "")):
            if secret:
                message = message.replace(secret, "[redacted]")
        print(f"ERROR: {message}", file=sys.stderr, flush=True)
        return 1
