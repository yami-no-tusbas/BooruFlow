"""Compatibility entry point for the migrated e621 tag updater."""

from booruflow.cli.e621_tags_update import *
from booruflow.cli.e621_tags_update import main

if __name__ == "__main__":
    raise SystemExit(main())
