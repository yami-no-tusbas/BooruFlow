"""Minimal Image Analysis process entry, deliberately free of heavy imports."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path


def _argument_value(name: str) -> str:
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except (ValueError, IndexError):
        return ""


def _log_path() -> Path:
    database = _argument_value("--database")
    if database:
        return Path(database).resolve().parent / "worker-bootstrap.log"
    return Path.cwd() / "worker-bootstrap.log"


def bootstrap_log(message: str) -> None:
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).isoformat(timespec="milliseconds")
    line = f"{timestamp} pid={os.getpid()} {message}\n"
    with path.open("a", encoding="utf-8", buffering=1) as stream:
        stream.write(line)
    print(f"BOOTSTRAP {message}", flush=True)


def main() -> int:
    bootstrap_log("process entry")
    bootstrap_log(f"argv={sys.argv!r}")
    bootstrap_log(f"parent_pid received={_argument_value('--parent-pid') or '0'}")
    bootstrap_log("import worker module begin")
    from booruflow.worker.image_analysis import main as worker_main
    bootstrap_log("import worker module complete")
    return worker_main(sys.argv[1:], bootstrap_log=bootstrap_log)


if __name__ == "__main__":
    raise SystemExit(main())
