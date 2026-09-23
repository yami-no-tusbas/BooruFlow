"""Explicit downloader and diagnostics for the optional local WD14 model."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from booruflow.infrastructure.wd14 import (
    DEFAULT_MODEL_ID,
    METADATA_FILENAME,
    MODEL_FILENAME,
    TAGS_FILENAME,
    WD14Config,
    diagnose_wd14,
)

BASE_URL = "https://huggingface.co/SmilingWolf/wd-vit-tagger-v3/resolve/main"
FILES = ((MODEL_FILENAME, f"{BASE_URL}/{MODEL_FILENAME}"),
         (TAGS_FILENAME, f"{BASE_URL}/{TAGS_FILENAME}"))


def _download(url: str, destination: Path) -> str:
    partial = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256(); completed = 0
    request = urllib.request.Request(url, headers={"User-Agent": "BooruFlow-WD14/1"})
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as stream:
        total = int(response.headers.get("Content-Length", "0"))
        while chunk := response.read(1024 * 1024):
            stream.write(chunk); digest.update(chunk); completed += len(chunk)
            print(f"DOWNLOAD {destination.name} {completed} {total}", flush=True)
    if completed == 0 or (total > 0 and completed != total):
        raise ValueError(f"Incomplete WD14 download: {destination.name}")
    print(f"VERIFYING {destination.name}", flush=True)
    os.replace(partial, destination)
    return digest.hexdigest()


def install(directory: Path, model_id: str) -> None:
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".wd14-install-", dir=directory.parent) as staging_name:
        staging = Path(staging_name)
        hashes = {name: _download(url, staging / name) for name, url in FILES}
        metadata = {
            "model_id": model_id,
            "model_sha256": hashes[MODEL_FILENAME],
            "tags_sha256": hashes[TAGS_FILENAME],
            "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "sources": {name: url for name, url in FILES},
        }
        (staging / METADATA_FILENAME).write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        if any(not (staging / name).is_file() or (staging / name).stat().st_size == 0
               for name, _url in FILES):
            raise ValueError("WD14 download is incomplete")
        print("INSTALLING WD14", flush=True)
        backup = directory.with_name(directory.name + ".previous")
        if backup.exists():
            raise FileExistsError(f"WD14 backup already exists: {backup}")
        if directory.exists():
            os.replace(directory, backup)
        try:
            os.replace(staging, directory)
        except OSError:
            if backup.exists():
                os.replace(backup, directory)
            raise
        if backup.exists():
            if {path.name for path in backup.iterdir()} <= {
                MODEL_FILENAME, TAGS_FILENAME, METADATA_FILENAME,
            }:
                shutil.rmtree(backup)
            else:
                print(f"WARNING: Previous WD14 files preserved at {backup}", flush=True)
    print(f"INSTALLED {model_id} {directory}", flush=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Manage the optional WD14 ONNX model")
    result.add_argument("command", choices=("install", "diagnose"))
    result.add_argument("--directory", type=Path, required=True)
    result.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "install":
        install(args.directory, args.model_id)
        return 0
    diagnostic = diagnose_wd14(WD14Config(args.directory, args.model_id))
    print(json.dumps({
        "available": diagnostic.available, "model_id": diagnostic.model_id,
        "runtime": diagnostic.runtime, "provider": diagnostic.provider,
        "device": diagnostic.device, "message": diagnostic.message,
        "expected_cuda": diagnostic.onnx.expected_cuda if diagnostic.onnx else "",
        "expected_cudnn": diagnostic.onnx.expected_cudnn if diagnostic.onnx else "",
        "announced_providers": diagnostic.onnx.announced_providers if diagnostic.onnx else (),
        "active_providers": diagnostic.onnx.active_providers if diagnostic.onnx else (),
        "preload_available": diagnostic.onnx.preload_available if diagnostic.onnx else False,
        "preload_succeeded": diagnostic.onnx.preload_succeeded if diagnostic.onnx else False,
        "cuda_runtime_installed": (
            diagnostic.onnx.cuda_runtime_installed if diagnostic.onnx else False
        ),
        "cudnn_installed": diagnostic.onnx.cudnn_installed if diagnostic.onnx else False,
    }))
    return 0 if diagnostic.available else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
