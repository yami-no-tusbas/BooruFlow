"""Atomic installation and migration of the optional Hydra 3.5 runtime."""

from __future__ import annotations

import hashlib
import os
import shutil
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

HYDRA_VERSION = "3.5"
HYDRA_REVISION = "cfa9b0a1ffcf2b8df8553be7673210fd60fba23b"
HYDRA_BASE_URL = f"https://huggingface.co/RedRocket/Hydra/resolve/{HYDRA_REVISION}"


@dataclass(frozen=True, slots=True)
class HydraArtifact:
    relative_path: str
    size: int
    sha256: str

    @property
    def url(self) -> str:
        source = (
            "models/hydra-3.5.safetensors"
            if self.relative_path == "hydra-3.5.safetensors"
            else self.relative_path
        )
        return f"{HYDRA_BASE_URL}/{source}?download=true"


HYDRA_ARTIFACTS = (
    HydraArtifact("hydra-3.5.safetensors", 1_064_526_448,
                  "5e9337c21019c51f2bb5b33d053ea3e2c0412e5ca972b5ea3c4c21e6987452ca"),
    HydraArtifact("hydra/__init__.py", 262,
                  "4f8d5400c8446f87f63444d44558a758ca7724b4d8815b4ff39f287b991803a0"),
    HydraArtifact("hydra/classification.py", 19_424,
                  "903f22bb33c7b51f0ad1d8db1a2d8f812f74999c0662a3c98996cfa21416ddf3"),
    HydraArtifact("hydra/cufork.py", 2_054,
                  "e4c4df2986f6a2cd22fafdac5ccfbac381539b55346a9f50068e71fc43399c58"),
    HydraArtifact("hydra/glu.py", 1_370,
                  "315f435469f81966b653c3cfa9ba263f8d1e09e20b00b79ac8b08f7f1b62ac40"),
    HydraArtifact("hydra/head.py", 3_957,
                  "b58d1ab7cae37e4e22a37e8e071895d719128688860e2ac39cbfdbdc50415bbd"),
    HydraArtifact("hydra/image.py", 12_561,
                  "b62929949cb36a325b28bf643fb41d732765625ea0f8c8da75f4a517ee711f3d"),
    HydraArtifact("hydra/label.py", 10_155,
                  "85b777582457c024c30678250f042a7fc5593b826c4b47b13c74bb9392bec3d3"),
    HydraArtifact("hydra/model.py", 18_323,
                  "034ab845e1a4d7c7e1eaae977559d8a362d7277e4c5e58bd613d3cacb73a34b3"),
    HydraArtifact("hydra/pool.py", 10_077,
                  "3b67c9052a151e030802143c8cec1ae4ec2cacc4af528eb14ccd2ddfda0c84fa"),
    HydraArtifact("hydra/siglip2.py", 15_521,
                  "f18d8b8f6edb5de796cdb0974d3746ed793f87cd96eeb94ff494cfff82c4b8c4"),
)


@dataclass(frozen=True, slots=True)
class HydraInstallation:
    state: str
    directory: Path
    size: int
    message: str


def hydra_directory(root: Path) -> Path:
    return root / "var" / "models" / "hydra" / HYDRA_VERSION


def legacy_hydra_directory(root: Path) -> Path:
    return root / "var" / "models" / "image_analysis" / "hydra-3.5-src"


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_hydra(directory: Path) -> HydraInstallation:
    if not directory.exists():
        return HydraInstallation("absent", directory, 0, "Hydra 3.5 is not installed")
    size = 0
    for artifact in HYDRA_ARTIFACTS:
        path = directory / artifact.relative_path
        try:
            actual_size = path.stat().st_size
        except OSError:
            return HydraInstallation("invalid", directory, size, f"Missing {artifact.relative_path}")
        size += actual_size
        if actual_size != artifact.size:
            return HydraInstallation("invalid", directory, size, f"Invalid size for {artifact.relative_path}")
        if _digest(path) != artifact.sha256:
            return HydraInstallation("invalid", directory, size, f"Invalid SHA256 for {artifact.relative_path}")
    return HydraInstallation("installed", directory, size, "Hydra 3.5 is installed and verified")


