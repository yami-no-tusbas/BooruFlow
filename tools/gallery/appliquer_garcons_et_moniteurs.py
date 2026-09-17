from __future__ import annotations

import csv
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from tools.gallery.auditer_garcons import MEDIA_EXTENSIONS, MONITORS, OUTPUT, RESERVE, ROOTS


PLAN = OUTPUT / "inventaire_dossiers.csv"
FILE_INVENTORY = OUTPUT / "inventaire_fichiers.csv"
STAMP = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
JOURNAL = OUTPUT / f"application-{STAMP}.json"

MONITOR_ADDITIONS = (
    ("femdom", ("gelbooru.com",), "sexual"),
    ("chastity_cage", ("e621.net", "gelbooru.com"), "sexual"),
    ("flat_chastity_cage", ("e621.net", "gelbooru.com"), "sexual"),
    ("erection_under_clothes", ("gelbooru.com",), "sexual"),
    ("yaoi", ("gelbooru.com",), "sexual"),
    ("crossdressing", ("e621.net", "gelbooru.com"), "root"),
    ("trap", ("gelbooru.com",), "root"),
    ("androgynous", ("e621.net", "gelbooru.com"), "root"),
    ("gynomorph", ("e621.net",), "root"),
    ("interracial", ("gelbooru.com",), "partner"),
    ("interspecies", ("e621.net", "gelbooru.com"), "partner"),
)


def files_direct(path: Path) -> list[Path]:
    return [p for p in path.iterdir() if p.is_file() and p.suffix.casefold() in MEDIA_EXTENSIONS]


def inventory() -> tuple[int, int]:
    files = [p for root in ROOTS for p in root.rglob("*") if p.is_file() and p.suffix.casefold() in MEDIA_EXTENSIONS]
    return len(files), sum(p.stat().st_size for p in files)


def load_moves() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    moves: list[dict[str, object]] = []
    completed: list[dict[str, object]] = []
    inventoried: dict[tuple[str, str], int] = {}
    with FILE_INVENTORY.open("r", encoding="utf-8-sig", newline="") as handle:
        for item in csv.DictReader(handle):
            inventoried[(item["root"], item["relative_path"])] = int(item["bytes"])
    with PLAN.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["status"] != "move" or int(row["direct_files"]) == 0:
                continue
            root = RESERVE / row["root"]
            source_dir = root / Path(row["source"])
            destination_dir = root / Path(row["proposed"])
            expected = [
                (Path(relative), size)
                for (item_root, relative), size in inventoried.items()
                if item_root == row["root"] and Path(relative).parent.as_posix() == Path(row["source"]).as_posix()
            ]
            if len(expected) != int(row["direct_files"]) or sum(size for _, size in expected) != int(row["direct_bytes"]):
                raise RuntimeError(f"Inventaire initial incohérent: {source_dir}")
            for relative, expected_bytes in expected:
                source = root / relative
                target = destination_dir / source.name
                if source.exists() and target.exists():
                    raise FileExistsError(f"Collision: {target}")
                if source.exists():
                    if source.stat().st_size != expected_bytes:
                        raise RuntimeError(f"Taille source modifiée: {source}")
                    moves.append({"source": source, "target": target, "bytes": expected_bytes})
                elif target.exists() and target.stat().st_size == expected_bytes:
                    completed.append({"source": str(source), "target": str(target), "bytes": expected_bytes})
                else:
                    raise RuntimeError(f"Fichier absent de la source et de la destination: {source}")
    return moves, completed


def apply_moves(moves: list[dict[str, object]], journal: dict[str, object], already_completed: list[dict[str, object]]) -> None:
    completed: list[dict[str, object]] = list(already_completed)
    newly_completed: list[dict[str, object]] = []
    try:
        for move in moves:
            source = Path(move["source"])
            target = Path(move["target"])
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            record = {"source": str(source), "target": str(target), "bytes": move["bytes"]}
            completed.append(record)
            newly_completed.append(record)
    except Exception:
        for move in reversed(newly_completed):
            source = Path(move["source"])
            target = Path(move["target"])
            source.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and not source.exists():
                os.replace(target, source)
        raise
    journal["moves"] = completed


