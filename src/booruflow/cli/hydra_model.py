"""Command line entry point used by the Hydra maintenance UI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from booruflow.application.hydra_model_manager import (
    hydra_directory,
    inspect_hydra,
    install_hydra,
    legacy_hydra_directory,
    migrate_legacy_hydra,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Manage the optional Hydra 3.5 runtime")
    result.add_argument("command", choices=("diagnose", "install", "migrate"))
    result.add_argument("--root", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    target = hydra_directory(args.root)
    if args.command == "install":
        result = install_hydra(
            target,
            progress=lambda name, done, total: print(
                f"DOWNLOAD {name} {done} {total}", flush=True
            ),
        )
    elif args.command == "migrate":
        result = migrate_legacy_hydra(legacy_hydra_directory(args.root), target)
    else:
        result = inspect_hydra(target)
    print(json.dumps({
        "state": result.state,
        "directory": str(result.directory),
        "size": result.size,
        "message": result.message,
    }), flush=True)
    return 0 if result.state == "installed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
