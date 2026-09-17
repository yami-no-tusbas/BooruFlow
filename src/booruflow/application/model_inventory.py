"""Read-only inventory of locally installed image-analysis models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from booruflow.application.hydra_model_manager import hydra_directory, inspect_hydra


@dataclass(frozen=True, slots=True)
class ModelStorageEntry:
    key: str
    label: str
    feature: str
    path: Path
    size: int
    required: bool
    kind: str


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _tree_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    try:
        paths = path.rglob("*")
        for candidate in paths:
            total += _file_size(candidate)
    except OSError:
        pass
    return total


def inventory_models(root: Path) -> tuple[ModelStorageEntry, ...]:
    """Return known model weights and storage overhead without changing disk state."""
    models_root = root / "var" / "models"
    models = models_root / "image_analysis"
    wd14 = models / "wd-vit-tagger-v3" / "model.onnx"
    hydra_clean_root = hydra_directory(root)
    hydra_clean = hydra_clean_root / "hydra-3.5.safetensors"
    hydra_root = models / "hydra-3.5-src"
    hydra_current = hydra_root / "models" / "hydra-3.5.safetensors"
    hydra_legacy = hydra_root / "models" / "jtp-3-hydra.safetensors"
    hydra_validation = hydra_root / "data" / "jtp-3-hydra-val.csv"
    lfs = hydra_root / ".git" / "lfs" / "objects"
    known = {wd14, hydra_clean, hydra_current, hydra_legacy, hydra_validation}

    entries = [
        ModelStorageEntry("wd14", "WD14 ViT Tagger v3", "WD14 tagging", wd14,
                          _file_size(wd14), True, "weight"),
        ModelStorageEntry("hydra-3.5", "Hydra 3.5", "e621 / furry tagging", hydra_clean,
                          _tree_size(hydra_clean_root), True, "weight"),
        ModelStorageEntry("hydra-clone-active", "Hydra 3.5 dans l'ancien clone",
                          "ancienne installation e621 / furry", hydra_current,
                          _file_size(hydra_current), False, "legacy_copy"),
        ModelStorageEntry("hydra-3", "JTP Hydra 3", "ancienne variante Hydra", hydra_legacy,
                          _file_size(hydra_legacy), False, "inactive_variant"),
        ModelStorageEntry("hydra-validation", "Hydra validation dataset", "développement Hydra",
                          hydra_validation, _file_size(hydra_validation), False, "development"),
        ModelStorageEntry("hydra-lfs", "Copies Git LFS Hydra", "stockage du clone source", lfs,
                          _tree_size(lfs), False, "duplicate_storage"),
    ]
    other = 0
    if models_root.is_dir():
        for path in models_root.rglob("*"):
            if (
                path.is_file()
                and path not in known
                and lfs not in path.parents
                and hydra_clean_root not in path.parents
            ):
                other += _file_size(path)
    entries.append(ModelStorageEntry(
        "other", "Autres fichiers de modèles", "code, métadonnées et autres analyseurs",
        models_root, other, False, "support",
    ))
    return tuple(entry for entry in entries if entry.size)


def model_totals(entries: tuple[ModelStorageEntry, ...]) -> dict[str, int]:
    totals = {"wd14": 0, "embeddings": 0, "e621": 0, "other": 0, "total": 0}
    for entry in entries:
        totals["total"] += entry.size
        if entry.key == "wd14":
            totals["wd14"] += entry.size
        elif entry.key == "hydra-3.5" or (
            entry.key == "hydra-clone-active"
            and not any(item.key == "hydra-3.5" for item in entries)
        ):
            totals["e621"] += entry.size
        else:
            totals["other"] += entry.size
    return totals


def format_size(size: int) -> str:
    value = float(size)
    for suffix in ("o", "Kio", "Mio", "Gio"):
        if value < 1024 or suffix == "Gio":
            return f"{value:.2f} {suffix}" if suffix == "Gio" else f"{value:.1f} {suffix}"
        value /= 1024
    raise AssertionError("unreachable")


def hydra_status(root: Path) -> tuple[str, int, str]:
    result = inspect_hydra(hydra_directory(root))
    return result.state, result.size, result.message
