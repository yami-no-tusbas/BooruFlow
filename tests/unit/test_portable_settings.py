import json
from pathlib import Path

from booruflow.application.portable_settings import PORTABLE_PATHS, PortableSettingsRepository


def test_copied_alpha_config_resolves_internal_paths_at_new_root(tmp_path: Path) -> None:
    old_root = tmp_path / "A" / "BooruFlow"
    new_root = tmp_path / "B" / "BooruFlow"
    old_config = old_root / "config" / "booruflow_settings.json"
    old_config.parent.mkdir(parents=True)
    original = {key: str(old_root / suffix) for key, suffix in PORTABLE_PATHS.items()}
    original["blacklist_file"] = str(tmp_path / "chosen" / "blacklist.txt")
    old_config.write_text(json.dumps(original), encoding="utf-8")
    new_config = new_root / "config" / old_config.name
    new_config.parent.mkdir(parents=True)
    new_config.write_bytes(old_config.read_bytes())

    repository = PortableSettingsRepository(new_config, new_root)
    loaded = repository.load()
    for key, suffix in PORTABLE_PATHS.items():
        assert loaded[key] == str(new_root / suffix)
    assert loaded["blacklist_file"] == original["blacklist_file"]
    persisted = json.loads(new_config.read_text(encoding="utf-8"))
    assert all(str(old_root) not in str(value) for value in persisted.values())
    assert repository.load() == loaded


def test_explicit_absolute_database_is_preserved(tmp_path: Path) -> None:
    root = tmp_path / "portable"
    repository = PortableSettingsRepository(root / "config" / "settings.json", root)
    external = tmp_path / "external" / "gelbooru_tags.db"
    repository.save({"gelbooru_tag_database": str(external)})
    assert repository.load()["gelbooru_tag_database"] == str(external)
