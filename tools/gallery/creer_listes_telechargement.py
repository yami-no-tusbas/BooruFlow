from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from tools.gallery.exporter_listings_galerie import DATABASE, PRIMARY_TREES, RESERVE, SECONDARY_TREES


# Kept local instead of importing the enrichment script, so this utility can
# remain focused on flat review/download lists.
TAXONOMY_FILE = Path("tag_organization.json")
OUTPUT = Path("listes_telechargement")
WEAPON_CATALOGUE = Path(
    r"C:\Users\Yami\Documents\Codex\2026-07-26\referenced-chatgpt-conversation-this-is-untrusted"
    r"\outputs\catalogue_armes.tsv"
)

# Compléments exacts vérifiés directement dans la base locale lorsque la
# taxonomie importée est incomplète. Les homonymes lexicaux sont volontairement
# exclus et les tags sans aucun post ne sont pas exportés.
DIRECT_DATABASE_ADDITIONS = {
    "eggs_and_insertion": {
        "egg_insertion",
        "egg_laying",
        "implied_egg_laying",
        "imminent_egg_laying",
        "laying_eggs",
        "egg_unlaying",
        "egg_in_pussy",
        "ovipositor",
        "anal_oviposition",
        "oral_oviposition",
        "vaginal_oviposition",
        "creature_insertion",
    },
}

