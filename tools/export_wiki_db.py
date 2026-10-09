"""Export the configured BooruFlow catalogues, synchronizing only when needed."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from booruflow.application.database_paths import (
    gelbooru_alias_database,
    gelbooru_tag_database,
    migrate_database_settings,
)
from booruflow.application.portable_settings import PREFIX
from booruflow.infrastructure.gelbooru_wiki_export import (
    export_wiki_database,
    format_export_error,
)
from booruflow.infrastructure.settings.json_repository import JsonSettingsRepository


def _absolute_catalogue_path(root: Path, value: Path) -> Path:
    text = value.as_posix()
    if text.startswith(PREFIX):
        return (root / text[len(PREFIX) :]).resolve()
    return (value if value.is_absolute() else root / value).resolve()


def load_catalogue_paths(root: Path) -> tuple[Path, Path]:
    settings = JsonSettingsRepository(root / "config/booruflow_settings.json").load()
    # Reuse the application's pure migration rules in memory; never save settings.
    settings, _changed = migrate_database_settings(settings, root)
    tags = gelbooru_tag_database(settings)
    aliases = gelbooru_alias_database(settings)
    if tags is None or aliases is None:
        raise ValueError("Configurez les catalogues Gelbooru dans les paramètres BooruFlow")
    return _absolute_catalogue_path(root, tags), _absolute_catalogue_path(root, aliases)


def load_configuration(root: Path) -> tuple[str, str, tuple[Path, ...]]:
    """Read existing JSON directly through its read-only repository; never migrate/save."""
    stored = JsonSettingsRepository(root / "config/booruflow_credentials.json").load()
    credentials = stored.get("gelbooru", {})
    if not isinstance(credentials, dict):
        credentials = {}
    user_id = os.environ.get("GELBOORU_USER_ID", "").strip()
    api_key = os.environ.get("GELBOORU_API_KEY", "").strip()
    if bool(user_id) != bool(api_key):
        raise ValueError("Set both GELBOORU_USER_ID and GELBOORU_API_KEY, or neither")
    if not user_id:
        user_id = str(credentials.get("user_id", "")).strip()
        api_key = str(credentials.get("api_key", "")).strip()
    protected = []
    for filename in ("booruflow_settings.json", "gelbooru_tagging_settings.json"):
        settings = JsonSettingsRepository(root / "config" / filename).load()
        for key in (
            "gelbooru_tag_database",
            "gelbooru_database",
            "gelbooru_alias_database",
            "e621_database",
        ):
            value = settings.get(key)
            if isinstance(value, str) and value.strip():
                path = Path(value)
                protected.append(_absolute_catalogue_path(root, path))
    return user_id, api_key, tuple(protected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Actualise les catalogues BooruFlow si nécessaire (24 h), puis exporte leurs copies SQLite."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "var/exports/gelbooru_wiki_tags.db",
        help="Fichier .db de sortie (défaut : var/exports/gelbooru_wiki_tags.db).",
    )
    parser.add_argument(
        "--force-update",
        action="store_true",
        help="Actualiser les deux catalogues même si leur réussite date de moins de 24 h.",
    )
    args = parser.parse_args(argv)
    user_id = api_key = ""
    try:
        user_id, api_key, protected = load_configuration(ROOT)
        tags_database, aliases_database = load_catalogue_paths(ROOT)
        output = args.output.resolve()
        for relative in ("data", "@app", "config", "resources", "var/state", "var/cache"):
            if output.is_relative_to((ROOT / relative).resolve()):
                raise ValueError("La destination appartient aux données de production BooruFlow")
        export_wiki_database(
            args.output,
            user_id,
            api_key,
            tags_database=tags_database,
            aliases_database=aliases_database,
            force_update=args.force_update,
            protected_paths=protected,
            progress=lambda message: print(message, flush=True),
        )
    except KeyboardInterrupt:
        print("Export interrompu ; la précédente base est conservée.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI boundary redacts authenticated errors.
        print("Échec de l'export :", file=sys.stderr)
        print(format_export_error(exc, api_key, user_id), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
