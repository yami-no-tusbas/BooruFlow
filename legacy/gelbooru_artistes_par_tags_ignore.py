"""Compatibility entry point for the migrated Gelbooru review scanner."""

from booruflow.cli.gelbooru_scan import *
from booruflow.cli.gelbooru_scan import main

if __name__ == "__main__":
    raise SystemExit(main())