def remove_empty_directories(journal: dict[str, object]) -> None:
    removed: list[str] = []
    for root in ROOTS:
        directories = sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
        for directory in directories:
            if not any(directory.iterdir()):
                directory.rmdir()
                removed.append(str(directory))
    journal["removed_empty_directories"] = removed


def monitor_by_query(monitors: list[dict[str, object]], query: str) -> dict[str, object]:
    for monitor in monitors:
        if " ".join(monitor.get("query", {}).get("tags", [])) == query:
            return monitor
    raise KeyError(query)


def add_monitors(journal: dict[str, object]) -> None:
    raw = MONITORS.read_bytes()
    document = json.loads(raw.decode("utf-8-sig"))
    monitors = document["monitors"]
    existing = {" ".join(m.get("query", {}).get("tags", [])) for m in monitors}
    skipped = [query for query, _, _ in MONITOR_ADDITIONS if query in existing]

    sexual_template = monitor_by_query(monitors, "condom_belt")["filenameOverride"]
    root_template = monitor_by_query(monitors, "pregnant")["filenameOverride"]
    marker = "/%search%/%artist%"
    if marker not in root_template:
        raise RuntimeError("Expression de destination racine inattendue")
    partner_template = root_template.replace(marker, "/interracial & interspecies/%artist%")

    templates = {"sexual": sexual_template, "root": root_template, "partner": partner_template}
    added: list[dict[str, object]] = []
    for query, sites, template in MONITOR_ADDITIONS:
        if query in existing:
            continue
        monitor = {
            "cumulated": 0,
            "delay": 0,
            "download": True,
            "filenameOverride": templates[template],
            "getBlacklisted": False,
            "interval": 3600,
            "lastCheck": "",
            "lastState": {"count": 0, "since": "", "state": ""},
            "lastSuccess": "",
            "notify": False,
            "pathOverride": "",
            "postFilters": [],
            "preciseCumulated": True,
            "query": {"tags": [query]},
            "sites": list(sites),
        }
        monitors.append(monitor)
        added.append(monitor)

    backup = MONITORS.with_name(f"monitors.backup-garcons-{STAMP}.json")
    shutil.copy2(MONITORS, backup)
    temporary = MONITORS.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    json.loads(temporary.read_text(encoding="utf-8"))
    os.replace(temporary, MONITORS)
    reread = json.loads(MONITORS.read_text(encoding="utf-8"))
    if len(reread["monitors"]) != len(monitors):
        shutil.copy2(backup, MONITORS)
        raise RuntimeError("Validation du nombre de moniteurs échouée")
    journal["monitors"] = {
        "before": len(monitors) - len(added),
        "after": len(monitors),
        "backup": str(backup),
        "added": added,
        "skipped_existing": skipped,
    }


def main() -> None:
    before_count, before_bytes = inventory()
    moves, already_completed = load_moves()
    journal: dict[str, object] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "before": {"files": before_count, "bytes": before_bytes},
        "planned": {
            "files": len(moves) + len(already_completed),
            "bytes": sum(int(m["bytes"]) for m in moves) + sum(int(m["bytes"]) for m in already_completed),
            "already_completed_on_resume": len(already_completed),
        },
    }
    apply_moves(moves, journal, already_completed)
    after_count, after_bytes = inventory()
    if (after_count, after_bytes) != (before_count, before_bytes):
        raise RuntimeError("Le total fichiers/octets a changé après les déplacements")
    for move in journal["moves"]:
        if Path(move["source"]).exists() or not Path(move["target"]).exists():
            raise RuntimeError(f"Déplacement non validé: {move}")
    remove_empty_directories(journal)
    add_monitors(journal)
    journal["after"] = {"files": after_count, "bytes": after_bytes}
    JOURNAL.write_text(json.dumps(journal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "journal": str(JOURNAL),
        "moved_files": len(journal["moves"]),
        "moved_bytes": sum(int(m["bytes"]) for m in journal["moves"]),
        "removed_empty_directories": len(journal["removed_empty_directories"]),
        "monitors_before": journal["monitors"]["before"],
        "monitors_after": journal["monitors"]["after"],
        "files_after": after_count,
        "bytes_after": after_bytes,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
