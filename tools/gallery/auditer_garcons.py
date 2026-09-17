from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


RESERVE = Path(r"D:\Réserve d'avatar v4")
ROOTS = (
    RESERVE / "Garçons (Gelbooru)",
    RESERVE / "Garçons (Gelbooru) s&l",
)
MONITORS = Path(r"D:\0ZGrabber_monitor\monitors.json")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT = PROJECT_ROOT / "outputs" / "audit_garcons"
MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif"}
MD5_RE = re.compile(r"(?i)(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])")


@dataclass(frozen=True)
class FolderAudit:
    root: str
    source: str
    direct_files: int
    recursive_files: int
    direct_bytes: int
    proposed: str
    status: str
    reason: str


SEXUAL_GROUPS = {
    "Bdsm": {
        "bdsm", "bondage", "bound", "restrained", "cuffs", "leash", "shackles",
        "pet_play", "slave", "body_writing", "humiliation", "spanking",
    },
    "Non-consensual": {
        "forced", "molestation", "rape", "gang_rape", "imminent_rape", "defeated",
    },
    "Oral": {"fellatio", "cum_in_mouth", "cum_on_face", "glory_hole", "penis_on_face"},
    "Penetration": {
        "anal_beads", "anal_fingering", "anal_fisting", "anal_object_insertion",
        "anal_tail", "butt_plug", "gaping", "pegging", "all_fours", "squatting",
    },
    "Sextoys": {"sex_toy"},
}

EXACT_DESTINATIONS = {
    "chastity_cage": "Sexual themes/chastity_cage",
    "flat_chastity_cage": "Sexual themes/flat_chastity_cage",
    "condom_belt": "Sexual themes/condom_belt",
    "erection_under_clothes": "Sexual themes/erection_under_clothes",
    "femdom": "Sexual themes/femdom",
    "femdom futanari": "Sexual themes/femdom futanari",
    "yaoi": "Sexual themes/yaoi",
    "prostitution": "Professions/prostitution",
    "public_use": "Sexual themes/Exposure and public/public_use",
}

# Ces dossiers restent à la racine, comme dans la galerie Tags de référence.
ROOT_LEVEL = {
    "trap", "crossdressing", "androgynous", "und", "gynomorph", "pregnant",
    "bestiality", "interracial & interspecies",
}


def media_files(path: Path, recursive: bool = False) -> list[Path]:
    iterator = path.rglob("*") if recursive else path.iterdir()
    return [item for item in iterator if item.is_file() and item.suffix.casefold() in MEDIA_EXTENSIONS]


def classify(relative: Path) -> tuple[str, str, str]:
    parts = relative.parts
    if not parts:
        return ".", "keep", "racine de la galerie"
    if parts[0] in {"Sexual themes", "Styles vestimentaires", "Animal ears", "Hairstyles", "Piercings", "Races", "Weapons", "Relations"}:
        return relative.as_posix(), "keep", "déjà sous une famille structurée"

    name = parts[0]
    tokens = set(name.split())
    if name in EXACT_DESTINATIONS:
        suffix = Path(*parts[1:]) if len(parts) > 1 else None
        destination = Path(EXACT_DESTINATIONS[name])
        if suffix:
            destination /= suffix
        return destination.as_posix(), "move", "parité directe avec la galerie Tags"
    if name in ROOT_LEVEL:
        return relative.as_posix(), "keep", "reste à la racine comme dans la galerie Tags"
    if tokens.intersection({"rape", "forced", "molestation", "gang_rape", "imminent_rape"}):
        destination = Path("Sexual themes") / "Non-consensual" / relative
        return destination.as_posix(), "move", "requête composite non consentie"
    for group, names in SEXUAL_GROUPS.items():
        if name in names or tokens.intersection(names):
            destination = Path("Sexual themes") / group / relative
            return destination.as_posix(), "move", f"pratique sexuelle classée dans {group}"
    return relative.as_posix(), "review", "aucune correspondance sûre dans les familles actuelles"


def audit_folders() -> list[FolderAudit]:
    rows: list[FolderAudit] = []
    for root in ROOTS:
        for folder in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: p.as_posix().casefold()):
            relative = folder.relative_to(root)
            direct = media_files(folder)
            recursive = media_files(folder, recursive=True)
            proposed, status, reason = classify(relative)
            rows.append(
                FolderAudit(
                    root=root.name,
                    source=relative.as_posix(),
                    direct_files=len(direct),
                    recursive_files=len(recursive),
                    direct_bytes=sum(item.stat().st_size for item in direct),
                    proposed=proposed,
                    status=status,
                    reason=reason,
                )
            )
    return rows


def file_inventory() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for root in ROOTS:
        for item in sorted(media_files(root, recursive=True), key=lambda p: p.as_posix().casefold()):
            match = MD5_RE.search(item.name)
            rows.append(
                {
                    "root": root.name,
                    "relative_path": item.relative_to(root).as_posix(),
                    "bytes": item.stat().st_size,
                    "embedded_md5": match.group(1).lower() if match else "",
                }
            )
    return rows


