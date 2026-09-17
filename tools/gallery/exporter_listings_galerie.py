from __future__ import annotations

import csv
import sqlite3
from collections import defaultdict
from pathlib import Path


RESERVE = Path(r"D:\Réserve d'avatar v4")
DATABASE = Path("g_tags_260712_blacklist.db")
OUTPUT = Path("listes_galerie")

PRIMARY_TREES = ("Tags (Gelbooru)", "Tags (gelbooru) c&l")
SECONDARY_TREES = ("Garçons (Gelbooru)", "Garçons (Gelbooru) s&l")
PRIORITY_GROUPS = (
    "Sexual themes",
    "Weapons",
    "Professions",
    "Races",
    "Relations",
    "Piercings",
    "Animal ears",
)


def safe_name(value: str) -> str:
    return value.lower().replace(" ", "_")


def find_group(tree: Path, wanted: str) -> Path | None:
    wanted_folded = wanted.casefold()
    for child in tree.iterdir():
        if child.is_dir() and child.name.casefold() == wanted_folded:
            return child
    return None


def file_count(folder: Path) -> int:
    return sum(1 for item in folder.iterdir() if item.is_file())


def load_tag_database() -> dict[str, tuple[int, int, int]]:
    connection = sqlite3.connect(f"file:{DATABASE.resolve().as_posix()}?mode=ro", uri=True)
    try:
        return {
            name: (post_count, category, ambiguous)
            for name, post_count, category, ambiguous in connection.execute(
                "SELECT name, post_count, category, ambiguous FROM tags"
            )
        }
    finally:
        connection.close()


def query_tokens(query: str, tags: dict[str, tuple[int, int, int]]) -> tuple[list[str], list[str]]:
    valid: list[str] = []
    unknown: list[str] = []
    for token in query.split():
        if token in tags:
            valid.append(token)
        else:
            unknown.append(token)
    return valid, unknown


def write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def hierarchy_rows(root: Path, tree_name: str, include_root: bool = True) -> list[dict[str, object]]:
    folders = [root, *sorted((p for p in root.rglob("*") if p.is_dir()), key=str)] if include_root else sorted(
        (p for p in root.rglob("*") if p.is_dir()), key=str
    )
    rows: list[dict[str, object]] = []
    for folder in folders:
        relative = folder.relative_to(root)
        path_text = "." if relative == Path(".") else " / ".join(relative.parts)
        parent_text = "" if relative == Path(".") or len(relative.parts) == 1 else " / ".join(relative.parts[:-1])
        children = sorted((item.name for item in folder.iterdir() if item.is_dir()), key=str.casefold)
        rows.append(
            {
                "tree": tree_name,
                "path": path_text,
                "parent": parent_text,
                "name": folder.name,
                "depth": 0 if relative == Path(".") else len(relative.parts),
                "is_parent": bool(children),
                "children": children,
                "files": file_count(folder),
            }
        )
    return rows


