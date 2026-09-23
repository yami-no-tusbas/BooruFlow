"""Compatibility CLI for the safe Gelbooru tag database importer v2."""

from __future__ import annotations

import argparse
import os
import sys
import threading
from contextlib import suppress
from pathlib import Path

from booruflow.infrastructure.gelbooru_tag_importer import (
    DatabaseUpdateUnavailable,
    ImportCancelled,
    rebuild_database,
    update_database,
)


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
    parser.add_argument("--mode", choices=("update", "rebuild"), default="rebuild")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    destination = args.db
    user_id = os.environ.get("GELBOORU_USER_ID", "")
    api_key = os.environ.get("GELBOORU_API_KEY", "")
    def safe(message: object) -> str:
        value = str(message)
        return value.replace(api_key, "[redacted]") if api_key else value

    cancelled = threading.Event()

    def watch_cancel() -> None:
        with suppress(OSError, ValueError):
            for line in sys.stdin or ():
                if line.strip().upper() == "STOP":
                    cancelled.set()
                    return

    threading.Thread(target=watch_cancel, name="booruflow-cancel", daemon=True).start()

    print(
        "Gelbooru tag importer v2 — "
        + ("incremental update" if args.mode == "update" else "full safe rebuild")
    )
    print(f"Destination: {destination.resolve()}")
    try:
        operation = update_database if args.mode == "update" else rebuild_database
        summary = operation(
            destination, user_id, api_key,
            progress=lambda message: print(safe(message), flush=True),
            cancelled=cancelled.is_set,
        )
    except ImportCancelled as exc:
        print(f"CANCEL_ACK {safe(exc)}", flush=True)
        return 2
    except DatabaseUpdateUnavailable as exc:
        print(f"ERROR: {safe(exc)}", flush=True)
        return 3
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports importer failures
        print(f"ERROR: {safe(exc)}", flush=True)
        return 1
    if args.mode == "update":
        print(
            f"Updated: {summary.imported:,} tags | checkpoint after_id={summary.last_id:,}",
            flush=True,
        )
    else:
        print(
            f"Validated: {summary.rows:,} tags | max id {summary.maximum_id:,} | "
            f"zero counts {summary.zero_counts:,}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
