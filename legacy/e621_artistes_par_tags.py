"""Compatibility entry point for the migrated e621 review scanner."""

from booruflow.cli.e621_scan import *
from booruflow.cli.e621_scan import main

if __name__ == "__main__":
    raise SystemExit(main())
