from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path


DATABASE = Path("g_tags_260712_blacklist.db")
TAXONOMY = Path("tag_organization.json")
SEXUAL_OUTPUT = Path("listes_galerie/01_sexual_themes")

# Conversion entre les grandes branches de la taxonomie importée et les
# groupes de navigation déjà présents dans la Réserve.
SEXUAL_PARENT_MAP = {
    "sex": "Sex acts",
    "sex_acts": "Sex acts",
    "frottage": "Frottage",
    "groping": "Groping",
    "hairjob": "Hairjob",
    "handjob": "Handjob",
    "masturbation": "Masturbation",
    "group_sex": "Group sex",
    "Group_Sex": "Group sex",
    "Same-sex_Acts": "Same-sex acts",
    "Fetishes": "Fetishes",
    "sexual_positions": "Positions",
    "bdsm": "Bdsm",
    "Bondage_and_Discipline": "Bdsm",
    "rape": "Non-consensual",
    "Rape": "Non-consensual",
    "femdom-rape": "Non-consensual",
    "oral": "Oral",
    "Penetration_and_Insertion": "Penetration",
    "object_insertion": "Penetration",
    "large_insertion": "Penetration",
    "food_insertion": "Penetration",
    "animal_insertion": "Penetration",
    "multiple_insertions": "Penetration",
    "urethral_insertion": "Penetration",
    "cervical_penetration": "Penetration",
    "nipple_penetration": "Penetration",
    "fingering": "Penetration",
    "fisting": "Penetration",
    "sex_objects": "Sextoys",
    "exhibitionism": "Exposure and public",
    "cum": "Ejaculation and semen",
    "Cum_Play": "Ejaculation and semen",
    "tentacles": "Tentacles",
}


def load_general_tags() -> dict[str, tuple[int, int]]:
    connection = sqlite3.connect(f"file:{DATABASE.resolve().as_posix()}?mode=ro", uri=True)
    try:
        return {
            name: (post_count, ambiguous)
            for name, post_count, ambiguous in connection.execute(
                "SELECT name, post_count, ambiguous FROM tags WHERE category = 0"
            )
        }
    finally:
        connection.close()


def taxonomy_tags(node: object, path: tuple[str, ...] = ()) -> list[tuple[str, tuple[str, ...]]]:
    found: list[tuple[str, tuple[str, ...]]] = []
    if not isinstance(node, dict):
        return found
    tag = node.get("__tag__")
    if isinstance(tag, str):
        found.append((tag, path))
    for key, value in node.items():
        if not key.startswith("__"):
            found.extend(taxonomy_tags(value, path + (key,)))
    return found


def clean_path(parts: tuple[str, ...], tag: str) -> tuple[str, ...]:
    cleaned = list(parts)
    if cleaned and cleaned[-1].casefold() == tag.casefold():
        cleaned.pop()
    return tuple(cleaned)


def proposed_path(source_path: tuple[str, ...], tag: str) -> tuple[str, ...]:
    cleaned = clean_path(source_path, tag)
    if not cleaned:
        parent = SEXUAL_PARENT_MAP.get(tag, tag.replace("_", " "))
        return (parent,)
    first = cleaned[0]
    parent = SEXUAL_PARENT_MAP.get(first, first.replace("_", " "))
    remainder = tuple(part.replace("_", " ") for part in cleaned[1:])
    # Avoid duplicating equivalent source and destination group labels.
    if remainder and remainder[0].casefold() == parent.casefold():
        remainder = remainder[1:]
    return (parent, *remainder)