DIRECT_SOCIAL_RELATIONS = {
    "siblings", "couple", "sisters", "twins", "brother_and_sister", "mother_and_daughter",
    "brothers", "father_and_child", "mother_and_son", "father_and_daughter", "husband_and_wife",
    "father_and_son", "cousins", "wife_and_wife", "wife_and_husband", "half-siblings",
    "adoptive_siblings", "triplets", "step-siblings", "grandmother_and_granddaughter",
    "grandfather_and_granddaughter", "grandfather_and_grandson", "husband_and_husband",
    "grandmother_and_grandson", "husband_and_wives", "stepmother_and_stepson",
    "brother_and_step-sister", "mother_and_baby", "stepsiblings", "brothers-in-law",
    "stepfather_and_stepdaughter", "sisters-in-law", "mother_and_father", "mother_and_daughters",
    "fraternal_twins", "parent_and_daughter", "great-grandmother_and_great-granddaughter",
    "twin_sisters", "brother_and_sister-in-law", "biological_siblings", "blood-related_siblings",
    "brother_and_sister_(lore)", "cousins_(lore)", "daughter_and_mother", "grandfather",
    "parent_and_offspring", "stepsisters", "stepmother_and_stepdaughter", "half_siblings",
    "father_and_children", "great-grandfather_and_great-granddaughter",
    "great-grandfather_and_great-grandson", "husband_and_spouse", "mother_and_babies",
    "parent_and_son", "sister_and_sister", "step_brothers", "wife_and_daughter",
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


def existing_gallery_tags(valid_tags: set[str]) -> dict[str, set[str]]:
    sources: dict[str, set[str]] = defaultdict(set)
    for tree_name in PRIMARY_TREES + SECONDARY_TREES:
        tree = RESERVE / tree_name
        for folder in (item for item in tree.rglob("*") if item.is_dir()):
            for token in folder.name.split():
                if token in valid_tags:
                    sources[token].add(f"{tree_name} :: {folder.relative_to(tree)}")
    return sources


def collect_taxonomy_tags(node: object, accepted_path_words: set[str], path: tuple[str, ...] = ()) -> dict[str, set[str]]:
    found: dict[str, set[str]] = defaultdict(set)
    if not isinstance(node, dict):
        return found
    folded_path = {part.casefold().replace("_", " ") for part in path}
    tag = node.get("__tag__")
    if isinstance(tag, str) and folded_path.intersection(accepted_path_words):
        found[tag].add(" / ".join(path))
    for key, value in node.items():
        if key.startswith("__"):
            continue
        for child_tag, child_paths in collect_taxonomy_tags(value, accepted_path_words, path + (key,)).items():
            found[child_tag].update(child_paths)
    return found


def collect_all_taxonomy_tags(node: object, path: tuple[str, ...] = ()) -> dict[str, set[tuple[str, ...]]]:
    found: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    if not isinstance(node, dict):
        return found
    tag = node.get("__tag__")
    if isinstance(tag, str):
        found[tag].add(path)
    for key, value in node.items():
        if key.startswith("__"):
            continue
        for child_tag, child_paths in collect_all_taxonomy_tags(value, path + (key,)).items():
            found[child_tag].update(child_paths)
    return found


def collect_empty_leaf_tags(
    node: object,
    valid_tags: set[str],
    path: tuple[str, ...] = (),
) -> dict[str, set[tuple[str, ...]]]:
    """Collect taxonomies whose canonical tags are represented by empty leaves."""
    found: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    if not isinstance(node, dict):
        return found
    for key, value in node.items():
        if key.startswith("__"):
            continue
        child_path = path + (key,)
        if isinstance(value, dict) and not value and key in valid_tags:
            found[key].add(child_path)
        else:
            for tag, paths in collect_empty_leaf_tags(value, valid_tags, child_path).items():
                found[tag].update(paths)
    return found


def classify_sexual_tag(paths: set[tuple[str, ...]]) -> str:
    words = {
        part.casefold().replace("_", " ")
        for path in paths
        for part in path
    }
    if words.intersection({"bdsm", "bondage", "bondage and discipline", "bondage gear", "bondage-specific"}):
        return "bdsm"
    if words.intersection({"rape", "femdom-rape", "non-consensual"}):
        return "non-consensual"
    if words.intersection({"sex objects", "sex toys"}):
        return "sextoys"
    if "exhibitionism" in words or "exposure" in words:
        return "exposure_and_public"
    if "oral" in words:
        return "oral"
    if words.intersection(
        {
            "penetration and insertion", "object insertion", "large insertion", "food insertion",
            "animal insertion", "multiple insertions", "urethral insertion", "cervical penetration",
            "nipple penetration", "fingering", "fisting",
        }
    ):
        return "penetration"
    if "sexual positions" in words:
        return "positions"
    if words.intersection({"cum", "cum play", "ejaculation"}):
        return "ejaculation_and_semen"
    if "tentacles" in words:
        return "tentacles"
    if any("egg" in word for word in words):
        return "eggs_and_insertion"
    return "autres_sexual_themes"


def create_sexual_lists(tags: dict[str, tuple[int, int]], existing: dict[str, set[str]], document: dict[str, object]) -> None:
    sexual = document["boards"]["gelbooru"]["Visual characteristics"]["sex"]
    related = collect_all_taxonomy_tags(sexual)
    by_group: dict[str, list[str]] = defaultdict(list)
    for tag, paths in related.items():
        if tag in tags and tag not in existing:
            by_group[classify_sexual_tag(paths)].append(tag)
    for group, direct_tags in DIRECT_DATABASE_ADDITIONS.items():
        for tag in direct_tags:
            if tag in tags and tags[tag][0] > 0 and tag not in existing and tag not in by_group[group]:
                by_group[group].append(tag)
                related.setdefault(tag, set()).add(("Ajout direct depuis la base Gelbooru", group))
    for candidates in by_group.values():
        candidates.sort(key=lambda tag: (-tags[tag][0], tag))

    preferred_order = (
        "bdsm", "sextoys", "non-consensual", "exposure_and_public", "oral", "penetration",
        "positions", "ejaculation_and_semen", "eggs_and_insertion", "tentacles",
        "autres_sexual_themes",
    )
    destination = OUTPUT / "01_sexual_themes"
    destination.mkdir(parents=True, exist_ok=True)
    for group in preferred_order:
        (destination / f"{group}.txt").write_text(
            "".join(f"{tag}\n" for tag in by_group.get(group, [])), encoding="utf-8"
        )

    all_candidates = sorted(
        {tag for candidates in by_group.values() for tag in candidates},
        key=lambda tag: (-tags[tag][0], tag),
    )
    (destination / "sexual_themes.txt").write_text(
        "".join(f"{tag}\n" for tag in all_candidates), encoding="utf-8"
    )
    with (destination / "sexual_themes_details.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("liste", "tag", "post_count", "ambiguous", "origine_taxonomique"))
        for group in preferred_order:
            for tag in by_group.get(group, []):
                writer.writerow(
                    (group, tag, tags[tag][0], tags[tag][1], " | ".join(" / ".join(path) for path in sorted(related[tag])))
                )

    all_valid_related = {tag for tag in related if tag in tags}
    excluded = sorted(all_valid_related.intersection(existing), key=lambda tag: (-tags[tag][0], tag))
    with (destination / "sexual_themes_deja_dans_galerie.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("tag", "post_count", "dossiers_existants"))
        for tag in excluded:
            writer.writerow((tag, tags[tag][0], " | ".join(sorted(existing[tag], key=str.casefold))))

    print(
        f"Sexual themes: {len(all_valid_related)} tags liés validés, "
        f"{len(excluded)} déjà dans la galerie, {len(all_candidates)} à examiner"
    )
    for group in preferred_order:
        print(f"  {group}: {len(by_group.get(group, []))}")


def create_weapon_lists(tags: dict[str, tuple[int, int]], existing: dict[str, set[str]]) -> None:
    if not WEAPON_CATALOGUE.is_file():
        raise FileNotFoundError(f"Catalogue historique introuvable : {WEAPON_CATALOGUE}")
    by_group: dict[str, list[str]] = defaultdict(list)
    original_rows = list(csv.DictReader(WEAPON_CATALOGUE.open("r", encoding="utf-8", newline=""), delimiter="\t"))
    for row in original_rows:
        tag = row["tag"]
        if tag in tags and tag not in existing:
            by_group[row["categorie"]].append(tag)
    for candidates in by_group.values():
        candidates.sort(key=lambda tag: (-tags[tag][0], tag))

    destination = OUTPUT / "02_weapons"
    destination.mkdir(parents=True, exist_ok=True)
    for group in sorted(by_group, key=str.casefold):
        (destination / f"{group}.txt").write_text(
            "".join(f"{tag}\n" for tag in by_group[group]), encoding="utf-8"
        )
    all_candidates = sorted(
        {tag for candidates in by_group.values() for tag in candidates},
        key=lambda tag: (-tags[tag][0], tag),
    )
    (destination / "weapons.txt").write_text(
        "".join(f"{tag}\n" for tag in all_candidates), encoding="utf-8"
    )
    with (destination / "weapons_details.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("liste", "tag", "post_count", "ambiguous"))
        for group in sorted(by_group, key=str.casefold):
            for tag in by_group[group]:
                writer.writerow((group, tag, tags[tag][0], tags[tag][1]))

    catalogued = {row["tag"] for row in original_rows}
    already_present = catalogued.intersection(existing)
    missing_from_database = catalogued.difference(tags)
    print(
        f"Weapons: {len(catalogued)} tags catalogués, {len(already_present)} déjà dans la galerie, "
        f"{len(missing_from_database)} absents de la base courante, {len(all_candidates)} à examiner"
    )
    for group in sorted(by_group, key=str.casefold):
        print(f"  {group}: {len(by_group[group])}")


def create_direct_database_list(
    folder_name: str,
    list_name: str,
    tags: dict[str, tuple[int, int]],
    existing: dict[str, set[str]],
    predicate,
) -> None:
    candidates = sorted(
        (
            tag for tag, (post_count, _ambiguous) in tags.items()
            if post_count > 0 and tag not in existing and predicate(tag)
        ),
        key=lambda tag: (-tags[tag][0], tag),
    )
    destination = OUTPUT / folder_name
    destination.mkdir(parents=True, exist_ok=True)
    (destination / f"{list_name}.txt").write_text(
        "".join(f"{tag}\n" for tag in candidates), encoding="utf-8"
    )
    with (destination / f"{list_name}_details.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("tag", "post_count", "ambiguous"))
        for tag in candidates:
            writer.writerow((tag, tags[tag][0], tags[tag][1]))
    print(f"{list_name}: {len(candidates)} tags absents de la galerie à examiner")


def create_grouped_direct_lists(
    folder_name: str,
    parent_name: str,
    groups: dict[str, object],
    tags: dict[str, tuple[int, int]],
    existing: dict[str, set[str]],
) -> None:
    destination = OUTPUT / folder_name
    destination.mkdir(parents=True, exist_ok=True)
    by_group: dict[str, list[str]] = {}
    for group, predicate in groups.items():
        candidates = sorted(
            (
                tag for tag, (post_count, _ambiguous) in tags.items()
                if post_count > 0 and tag not in existing and predicate(tag)
            ),
            key=lambda tag: (-tags[tag][0], tag),
        )
        by_group[group] = candidates
        (destination / f"{group}.txt").write_text(
            "".join(f"{tag}\n" for tag in candidates), encoding="utf-8"
        )
    all_candidates = sorted(
        {tag for candidates in by_group.values() for tag in candidates},
        key=lambda tag: (-tags[tag][0], tag),
    )
    (destination / f"{parent_name}.txt").write_text(
        "".join(f"{tag}\n" for tag in all_candidates), encoding="utf-8"
    )
    with (destination / f"{parent_name}_details.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("liste", "tag", "post_count", "ambiguous"))
        for group, candidates in by_group.items():
            for tag in candidates:
                writer.writerow((group, tag, tags[tag][0], tags[tag][1]))
    print(f"{parent_name}: {len(all_candidates)} tags uniques absents de la galerie")
    for group, candidates in by_group.items():
        print(f"  {group}: {len(candidates)}")


def create_vehicle_lists(
    tags: dict[str, tuple[int, int]],
    existing: dict[str, set[str]],
    document: dict[str, object],
) -> None:
    vehicles = document["boards"]["gelbooru"]["More"]["Tag_group:Technology"]["Vehicles"]
    all_taxonomy = collect_empty_leaf_tags(vehicles, set(tags))
    type_branch = vehicles["1 - By vehicle type"]
    groups: dict[str, list[str]] = {}
    for source_name, output_name in (("Land", "ground"), ("Air", "air"), ("Water", "water"), ("Space", "space")):
        source_tags = collect_empty_leaf_tags(type_branch[source_name], set(tags))
        groups[output_name] = sorted(
            (tag for tag in source_tags if tag not in existing),
            key=lambda tag: (-tags[tag][0], tag),
        )
    fictional = {
        tag for tag, paths in all_taxonomy.items()
        if any("fictional" in part.casefold() for path in paths for part in path)
    }
    groups["fictional"] = sorted(
        (tag for tag in fictional if tag not in existing),
        key=lambda tag: (-tags[tag][0], tag),
    )
    groups["usage_and_attributes"] = sorted(
        (
            tag for tag, paths in all_taxonomy.items()
            if tag not in existing
            and any(path and path[0] in {"2 - By usage", "4 - By attribute", "5 - Image description"} for path in paths)
        ),
        key=lambda tag: (-tags[tag][0], tag),
    )

    destination = OUTPUT / "10_vehicles"
    destination.mkdir(parents=True, exist_ok=True)
    for group, candidates in groups.items():
        (destination / f"{group}.txt").write_text("".join(f"{tag}\n" for tag in candidates), encoding="utf-8")
    parent = sorted(
        (tag for tag in all_taxonomy if tag not in existing),
        key=lambda tag: (-tags[tag][0], tag),
    )
    (destination / "vehicles.txt").write_text("".join(f"{tag}\n" for tag in parent), encoding="utf-8")
    with (destination / "vehicles_details.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("tag", "post_count", "ambiguous", "chemins_taxonomiques"))
        for tag in parent:
            writer.writerow((tag, tags[tag][0], tags[tag][1], " | ".join(" / ".join(path) for path in sorted(all_taxonomy[tag]))))
    print(f"vehicles: {len(parent)} tags absents de la galerie")
    for group, candidates in groups.items():
        print(f"  {group}: {len(candidates)}")


def create_taxonomy_parent_list(
    folder_name: str,
    parent_name: str,
    nodes: list[object],
    tags: dict[str, tuple[int, int]],
    existing: dict[str, set[str]],
    path_filter=None,
) -> None:
    related: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for node in nodes:
        for tag, paths in collect_all_taxonomy_tags(node).items():
            related[tag].update(paths)
        for tag, paths in collect_empty_leaf_tags(node, set(tags)).items():
            related[tag].update(paths)
    canonical = {tag.casefold(): tag for tag in tags}
    normalized_related: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for tag, paths in related.items():
        resolved = canonical.get(tag.casefold())
        if resolved is not None:
            normalized_related[resolved].update(paths)
    related = normalized_related
    candidates = sorted(
        (
            tag for tag, paths in related.items()
            if tag in tags and tag not in existing
            and (path_filter is None or path_filter(paths))
        ),
        key=lambda tag: (-tags[tag][0], tag),
    )
    destination = OUTPUT / folder_name
    destination.mkdir(parents=True, exist_ok=True)
    (destination / f"{parent_name}.txt").write_text(
        "".join(f"{tag}\n" for tag in candidates), encoding="utf-8"
    )
    with (destination / f"{parent_name}_details.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("tag", "post_count", "ambiguous", "chemins_taxonomiques"))
        for tag in candidates:
            writer.writerow((tag, tags[tag][0], tags[tag][1], " | ".join(" / ".join(path) for path in sorted(related[tag]))))
    print(f"{parent_name}: {len(candidates)} tags taxonomiques absents de la galerie")


def create_taxonomy_child_lists(
    folder_name: str,
    parent_name: str,
    node: dict[str, object],
    tags: dict[str, tuple[int, int]],
    existing: dict[str, set[str]],
    tag_filter=None,
) -> None:
    destination = OUTPUT / folder_name
    destination.mkdir(parents=True, exist_ok=True)
    by_group: dict[str, list[str]] = {}
    all_related: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    canonical = {tag.casefold(): tag for tag in tags}
    for child_name, child in node.items():
        if child_name.startswith("__") or not isinstance(child, dict):
            continue
        related = collect_all_taxonomy_tags(child)
        for tag, paths in collect_empty_leaf_tags(child, set(tags)).items():
            related[tag].update(paths)
        normalized_related: dict[str, set[tuple[str, ...]]] = defaultdict(set)
        for tag, paths in related.items():
            resolved = canonical.get(tag.casefold())
            if resolved is not None:
                normalized_related[resolved].update(paths)
        related = normalized_related
        candidates = sorted(
            (
                tag for tag in related
                if tag in tags and tag not in existing and (tag_filter is None or tag_filter(tag))
            ),
            key=lambda tag: (-tags[tag][0], tag),
        )
        safe_group = child_name.casefold().replace(" ", "_").replace("/", "_").replace(":", "_")
        by_group[safe_group] = candidates
        (destination / f"{safe_group}.txt").write_text("".join(f"{tag}\n" for tag in candidates), encoding="utf-8")
        for tag, paths in related.items():
            all_related[tag].update({(child_name, *path) for path in paths})
    parent = sorted(
        {tag for candidates in by_group.values() for tag in candidates},
        key=lambda tag: (-tags[tag][0], tag),
    )
    (destination / f"{parent_name}.txt").write_text("".join(f"{tag}\n" for tag in parent), encoding="utf-8")
    with (destination / f"{parent_name}_details.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("tag", "post_count", "ambiguous", "chemins_taxonomiques"))
        for tag in parent:
            writer.writerow((tag, tags[tag][0], tags[tag][1], " | ".join(" / ".join(path) for path in sorted(all_related[tag]))))
    print(f"{parent_name}: {len(parent)} tags absents de la galerie")


def supplement_parent_list(
    path: Path,
    details_path: Path,
    additions: set[str],
    tags: dict[str, tuple[int, int]],
    existing: dict[str, set[str]],
) -> None:
    current = {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    accepted = {
        tag for tag in additions
        if tag in tags and tags[tag][0] > 0 and tag not in existing
    }
    merged = sorted(current | accepted, key=lambda tag: (-tags[tag][0], tag))
    path.write_text("".join(f"{tag}\n" for tag in merged), encoding="utf-8")
    with details_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("tag", "post_count", "ambiguous", "origine"))
        for tag in merged:
            writer.writerow((tag, tags[tag][0], tags[tag][1], "ajout_direct_base" if tag in accepted else "taxonomie"))
    print(f"{path.stem} enrichi: {len(merged)} tags, dont {len(accepted - current)} ajouts directs")


def main() -> None:
    tags = load_general_tags()
    existing = existing_gallery_tags(set(tags))
    document = json.loads(TAXONOMY_FILE.read_text(encoding="utf-8"))
    create_sexual_lists(tags, existing, document)
    create_weapon_lists(tags, existing)
    create_direct_database_list(
        "06_piercings",
        "piercings",
        tags,
        existing,
        lambda tag: tag == "piercing" or "piercing" in tag,
    )
    create_grouped_direct_lists(
        "08_body_modifications",
        "body_modifications",
        {
            "piercings": lambda tag: tag == "piercing" or "piercing" in tag,
            "tattoos": lambda tag: tag == "tattoo" or "tattoo" in tag,
            "scarification_and_branding": lambda tag: tag == "scarification" or tag in {
                "branding", "branding_iron", "imminent_branding", "holding_branding_iron",
            },
            "implants": lambda tag: tag in {
                "breast_implants", "ocular_implant", "implant", "gold_implants",
                "cochlear_implant", "subdermal_port",
            },
            "tongue_and_ear_modifications": lambda tag: tag in {
                "split_tongue", "stretched_ears", "ear_gauge",
            },
            "temporary_body_art": lambda tag: tag in {
                "body_writing", "body_paint", "henna", "nail_art",
            },
        },
        tags,
        existing,
    )
    create_vehicle_lists(tags, existing, document)
    create_direct_database_list(
        "11_mechas",
        "mechas",
        tags,
        existing,
        lambda tag: (
            tag in {"mecha", "mech", "powered_armor", "exoskeleton", "mobile_suit", "giant_robot", "piloted_robot"}
            or tag.startswith("mecha_") or tag.endswith("_mecha")
            or tag.startswith("mech_") or tag.endswith("_mech")
        ),
    )
    gelbooru = document["boards"]["gelbooru"]
    attire = gelbooru["Visual characteristics"]["Attire and body accessories"]["Attire"]
    create_taxonomy_parent_list("12_clothing", "clothing", [attire], tags, existing)
    animals = gelbooru["Creatures"]["animals"]
    create_taxonomy_parent_list(
        "13_animals",
        "animals",
        [animals],
        tags,
        existing,
        lambda paths: any(
            not any(blocked in part.casefold() for part in path for blocked in ("girl", "people", "attire", "body parts"))
            for path in paths
        ),
    )
    eyewear = attire["Eyewear"]
    jewelry_glasses = attire["Jewelry and Accessories"]["Head and Face"]["glasses"]
    create_taxonomy_parent_list("14_glasses_and_eyewear", "glasses_and_eyewear", [eyewear, jewelry_glasses], tags, existing)
    animal_ears_node = gelbooru["Visual characteristics"]["body"]["body_parts"]["head"]["ears"]["ears"]["animal_ears"]
    create_taxonomy_parent_list("07_animal_ears", "animal_ears", [animal_ears_node], tags, existing)
    races_node = gelbooru["Creatures"]["legendary_creatures"]
    create_taxonomy_child_lists(
        "15_races",
        "races",
        races_node,
        tags,
        existing,
        lambda tag: not tag.endswith("_ears") and tag != "animal_ears",
    )
    family = gelbooru["More"]["Tag_group:Family_relationships"]
    create_taxonomy_child_lists("16_social_and_family_relations", "social_and_family_relations", family, tags, existing)
    supplement_parent_list(
        OUTPUT / "16_social_and_family_relations" / "social_and_family_relations.txt",
        OUTPUT / "16_social_and_family_relations" / "social_and_family_relations_details.tsv",
        DIRECT_SOCIAL_RELATIONS,
        tags,
        existing,
    )
    holidays = gelbooru["Real world"]["Tag_group:Holidays_and_celebrations"]
    create_taxonomy_child_lists("17_holidays", "holidays", holidays, tags, existing)
    technology = gelbooru["More"]["Tag_group:Technology"]
    computer_nodes = [
        technology["Computers, interfaces, gadgets, IT & communication hardware"],
        technology["Computers"],
        gelbooru["Objects"]["Tag_group:Audio_tags"]["Devices that play recorded music"],
    ]
    create_taxonomy_parent_list("18_home_electronics", "home_electronics", computer_nodes, tags, existing)
    audio = gelbooru["Objects"]["Tag_group:Audio_tags"]
    instrument_node = {
        key: audio[key]
        for key in ("Brass", "Percussion", "Strings", "Woodwinds", "Keyboard Instruments", "Other Instruments")
        if key in audio
    }
    create_taxonomy_child_lists("19_musical_instruments", "musical_instruments", instrument_node, tags, existing)
    bathing = gelbooru["More"]["Tag_group:Water"]["Actions"]["Bathing"]
    create_taxonomy_parent_list("20_bathing_and_showering", "bathing_and_showering", [bathing], tags, existing)
    nudity = attire["Nudity"]
    create_taxonomy_child_lists("21_nudity_adjacent", "nudity_adjacent", nudity, tags, existing)
    create_grouped_direct_lists(
        "17_holidays",
        "holidays",
        {
            "christmas": lambda tag: "christmas" in tag,
            "halloween": lambda tag: "halloween" in tag,
            "new_year": lambda tag: "new_year" in tag or tag in {"new_years_eve", "hatsumode"},
            "valentines": lambda tag: "valentine" in tag,
            "easter": lambda tag: "easter" in tag,
            "other_holidays": lambda tag: any(word in tag for word in ("thanksgiving", "tanabata", "obon", "hanami", "oktoberfest")),
        },
        tags,
        existing,
    )
    create_grouped_direct_lists(
        "18_home_electronics",
        "home_electronics",
        {
            "audio": lambda tag: any(word in tag for word in ("headphones", "earphones", "speaker", "microphone", "stereo", "radio")),
            "computers": lambda tag: any(word in tag for word in ("computer", "laptop", "keyboard", "computer_mouse", "monitor_(computer)")),
            "phones_and_tablets": lambda tag: any(word in tag for word in ("smartphone", "cellphone", "mobile_phone", "tablet_computer")),
            "gaming": lambda tag: any(word in tag for word in ("game_console", "game_controller", "handheld_game_console")),
            "photo_and_video": lambda tag: any(word in tag for word in ("camera", "camcorder", "webcam")),
            "home_devices": lambda tag: tag in {"television", "printer", "router", "roomba", "robot_vacuum", "vacuum_cleaner", "smart_speaker"},
        },
        tags,
        existing,
    )
    create_grouped_direct_lists(
        "20_bathing_and_showering",
        "bathing_and_showering",
        {
            "bathing": lambda tag: tag == "bathing" or tag.startswith("bathing_") or tag.endswith("_bathing"),
            "showering": lambda tag: tag == "showering" or tag.startswith("showering_") or tag.endswith("_showering"),
            "bathroom_and_bath": lambda tag: tag in {"bath", "bathtub", "bathroom", "public_bath", "outdoor_bath", "bubble_bath", "hot_spring"},
            "washing_and_drying": lambda tag: tag in {"washing_hair", "washing_body", "drying_hair", "towel_drying", "wet_hair", "wet_clothes"},
        },
        tags,
        existing,
    )
    create_grouped_direct_lists(
        "22_furniture",
        "furniture",
        {
            "seating": lambda tag: tag in {"chair", "armchair", "swivel_chair", "wheelchair", "sofa", "couch", "stool", "bench", "office_chair", "rocking_chair"},
            "tables_and_desks": lambda tag: tag in {"table", "desk", "coffee_table", "dining_table", "nightstand", "school_desk"},
            "storage": lambda tag: tag in {"bookshelf", "bookcase", "shelf", "cabinet", "dresser", "wardrobe", "filing_cabinet"},
            "beds": lambda tag: tag in {"bed", "bunk_bed", "hospital_bed", "canopy_bed", "futon", "crib"},
            "other_furniture": lambda tag: tag in {"furniture", "vanity", "screen", "room_divider", "coat_rack"},
        },
        tags,
        existing,
    )
    create_grouped_direct_lists(
        "23_universes_and_aesthetics",
        "universes_and_aesthetics",
        {
            "science_fiction": lambda tag: "science_fiction" in tag or tag in {"sci-fi", "space_opera"},
            "cyberpunk": lambda tag: "cyberpunk" in tag,
            "steampunk": lambda tag: "steampunk" in tag,
            "other_punk": lambda tag: any(word in tag for word in ("dieselpunk", "biopunk", "solarpunk", "atompunk", "clockpunk")),
            "post_apocalyptic": lambda tag: "post-apocal" in tag or tag in {"apocalypse", "post_apocalypse", "post-apocalyptic"},
            "retro_futurism": lambda tag: "retro_futur" in tag,
        },
        tags,
        existing,
    )
    create_grouped_direct_lists(
        "24_social_styles",
        "social_styles",
        {
            "ganguro_and_gyaru": lambda tag: "ganguro" in tag or "gyaru" in tag,
            "delinquent": lambda tag: "delinquent" in tag or tag in {"sukeban", "bancho"},
            "punk_and_rebel": lambda tag: tag in {"punk", "punk_girl", "rebel", "biker", "rocker"},
        },
        tags,
        existing,
    )
    create_grouped_direct_lists(
        "09_injuries",
        "injuries",
        {
            "general_injuries": lambda tag: (
                tag in {"injury", "injured", "wounded", "wounds", "wound", "implied_injury"}
                or tag.endswith("_injury") or tag.startswith("injured_")
            ),
            "wounds_and_cuts": lambda tag: (
                tag in {"deep_wound", "gunshot_wound", "bullet_wound", "open_wound", "exit_wound", "cuts", "scrape", "scrapes"}
                or tag.endswith("_wound") or tag.startswith("wound_on_") or tag.startswith("scraped_")
            ),
            "bruises": lambda tag: "bruise" in tag and not tag.startswith("fake_"),
            "bleeding_and_blood": lambda tag: (
                tag in {"blood", "bleeding", "bloody_face", "bloody_nose", "bloody_mouth", "bloody_hands", "bloody_clothes"}
                or tag.startswith("blood_on_") or tag.startswith("blood_from_") or tag.startswith("bleeding_from_")
            ),
            "bandages": lambda tag: tag == "bandages" or tag.startswith("bandaged_") or tag.startswith("bandage_on_") or tag.startswith("bandage_over_"),
            "scars": lambda tag: tag == "scar" or tag.startswith("scar_") or tag.endswith("_scar"),
            "burns": lambda tag: tag in {
                "burn", "burns", "burned", "burnt", "burn_scar", "burn_mark", "burned_skin", "cigarette_burn",
            },
            "fractures_and_missing_limbs": lambda tag: tag in {
                "broken_bone", "broken_bones", "fracture", "fractured", "missing_limb", "missing_limbs",
            },
        },
        tags,
        existing,
    )


if __name__ == "__main__":
    main()
