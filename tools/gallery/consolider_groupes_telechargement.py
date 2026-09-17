from __future__ import annotations

import csv
import shutil
from collections import defaultdict
from pathlib import Path

from tools.gallery.creer_listes_telechargement import existing_gallery_tags, load_general_tags


ROOT = Path("listes_telechargement")


def read_tags(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def write_tags(path: Path, values: set[str], metadata: dict[str, tuple[int, int]]) -> None:
    ordered = sorted(values, key=lambda tag: (-metadata.get(tag, (0, 0))[0], tag))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{tag}\n" for tag in ordered), encoding="utf-8")


def replace_children(folder: Path, keep: set[str], groups: dict[str, set[str]], metadata: dict[str, tuple[int, int]]) -> None:
    for path in folder.glob("*.txt"):
        if path.name not in keep:
            path.unlink()
    for name, values in groups.items():
        if values:
            write_tags(folder / f"{name}.txt", values, metadata)


def consolidate_animals(metadata: dict[str, tuple[int, int]]) -> None:
    folder = ROOT / "13_animals"
    parent = read_tags(folder / "animals.txt")
    paths: dict[str, str] = {}
    with (folder / "animals_details.tsv").open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            paths[row["tag"]] = row["chemins_taxonomiques"].casefold()
    groups: dict[str, set[str]] = defaultdict(set)
    for tag in parent:
        path = paths.get(tag, "")
        if any(word in tag for word in ("fish", "shark", "whale", "dolphin", "octopus", "squid", "crab", "jellyfish")):
            groups["aquatic_animals"].add(tag)
        elif any(word in tag for word in ("insect", "spider", "butterfly", "bee", "ant", "beetle", "moth")):
            groups["insects_and_arthropods"].add(tag)
        elif "domestic pets" in path:
            groups["domestic_and_companion_animals"].add(tag)
        elif "farm animals" in path:
            groups["farm_animals"].add(tag)
        elif "birds" in path:
            groups["birds"].add(tag)
        elif "extinct animals" in path:
            groups["extinct_animals"].add(tag)
        else:
            groups["wild_and_other_animals"].add(tag)
    replace_children(folder, {"animals.txt", "extensions_db_a_verifier.txt", "liste_elargie.txt"}, groups, metadata)


def consolidate_races(metadata: dict[str, tuple[int, int]]) -> None:
    folder = ROOT / "15_races"
    parent = read_tags(folder / "races.txt")
    groups = {
        "dragons_and_draconic": {tag for tag in parent if "dragon" in tag or tag in {"drider", "lizard_girl"}},
        "fantasy_humanoids": {tag for tag in parent if any(word in tag for word in ("centaur", "dwarf", "gnome", "goblin", "lamia", "minotaur", "satyr", "troll", "cyclops"))},
        "fairies_and_nature_spirits": {tag for tag in parent if tag in {"pixie", "unicorn", "pegasus", "phoenix", "mushroom_girl"}},
        "undead_and_spirits": {tag for tag in parent if tag in {"ghost", "ghoul", "dullahan", "yuki_onna"}},
        "youkai_and_east_asian": {tag for tag in parent if tag in {"youkai", "kitsune", "tanuki", "tengu", "kappa", "tsuchigumo"}},
        "beast_and_monster_people": {tag for tag in parent if "_girl" in tag or tag in {"harpy", "scylla", "werewolf", "shapeshifter"}},
        "mythical_creatures": {tag for tag in parent if tag in {"chimera", "sphinx", "gargoyle", "hippogriff", "hippocampus", "gryphon"}},
    }
    assigned = set().union(*groups.values())
    groups["other_fantasy_races"] = parent - assigned
    replace_children(folder, {"races.txt", "extensions_db_a_verifier.txt", "liste_elargie.txt"}, groups, metadata)


def consolidate_relations(metadata: dict[str, tuple[int, int]], existing: set[str]) -> None:
    folder = ROOT / "16_social_and_family_relations"
    parent = read_tags(folder / "social_and_family_relations.txt")
    professional = {
        "coworkers", "colleagues", "boss_and_employee", "teacher_and_student", "master_and_servant",
        "doctor_and_patient", "senpai_and_kouhai", "superior_and_subordinate",
    }
    social = {"friends", "friendship", "rivals", "enemies", "classmates", "teammates", "neighbors"}
    parent |= {tag for tag in professional | social if tag in metadata and metadata[tag][0] > 0 and tag not in existing}
    write_tags(folder / "social_and_family_relations.txt", parent, metadata)
    groups = {
        "blood_family": {tag for tag in parent if any(word in tag for word in ("father", "mother", "brother", "sister", "siblings", "daughter", "son", "grand", "aunt", "uncle", "cousin", "twins", "triplets", "quadruplets", "sextuplets", "septuplets")) and "step" not in tag and "adoptive" not in tag},
        "step_and_adoptive_family": {tag for tag in parent if "step" in tag or "adoptive" in tag},
        "couples_and_spouses": {tag for tag in parent if any(word in tag for word in ("husband", "wife", "couple", "spouse"))},
        "professional_and_hierarchical": parent.intersection(professional),
        "friends_rivals_and_peers": parent.intersection(social),
    }
    assigned = set().union(*groups.values())
    groups["other_relationships"] = parent - assigned
    replace_children(folder, {"social_and_family_relations.txt", "extensions_db_a_verifier.txt", "liste_elargie.txt"}, groups, metadata)


def consolidate_holidays(metadata: dict[str, tuple[int, int]]) -> None:
    folder = ROOT / "17_holidays"
    parent = read_tags(folder / "holidays.txt")
    groups = {
        "winter_holidays_and_new_year": {tag for tag in parent if any(word in tag for word in ("christmas", "new_year", "hanukkah"))},
        "romantic_and_spring_holidays": {tag for tag in parent if any(word in tag for word in ("valentine", "easter"))},
        "halloween_and_autumn_events": {tag for tag in parent if any(word in tag for word in ("halloween", "thanksgiving", "oktoberfest"))},
        "asian_festivals": {tag for tag in parent if any(word in tag for word in ("tanabata", "obon", "hanami", "chuseok", "setsu", "tsukimi", "hinamatsuri", "songkran", "dragon_boat", "mid-autumn"))},
    }
    assigned = set().union(*groups.values())
    groups["other_holidays_and_celebrations"] = parent - assigned
    replace_children(folder, {"holidays.txt", "extensions_db_a_verifier.txt", "liste_elargie.txt"}, groups, metadata)


def consolidate_nudity(metadata: dict[str, tuple[int, int]]) -> None:
    folder = ROOT / "21_nudity_adjacent"
    source = {path.stem: read_tags(path) for path in folder.glob("*.txt")}
    parent = source.get("nudity_adjacent", set())
    groups = {
        "general_and_complete_nudity": source.get("complete_(or_mostly)", set()) | source.get("nudity_by_gender", set()) | source.get("any_clothes", set()),
        "upper_body_exposure": set().union(*(source.get(name, set()) for name in ("exposed_head_or_neck", "exposed_shoulders_or_arms", "exposed_chest", "exposed_breasts", "exposed_parts_of_breasts", "exposed_nipples", "exposed_torso"))),
        "lower_body_and_crotch_exposure": source.get("focus_on_exposed_ass_or_crotch", set()) | source.get("focus_on_exposed_legs_or_feet", set()),
        "partial_nudity_and_clothing_exceptions": source.get("partial_nudity", set()) | source.get("specific_clothes_or_ornaments_being_worn_as_exceptions", set()) | source.get("misc", set()),
        "dressing_covering_and_viewpoints": source.get("dressing___covering_body_parts", set()) | source.get("naughty_points_of_view", set()),
    }
    assigned = set().union(*groups.values())
    groups["other_nudity_adjacent"] = parent - assigned
    replace_children(folder, {"nudity_adjacent.txt", "extensions_db_a_verifier.txt", "liste_elargie.txt"}, groups, metadata)


def merge_home_objects(metadata: dict[str, tuple[int, int]]) -> None:
    electronics = ROOT / "18_home_electronics"
    furniture = ROOT / "22_furniture"
    destination = ROOT / "18_home_objects_and_furniture"
    if not electronics.exists() and not furniture.exists():
        return
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    groups = {
        "audio_and_media": read_tags(electronics / "audio.txt"),
        "computers_and_office_devices": read_tags(electronics / "computers.txt"),
        "phones_gaming_and_cameras": read_tags(electronics / "phones_and_tablets.txt") | read_tags(electronics / "gaming.txt") | read_tags(electronics / "photo_and_video.txt"),
        "domestic_devices": read_tags(electronics / "home_devices.txt"),
        "seating": read_tags(furniture / "seating.txt"),
        "tables_desks_and_storage": read_tags(furniture / "tables_and_desks.txt") | read_tags(furniture / "storage.txt"),
        "beds_and_other_furniture": read_tags(furniture / "beds.txt") | read_tags(furniture / "other_furniture.txt"),
    }
    for name, values in groups.items():
        write_tags(destination / f"{name}.txt", values, metadata)
    parent = read_tags(electronics / "home_electronics.txt") | read_tags(furniture / "furniture.txt")
    write_tags(destination / "home_objects_and_furniture.txt", parent, metadata)
    shutil.rmtree(electronics)
    shutil.rmtree(furniture)


def remove_empty_lists() -> int:
    removed = 0
    for path in ROOT.rglob("*.txt"):
        if not read_tags(path):
            path.unlink()
            removed += 1
    return removed


def remove_singleton_sublists() -> int:
    protected = {
        "animals.txt", "races.txt", "social_and_family_relations.txt", "holidays.txt",
        "home_objects_and_furniture.txt", "nudity_adjacent.txt", "liste_elargie.txt",
        "extensions_db_a_verifier.txt",
    }
    removed = 0
    for path in ROOT.rglob("*.txt"):
        if path.name not in protected and len(read_tags(path)) == 1:
            path.unlink()
            removed += 1
    return removed


def refresh_indexes() -> None:
    for index in ROOT.rglob("INDEX.tsv"):
        rows = []
        with index.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                target = index.parent / row["fichier"]
                count = len(read_tags(target))
                if count >= 2:
                    rows.append((row["groupe"], count, row["fichier"]))
        with index.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(("groupe", "nombre_tags", "fichier"))
            writer.writerows(rows)


def main() -> None:
    metadata = load_general_tags()
    existing = set(existing_gallery_tags(set(metadata)))
    consolidate_animals(metadata)
    consolidate_races(metadata)
    consolidate_relations(metadata, existing)
    consolidate_holidays(metadata)
    consolidate_nudity(metadata)
    merge_home_objects(metadata)
    removed_empty = remove_empty_lists()
    removed_single = remove_singleton_sublists()
    refresh_indexes()
    print(
        f"Consolidation terminée ; {removed_empty} listes vides et "
        f"{removed_single} micro-listes d'un seul tag supprimées."
    )


if __name__ == "__main__":
    main()