def write_tree(path: Path, rows: list[dict[str, object]]) -> None:
    tree: dict[str, object] = {}
    for row in rows:
        node = tree
        for part in row["proposed_path"]:
            node = node.setdefault(part, {})  # type: ignore[assignment]
        node.setdefault("__tags__", []).append(row["tag"])  # type: ignore[union-attr]

    lines = ["+ Sexual themes"]

    def emit(node: dict[str, object], depth: int) -> None:
        for name in sorted((key for key in node if key != "__tags__"), key=str.casefold):
            lines.append(f"{'  ' * depth}+ {name}")
            emit(node[name], depth + 1)  # type: ignore[arg-type]
        for tag in sorted(node.get("__tags__", []), key=str.casefold):  # type: ignore[arg-type]
            lines.append(f"{'  ' * depth}- {tag}")

    emit(tree, 1)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def enrich_sexual_themes(tags: dict[str, tuple[int, int]], document: dict[str, object]) -> None:
    sexual = document["boards"]["gelbooru"]["Visual characteristics"]["sex"]
    existing = {
        line.strip()
        for line in (SEXUAL_OUTPUT / "02_tags_generaux_existants.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }

    paths_by_tag: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for tag, source_path in taxonomy_tags(sexual):
        if tag in tags:
            paths_by_tag[tag].add(source_path)

    rows: list[dict[str, object]] = []
    for tag, source_paths in paths_by_tag.items():
        best_source = min(source_paths, key=lambda value: (len(value), tuple(part.casefold() for part in value)))
        rows.append(
            {
                "tag": tag,
                "post_count": tags[tag][0],
                "ambiguous": tags[tag][1],
                "existing": tag in existing,
                "source_paths": source_paths,
                "proposed_path": proposed_path(best_source, tag),
            }
        )

    # Preserve existing gallery tags even when the imported taxonomy does not
    # contain them. Their current parent is recovered from the source catalogue.
    existing_parents: dict[str, set[str]] = defaultdict(set)
    with (SEXUAL_OUTPUT / "00_catalogue_existant.tsv").open("r", encoding="utf-8", newline="") as handle:
        for source_row in csv.DictReader(handle, delimiter="\t"):
            subgroup = source_row["sous_groupe"].split(" / ")[0] if source_row["sous_groupe"] else "Autres thèmes sexuels"
            for tag in source_row["tags_valides"].split():
                if tag in existing:
                    existing_parents[tag].add(subgroup)
    covered = {str(row["tag"]) for row in rows}
    for tag in sorted(existing - covered):
        parents = existing_parents.get(tag) or {"Autres thèmes sexuels"}
        parent = sorted(parents, key=str.casefold)[0]
        rows.append(
            {
                "tag": tag,
                "post_count": tags[tag][0],
                "ambiguous": tags[tag][1],
                "existing": True,
                "source_paths": {("Réserve", parent)},
                "proposed_path": (parent,),
            }
        )
    rows.sort(key=lambda row: (tuple(str(p).casefold() for p in row["proposed_path"]), -int(row["post_count"]), str(row["tag"])))

    with (SEXUAL_OUTPUT / "05_catalogue_enrichi.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("parent_propose", "tag", "post_count", "ambiguous", "deja_present", "chemins_taxonomiques"))
        for row in rows:
            writer.writerow(
                (
                    " / ".join(row["proposed_path"]), row["tag"], row["post_count"], row["ambiguous"],
                    1 if row["existing"] else 0,
                    " | ".join(" / ".join(path) for path in sorted(row["source_paths"])),
                )
            )

    complements = sorted(
        (row for row in rows if not row["existing"]),
        key=lambda row: (-int(row["post_count"]), str(row["tag"])),
    )
    (SEXUAL_OUTPUT / "06_tags_complementaires.txt").write_text(
        "".join(f"{row['tag']}\n" for row in complements), encoding="utf-8"
    )
    with (SEXUAL_OUTPUT / "06_tags_complementaires_par_parent.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("parent_propose", "tag", "post_count", "ambiguous"))
        for row in complements:
            writer.writerow((" / ".join(row["proposed_path"]), row["tag"], row["post_count"], row["ambiguous"]))

    write_tree(SEXUAL_OUTPUT / "07_hierarchie_enrichie_proposee.txt", rows)
    print(
        f"Sexual themes: {len(rows)} tags généraux validés, "
        f"{sum(1 for row in rows if row['existing'])} déjà présents, {len(complements)} compléments"
    )


def main() -> None:
    tags = load_general_tags()
    document = json.loads(TAXONOMY.read_text(encoding="utf-8"))
    enrich_sexual_themes(tags, document)


if __name__ == "__main__":
    main()
