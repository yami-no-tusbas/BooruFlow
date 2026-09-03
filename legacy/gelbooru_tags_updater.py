"""Compatibility entry point for the migrated Gelbooru tag updater."""

from booruflow.cli.gelbooru_tags_update import main

if __name__ == "__main__":
    raise SystemExit(main())
