from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from booruflow.application import hydra_model_manager as manager


class Response:
    def __init__(self, content: bytes, *, fail_after: bool = False) -> None:
        self.content = content
        self.offset = 0
        self.fail_after = fail_after

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _size: int) -> bytes:
        if self.fail_after and self.offset:
            raise OSError("connection interrupted")
        if self.offset >= len(self.content):
            return b""
        chunk = self.content[self.offset:self.offset + 2]
        self.offset += len(chunk)
        return chunk


@pytest.fixture
def artifacts(monkeypatch):
    contents = {
        "hydra-3.5.safetensors": b"weight",
        "hydra/__init__.py": b"source",
    }
    manifest = tuple(
        manager.HydraArtifact(name, len(content), hashlib.sha256(content).hexdigest())
        for name, content in contents.items()
    )
    monkeypatch.setattr(manager, "HYDRA_ARTIFACTS", manifest)
    return contents


def write_installation(directory: Path, contents: dict[str, bytes]) -> None:
    for name, content in contents.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def test_detects_absent_installed_and_corrupt_hydra(tmp_path: Path, artifacts) -> None:
    target = manager.hydra_directory(tmp_path)
    assert manager.inspect_hydra(target).state == "absent"
    write_installation(target, artifacts)
    assert manager.inspect_hydra(target).state == "installed"
    (target / "hydra-3.5.safetensors").write_bytes(b"broken")
    assert manager.inspect_hydra(target).state == "invalid"


def test_download_is_atomic_and_verified(tmp_path: Path, artifacts) -> None:
    target = manager.hydra_directory(tmp_path)

    def opener(request, timeout):
        del timeout
        name = "hydra-3.5.safetensors" if "models/" in request.full_url else "hydra/__init__.py"
        return Response(artifacts[name])

    result = manager.install_hydra(target, opener=opener)
    assert result.state == "installed"
    assert not list(target.parent.glob("*.part"))
    assert not list(target.parent.glob(".*.installing-*"))


def test_interrupted_reinstall_keeps_existing_installation(tmp_path: Path, artifacts) -> None:
    target = manager.hydra_directory(tmp_path)
    write_installation(target, artifacts)

    def interrupted(_request, timeout):
        del timeout
        return Response(b"weight", fail_after=True)

    with pytest.raises(OSError, match="interrupted"):
        manager.install_hydra(target, opener=interrupted)
    assert manager.inspect_hydra(target).state == "installed"
    assert not list(target.parent.glob(".*.installing-*"))


def test_invalid_download_never_replaces_existing_installation(tmp_path: Path, artifacts) -> None:
    target = manager.hydra_directory(tmp_path)
    write_installation(target, artifacts)
    with pytest.raises(ValueError, match="Integrity"):
        manager.install_hydra(target, opener=lambda *_args, **_kwargs: Response(b"wrong"))
    assert manager.inspect_hydra(target).state == "installed"


def test_migration_copies_only_runtime_and_keeps_legacy_clone(tmp_path: Path, artifacts) -> None:
    legacy = manager.legacy_hydra_directory(tmp_path)
    target = manager.hydra_directory(tmp_path)
    (legacy / "models").mkdir(parents=True)
    (legacy / "models/hydra-3.5.safetensors").write_bytes(artifacts["hydra-3.5.safetensors"])
    (legacy / "hydra").mkdir()
    (legacy / "hydra/__init__.py").write_bytes(artifacts["hydra/__init__.py"])
    (legacy / "models/jtp-3-hydra.safetensors").write_bytes(b"unused")
    (legacy / ".git/lfs/objects").mkdir(parents=True)
    (legacy / ".git/lfs/objects/copy").write_bytes(b"unused")

    assert manager.migrate_legacy_hydra(legacy, target).state == "installed"
    assert not (target / "models/jtp-3-hydra.safetensors").exists()
    assert not (target / ".git").exists()
    assert legacy.exists()


def test_settings_migrate_only_after_verified_installation(tmp_path: Path, artifacts) -> None:
    settings = {
        "image_analysis_hydra_source_directory": str(manager.legacy_hydra_directory(tmp_path)),
        "image_analysis_hydra_model_path": str(
            manager.legacy_hydra_directory(tmp_path) / "models/hydra-3.5.safetensors"
        ),
    }
    assert manager.migrated_hydra_settings(settings, tmp_path) == (settings, False)
    target = manager.hydra_directory(tmp_path)
    write_installation(target, artifacts)
    updated, changed = manager.migrated_hydra_settings(settings, tmp_path)
    assert changed
    assert updated["image_analysis_hydra_source_directory"] == str(target)
    assert updated["image_analysis_hydra_model_path"] == str(
        target / "hydra-3.5.safetensors"
    )


def test_removal_requires_confirmation_and_never_touches_wd14(
    tmp_path: Path, artifacts
) -> None:
    target = manager.hydra_directory(tmp_path)
    wd14 = tmp_path / "var/models/image_analysis/wd-vit-tagger-v3/model.onnx"
    write_installation(target, artifacts)
    wd14.parent.mkdir(parents=True)
    wd14.write_bytes(b"wd14")

    def recycler(paths):
        selected = tuple(paths)
        assert selected == (target,)
        shutil.rmtree(target)
        return True, "removed"

    with pytest.raises(PermissionError, match="confirmation"):
        manager.remove_hydra(tmp_path, confirmed=False, recycler=recycler)
    recovered = manager.remove_hydra(tmp_path, confirmed=True, recycler=recycler)
    assert recovered == sum(len(value) for value in artifacts.values())
    assert wd14.read_bytes() == b"wd14"
