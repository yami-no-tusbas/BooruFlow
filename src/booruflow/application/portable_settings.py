"""Keep bundled defaults relocatable while exposing absolute paths to the app."""

from __future__ import annotations

from pathlib import Path

from booruflow.infrastructure.settings import JsonSettingsRepository

PORTABLE_PATHS = {
    "gelbooru_tag_database": Path("data/databases/gelbooru_tags.db"),
    "gelbooru_alias_database": Path("data/databases/gelbooru_aliases.db"),
    "e621_database": Path("data/databases/e621_tags.db"),
    "output_root": Path("var/results"),
    "image_analysis_wd14_model_directory": Path("var/models/image_analysis/wd-vit-tagger-v3"),
    "image_analysis_hydra_source_directory": Path("var/models/hydra/3.5"),
    "image_analysis_hydra_model_path": Path("var/models/hydra/3.5/hydra-3.5.safetensors"),
}
PREFIX = "@app/"


class PortableSettingsRepository(JsonSettingsRepository):
    """Persist only canonical internal defaults as paths relative to the exe."""

    def __init__(self, path: Path, root: Path) -> None:
        super().__init__(path)
        self.root = root.resolve()

    def load(self) -> dict[str, object]:
        stored = super().load()
        if not stored:
            return stored
        # Pre-marker Alpha builds wrote their original executable directory into
        # every default. Require agreement across multiple keys before migrating;
        # a user-selected absolute path must remain untouched.
        candidates: dict[Path, int] = {}
        for key, suffix in PORTABLE_PATHS.items():
            value = str(stored.get(key, ""))
            if value.startswith(PREFIX):
                continue
            path = Path(value)
            if not path.is_absolute() or tuple(path.parts[-len(suffix.parts):]) != suffix.parts:
                continue
            old_root = Path(*path.parts[:-len(suffix.parts)])
            candidates[old_root] = candidates.get(old_root, 0) + 1
        old_roots = {root for root, count in candidates.items() if count >= 2 and root != self.root}
        migrated = dict(stored)
        for key, suffix in PORTABLE_PATHS.items():
            value = str(stored.get(key, ""))
            if value == f"{PREFIX}{suffix.as_posix()}" or any(
                Path(value) == old_root / suffix for old_root in old_roots
            ):
                migrated[key] = str(self.root / suffix)
        if migrated != stored:
            self.save(migrated)
        return migrated

    def save(self, values: dict[str, object]) -> None:
        stored = dict(values)
        for key, suffix in PORTABLE_PATHS.items():
            if str(values.get(key, "")) == str(self.root / suffix):
                stored[key] = f"{PREFIX}{suffix.as_posix()}"
        super().save(stored)
