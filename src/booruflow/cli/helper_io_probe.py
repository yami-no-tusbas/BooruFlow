"""Harmless pipe/exit-code probe for the windowed portable helper dispatch."""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("echo", "stop", "fail"), required=True)
    args = parser.parse_args()
    print("HELPER_STDOUT_READY", flush=True)
    print("HELPER_STDERR_READY", file=sys.stderr, flush=True)
    if args.mode == "fail":
        raise OSError(22, "Invalid argument")
    if args.mode == "stop":
        for line in sys.stdin or ():
            if line.strip().upper() == "STOP":
                print("STOP_ACK", flush=True)
                return 2
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
