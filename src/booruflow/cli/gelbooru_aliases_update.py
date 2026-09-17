"""GET-only CLI for the local Gelbooru alias catalogue."""

from __future__ import annotations

import argparse
from pathlib import Path

from booruflow.infrastructure.gelbooru_aliases import GelbooruAliasSynchronizer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update the local Gelbooru alias catalogue.")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument(
        "--mode", required=True,
        choices=("incremental", "pending", "full", "initial"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    synchronizer = GelbooruAliasSynchronizer(args.db)
    def update():
        result = synchronizer.incremental()
        return synchronizer.initial_import() if result.state == "initial_import_required" else result

    operation = {
        "incremental": update,
        "pending": synchronizer.revalidate_pending,
        "full": synchronizer.full_reconciliation,
        "initial": synchronizer.initial_import,
    }[args.mode]
    try:
        summary = operation()
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports safe failure
        print(f"ERROR: {exc}", flush=True)
        return 1
    print(
        f"ALIAS_SUMMARY state={summary.state} active={summary.active} "
        f"pending={summary.pending} missing={summary.missing} new={summary.new} "
        f"modified={summary.modified} checkpoint={summary.checkpoint_size}",
        flush=True,
    )
    return 0 if summary.state == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