def write_hierarchy(destination: Path, rows: list[dict[str, object]], stem: str = "00_hierarchie_complete") -> None:
    with (destination / f"{stem}.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("arbre", "chemin", "parent", "dossier", "profondeur", "est_parent", "enfants_directs", "fichiers_directs"))
        for row in rows:
            writer.writerow(
                (
                    row["tree"], row["path"], row["parent"], row["name"], row["depth"],
                    1 if row["is_parent"] else 0, " | ".join(row["children"]), row["files"],
                )
            )

    text_lines: list[str] = []
    current_tree = None
    for row in rows:
        if row["tree"] != current_tree:
            current_tree = str(row["tree"])
            text_lines.append(f"[{current_tree}]")
        marker = "+" if row["is_parent"] else "-"
        text_lines.append(f"{'  ' * int(row['depth'])}{marker} {row['name']}")
    write_lines(destination / f"{stem}.txt", text_lines)


def collect_group(group_name: str, tags: dict[str, tuple[int, int, int]]) -> dict[str, int]:
    destination = OUTPUT / f"{PRIORITY_GROUPS.index(group_name) + 1:02d}_{safe_name(group_name)}"
    destination.mkdir(parents=True, exist_ok=True)

    queries: list[dict[str, object]] = []
    tag_sources: dict[str, set[str]] = defaultdict(set)
    unknown_sources: dict[str, set[str]] = defaultdict(set)
    hierarchy: list[dict[str, object]] = []

    for tree_name in PRIMARY_TREES + SECONDARY_TREES:
        tree = RESERVE / tree_name
        group = find_group(tree, group_name)
        if group is None:
            continue
        hierarchy.extend(hierarchy_rows(group, tree_name))
        for folder in [group, *sorted((p for p in group.rglob("*") if p.is_dir()), key=str)]:
            count = file_count(folder)
            relative = folder.relative_to(group)
            child_directories = any(item.is_dir() for item in folder.iterdir())
            # The group root and empty intermediate directories are navigation labels,
            # not Grabber queries. A directory with files can be both a query and a parent.
            if relative == Path("."):
                continue
            if count == 0 and child_directories:
                continue
            query = folder.name
            subgroup = " / ".join(relative.parts[:-1])
            valid, unknown = query_tokens(query, tags)
            queries.append(
                {
                    "tree": tree_name,
                    "subgroup": subgroup,
                    "query": query,
                    "files": count,
                    "valid": " ".join(valid),
                    "unknown": " ".join(unknown),
                }
            )
            source = f"{tree_name} :: {relative}"
            for tag in valid:
                tag_sources[tag].add(source)
            for token in unknown:
                unknown_sources[token].add(source)

    unique_queries = sorted({str(row["query"]) for row in queries}, key=str.casefold)
    general_tags = sorted(
        (tag for tag in tag_sources if tags[tag][1] == 0),
        key=lambda tag: (-tags[tag][0], tag),
    )

    write_lines(destination / "01_requetes_existantes.txt", unique_queries)
    write_lines(destination / "02_tags_generaux_existants.txt", general_tags)
    write_hierarchy(destination, hierarchy)

    with (destination / "00_catalogue_existant.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("arbre", "sous_groupe", "requete_dossier", "fichiers_directs", "tags_valides", "elements_non_reconnus"))
        for row in sorted(queries, key=lambda r: (str(r["tree"]).casefold(), str(r["subgroup"]).casefold(), str(r["query"]).casefold())):
            writer.writerow((row["tree"], row["subgroup"], row["query"], row["files"], row["valid"], row["unknown"]))

    with (destination / "03_tags_generaux_details.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("tag", "post_count", "ambiguous", "sources"))
        for tag in general_tags:
            post_count, _category, ambiguous = tags[tag]
            writer.writerow((tag, post_count, ambiguous, " | ".join(sorted(tag_sources[tag], key=str.casefold))))

    with (destination / "04_elements_non_reconnus.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("element", "sources"))
        for token in sorted(unknown_sources, key=str.casefold):
            writer.writerow((token, " | ".join(sorted(unknown_sources[token], key=str.casefold))))

    return {
        "folders": len(queries),
        "queries": len(unique_queries),
        "general_tags": len(general_tags),
        "unknown": len(unknown_sources),
    }


def collect_ungrouped(tags: dict[str, tuple[int, int, int]]) -> dict[str, int]:
    known_groups = {name.casefold() for name in PRIORITY_GROUPS}
    rows: list[tuple[str, str, int, str, str]] = []
    tag_sources: dict[str, set[str]] = defaultdict(set)
    unknown_sources: dict[str, set[str]] = defaultdict(set)

    for tree_name in PRIMARY_TREES:
        tree = RESERVE / tree_name
        for folder in sorted((p for p in tree.iterdir() if p.is_dir()), key=lambda p: p.name.casefold()):
            if folder.name.casefold() in known_groups:
                continue
            # A root folder containing directories is already an existing group.
            # Only root leaves belong in the backlog of genuinely ungrouped queries.
            if any(item.is_dir() for item in folder.iterdir()):
                continue
            valid, unknown = query_tokens(folder.name, tags)
            rows.append((tree_name, folder.name, file_count(folder), " ".join(valid), " ".join(unknown)))
            source = f"{tree_name} :: {folder.name}"
            for tag in valid:
                if tags[tag][1] == 0:
                    tag_sources[tag].add(source)
            for token in unknown:
                unknown_sources[token].add(source)

    destination = OUTPUT / "00_non_groupes_racine"
    destination.mkdir(parents=True, exist_ok=True)
    write_lines(destination / "01_requetes_non_groupees.txt", sorted({row[1] for row in rows}, key=str.casefold))
    general_tags = sorted(tag_sources, key=lambda tag: (-tags[tag][0], tag))
    write_lines(destination / "02_tags_generaux_non_groupes.txt", general_tags)

    with (destination / "00_catalogue_non_groupe.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("arbre", "requete_dossier", "fichiers_directs", "tags_valides", "elements_non_reconnus"))
        writer.writerows(rows)

    with (destination / "03_tags_generaux_details.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("tag", "post_count", "ambiguous", "sources"))
        for tag in general_tags:
            post_count, _category, ambiguous = tags[tag]
            writer.writerow((tag, post_count, ambiguous, " | ".join(sorted(tag_sources[tag], key=str.casefold))))

    with (destination / "04_elements_non_reconnus.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("element", "sources"))
        for token in sorted(unknown_sources, key=str.casefold):
            writer.writerow((token, " | ".join(sorted(unknown_sources[token], key=str.casefold))))

    return {
        "folders": len(rows),
        "queries": len({row[1] for row in rows}),
        "general_tags": len(general_tags),
        "unknown": len(unknown_sources),
    }


def export_global_hierarchy() -> None:
    rows: list[dict[str, object]] = []
    for tree_name in PRIMARY_TREES + SECONDARY_TREES:
        tree = RESERVE / tree_name
        rows.extend(hierarchy_rows(tree, tree_name))
    write_hierarchy(OUTPUT, rows, "HIERARCHIE_GELBOORU_COMPLETE")


def main() -> None:
    if not RESERVE.is_dir():
        raise SystemExit(f"Réserve introuvable : {RESERVE}")
    if not DATABASE.is_file():
        raise SystemExit(f"Base introuvable : {DATABASE}")

    OUTPUT.mkdir(exist_ok=True)
    tags = load_tag_database()
    export_global_hierarchy()
    summaries = [("Non groupés", collect_ungrouped(tags))]
    for group in PRIORITY_GROUPS:
        summaries.append((group, collect_group(group, tags)))

    with (OUTPUT / "RESUME.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("groupe", "dossiers_sources", "requetes_uniques", "tags_generaux_uniques", "elements_non_reconnus"))
        for group, summary in summaries:
            writer.writerow((group, summary["folders"], summary["queries"], summary["general_tags"], summary["unknown"]))

    for group, summary in summaries:
        print(
            f"{group}: {summary['folders']} dossiers, {summary['queries']} requêtes, "
            f"{summary['general_tags']} tags généraux, {summary['unknown']} éléments non reconnus"
        )


if __name__ == "__main__":
    main()