def duplicate_groups(files: list[dict[str, object]]) -> list[dict[str, object]]:
    by_hint: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    for row in files:
        md5 = str(row["embedded_md5"])
        if md5:
            by_hint[(md5, int(row["bytes"]))].append(row)
    candidates = [group for group in by_hint.values() if len(group) > 1]
    verified: list[dict[str, object]] = []
    for group in candidates:
        by_actual: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in group:
            path = RESERVE / str(row["root"]) / Path(str(row["relative_path"]))
            digest = hashlib.md5(path.read_bytes()).hexdigest()
            by_actual[digest].append(row)
        for digest, matches in by_actual.items():
            if len(matches) > 1:
                verified.append({"md5": digest, "bytes": matches[0]["bytes"], "files": matches})
    return verified


def proposed_collisions(folders: list[FolderAudit]) -> list[dict[str, str]]:
    collisions: list[dict[str, str]] = []
    for row in folders:
        if row.status != "move" or not row.direct_files:
            continue
        root = RESERVE / row.root
        source = root / Path(row.source)
        destination = root / Path(row.proposed)
        for item in media_files(source):
            target = destination / item.name
            if target.exists() and target.resolve() != item.resolve():
                collisions.append({
                    "root": row.root,
                    "source": item.relative_to(root).as_posix(),
                    "target": target.relative_to(root).as_posix(),
                })
    return collisions


def monitor_summary() -> dict[str, object]:
    document = json.loads(MONITORS.read_text(encoding="utf-8-sig"))
    monitors = document.get("monitors", [])
    exact_garcons = [m for m in monitors if "garçon" in m.get("filenameOverride", "").casefold()]
    return {
        "count": len(monitors),
        "destinations_into_garcons": len(exact_garcons),
        "neutral_count_tokens": sum("1boy" in m.get("filenameOverride", "") for m in monitors),
    }


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    folders = audit_folders()
    files = file_inventory()
    duplicates = duplicate_groups(files)
    collisions = proposed_collisions(folders)
    monitor = monitor_summary()

    write_csv(
        OUTPUT / "inventaire_dossiers.csv",
        [asdict(row) for row in folders],
        list(FolderAudit.__dataclass_fields__),
    )
    write_csv(OUTPUT / "inventaire_fichiers.csv", files, ["root", "relative_path", "bytes", "embedded_md5"])
    (OUTPUT / "doublons_md5.json").write_text(
        json.dumps(duplicates, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_csv(OUTPUT / "collisions_proposees.csv", collisions, ["root", "source", "target"])

    status_counts = Counter(row.status for row in folders)
    root_stats = {}
    for root in ROOTS:
        selected = [row for row in files if row["root"] == root.name]
        root_stats[root.name] = (len(selected), sum(int(row["bytes"]) for row in selected))
    moves = [row for row in folders if row.status == "move" and row.direct_files]
    reviews = [row for row in folders if row.status == "review" and row.direct_files]

    lines = [
        "# Audit en lecture seule des galeries Garçons",
        "",
        f"Généré le {datetime.now().astimezone().isoformat(timespec='seconds')}.",
        "",
        "## Périmètre",
        "",
    ]
    for name, (count, size) in root_stats.items():
        lines.append(f"- `{name}` : {count} fichiers, {size} octets")
    lines += [
        f"- Moniteurs : {monitor['count']}, dont {monitor['destinations_into_garcons']} destination directe vers Garçons",
        f"- Moniteurs conservant les indicateurs neutres de nombre/sexe : {monitor['neutral_count_tokens']}",
        "",
        "## Résultat des phases 1 et 2",
        "",
        f"- Dossiers inventoriés : {len(folders)}",
        f"- Déplacements de forte confiance : {status_counts['move']}",
        f"- Dossiers déjà structurés : {status_counts['keep']}",
        f"- Dossiers à revoir : {status_counts['review']}",
        f"- Groupes de doublons MD5 vérifiés : {len(duplicates)}",
        f"- Collisions de noms dans les destinations proposées : {len(collisions)}",
        f"- Fichiers concernés par les déplacements proposés : {sum(row.direct_files for row in moves)}",
        f"- Octets concernés par les déplacements proposés : {sum(row.direct_bytes for row in moves)}",
        "",
        "## Phase 3 : déplacements proposés (fichiers directs)",
        "",
        "| Galerie | Source | Destination | Fichiers | Octets |",
        "|---|---|---|---:|---:|",
    ]
    for row in moves:
        lines.append(f"| {row.root} | `{row.source}` | `{row.proposed}` | {row.direct_files} | {row.direct_bytes} |")
    lines += [
        "",
        "## Revue manuelle requise (dossiers contenant directement des fichiers)",
        "",
        "| Galerie | Dossier | Fichiers | Motif |",
        "|---|---|---:|---|",
    ]
    for row in reviews:
        lines.append(f"| {row.root} | `{row.source}` | {row.direct_files} | {row.reason} |")
    lines += [
        "",
        "## Garanties",
        "",
        "- Aucun fichier de la réserve n'a été déplacé, renommé ou supprimé.",
        "- `monitors.json` n'a pas été modifié.",
        "- Les scénarios hétérosexuels et homosexuels restent tous deux admis.",
        "- Les doublons sont seulement signalés ; aucune suppression n'est proposée automatiquement.",
    ]
    (OUTPUT / "RAPPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "folders": len(folders),
        "files": len(files),
        "bytes": sum(int(row["bytes"]) for row in files),
        "statuses": status_counts,
        "verified_duplicate_groups": len(duplicates),
        "proposed_collisions": len(collisions),
        "output": str(OUTPUT.absolute()),
    }, ensure_ascii=False, default=dict, indent=2))


if __name__ == "__main__":
    main()
