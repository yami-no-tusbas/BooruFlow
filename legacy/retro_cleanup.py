"""Compatibility entry point for the migrated retroactive-cleanup service."""

from booruflow.infrastructure.retro_cleanup import *  # noqa: F403
from booruflow.infrastructure.retro_cleanup import main


if __name__ == "__main__":
    raise SystemExit(main())
