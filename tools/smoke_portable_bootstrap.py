"""Check bootstrap behavior in an isolated copy of a built portable folder."""

import argparse
import json
from pathlib import Path

from booruflow.application.bundled_database_bootstrap import (
    BOOTSTRAP_NAMES,
    prepare_bundled_database,
    sha256_file,
    validate_database,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    assert (root / "BooruFlow.exe").is_file()
    manifest = json.loads((root / "bootstrap" / "manifest.json").read_text(encoding="utf-8"))
    for kind, (archive_name, _member, _table) in BOOTSTRAP_NAMES.items():
        entry = manifest[kind]
        archive = root / "bootstrap" / archive_name
        destination = root / entry["target"]
        assert not destination.exists(), f"Smoke copy is not fresh: {kind}"
        assert archive.is_file() and sha256_file(archive) == entry["archive_sha256"]
        progress = []
        result = prepare_bundled_database(
            root, kind, destination,
            progress=lambda _name, current, total, records=progress: records.append((current, total)),
        )
        assert result.state == "installed" and not result.warning, (kind, result)
        assert progress and progress[-1] == (entry["uncompressed_size"], entry["uncompressed_size"])
        assert sha256_file(destination) == entry["database_sha256"]
        assert validate_database(destination, kind) == entry.get("after_id")
        assert not archive.exists()
        assert prepare_bundled_database(root, kind, destination).state == "ready"
        print(f"{kind}: installed, validated, archive removed, restart ready")


if __name__ == "__main__":
    main()