@contextmanager
def _staging_directory(target: Path) -> Iterator[Path]:
    staging = target.parent / f".{target.name}.installing-{uuid4().hex}"
    staging.mkdir(parents=True)
    try:
        yield staging
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _activate(staging: Path, target: Path) -> None:
    backup = target.parent / f".{target.name}.previous-{uuid4().hex}"
    target.parent.mkdir(parents=True, exist_ok=True)
    had_target = target.exists()
    try:
        if had_target:
            os.replace(target, backup)
        os.replace(staging, target)
    except Exception:
        if had_target and backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    else:
        if backup.exists():
            shutil.rmtree(backup)


def _copy_legacy_artifact(legacy: Path, artifact: HydraArtifact, destination: Path) -> None:
    source = (
        legacy / "models" / "hydra-3.5.safetensors"
        if artifact.relative_path == "hydra-3.5.safetensors"
        else legacy / artifact.relative_path
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def migrate_legacy_hydra(legacy: Path, target: Path) -> HydraInstallation:
    """Copy only the verified runtime; the legacy clone is deliberately retained."""
    with _staging_directory(target) as staging:
        for artifact in HYDRA_ARTIFACTS:
            _copy_legacy_artifact(legacy, artifact, staging / artifact.relative_path)
        result = inspect_hydra(staging)
        if result.state != "installed":
            raise ValueError(result.message)
        _activate(staging, target)
    return inspect_hydra(target)


def _download(
    artifact: HydraArtifact,
    destination: Path,
    opener: Callable = urllib.request.urlopen,
    progress: Callable[[str, int, int], None] | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(artifact.url, headers={"User-Agent": "BooruFlow-Hydra/1"})
    completed = 0
    digest = hashlib.sha256()
    try:
        with opener(request, timeout=60) as response, partial.open("wb") as stream:
            while chunk := response.read(4 * 1024 * 1024):
                stream.write(chunk)
                digest.update(chunk)
                completed += len(chunk)
                if progress:
                    progress(artifact.relative_path, completed, artifact.size)
        if completed != artifact.size or digest.hexdigest() != artifact.sha256:
            raise ValueError(f"Integrity check failed for {artifact.relative_path}")
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def install_hydra(
    target: Path,
    opener: Callable = urllib.request.urlopen,
    progress: Callable[[str, int, int], None] | None = None,
) -> HydraInstallation:
    with _staging_directory(target) as staging:
        for artifact in HYDRA_ARTIFACTS:
            _download(artifact, staging / artifact.relative_path, opener, progress)
        result = inspect_hydra(staging)
        if result.state != "installed":
            raise ValueError(result.message)
        _activate(staging, target)
    return inspect_hydra(target)


def migrated_hydra_settings(settings: dict[str, object], root: Path) -> tuple[dict[str, object], bool]:
    target = hydra_directory(root)
    if inspect_hydra(target).state != "installed":
        return settings, False
    updated = dict(settings)
    source_value = str(updated.get("image_analysis_hydra_source_directory", ""))
    model_value = str(updated.get("image_analysis_hydra_model_path", ""))
    legacy = legacy_hydra_directory(root)
    allowed_sources = {"", str(legacy), str(target)}
    allowed_models = {
        "",
        str(legacy / "models" / "hydra-3.5.safetensors"),
        str(target / "hydra-3.5.safetensors"),
    }
    if source_value not in allowed_sources or model_value not in allowed_models:
        return settings, False
    updated["image_analysis_hydra_source_directory"] = str(target)
    updated["image_analysis_hydra_model_path"] = str(target / "hydra-3.5.safetensors")
    return updated, updated != settings


def remove_hydra(
    root: Path,
    *,
    confirmed: bool,
    recycler: Callable[[Iterable[Path]], tuple[bool, str]],
) -> int:
    """Remove only the clean Hydra directory after an explicit confirmation."""
    if not confirmed:
        raise PermissionError("Hydra removal requires explicit confirmation")
    target = hydra_directory(root)
    expected_parent = (root / "var" / "models" / "hydra").resolve()
    if target.resolve().parent != expected_parent:
        raise ValueError("Hydra removal target escaped the models directory")
    installation = inspect_hydra(target)
    if installation.state == "absent":
        return 0
    success, message = recycler((target,))
    if not success:
        raise OSError(message)
    return installation.size
