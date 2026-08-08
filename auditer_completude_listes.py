from __future__ import annotations

import csv
from pathlib import Path

from creer_listes_telechargement import existing_gallery_tags, load_general_tags


ROOT = Path("listes_telechargement")

# Termes volontairement concrets. Les extensions restent séparées des listes
# contrôlées, car un nom de tag peut être lexicalement proche sans être pertinent.
RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "01_sexual_themes": ("sexual_themes.txt", (
        "bdsm", "bondage", "gag", "shibari", "restrained", "sex_toy", "dildo", "vibrator",
        "rape", "molestation", "forced_orgasm", "exposure", "public_use", "fellatio", "oral",
        "penetration", "insertion", "fisting", "sex_position", "cum_", "ejaculation", "ovipos",
        "egg_laying", "tentacle_sex",
    )),
    "02_weapons": ("weapons.txt", (
        "weapon", "sword", "knife", "dagger", "axe", "spear", "polearm", "bow_(weapon)", "crossbow",
        "gun", "handgun", "pistol", "rifle", "shotgun", "revolver", "machine_gun", "cannon", "launcher",
        "missile", "rocket", "grenade", "bomb", "explosive", "ammunition", "bullet", "shield",
    )),
    "08_body_modifications": ("body_modifications.txt", (
        "piercing", "tattoo", "scarification", "branding", "implant", "split_tongue", "stretched_ears",
        "ear_gauge", "body_writing", "body_paint", "henna",
    )),
    "09_injuries": ("injuries.txt", (
        "injury", "injured", "wound", "bruise", "bleeding", "blood_on_", "blood_from_", "bloody_",
        "bandage", "scar_", "_scar", "burn_scar", "burned_skin", "broken_bone", "fracture", "missing_limb",
    )),
    "10_vehicles": ("vehicles.txt", (
        "vehicle", "car", "truck", "bus", "van", "motorcycle", "scooter", "bicycle", "train", "locomotive",
        "aircraft", "airplane", "fighter_jet", "helicopter", "boat", "ship", "submarine", "watercraft",
        "spacecraft", "spaceship", "rover",
    )),
    "11_mechas": ("mechas.txt", (
        "mecha", "mech", "mobile_suit", "powered_armor", "exoskeleton", "giant_robot", "piloted_robot",
    )),
    "12_clothing": ("clothing.txt", (
        "clothes", "clothing", "dress", "shirt", "skirt", "pants", "shorts", "jacket", "coat", "uniform",
        "underwear", "panties", "bra", "lingerie", "swimsuit", "shoes", "boots", "socks", "stockings",
        "gloves", "hat", "headwear", "scarf", "belt",
    )),
    "13_animals": ("animals.txt", (
        "animal", "cat", "dog", "wolf", "fox", "rabbit", "bunny", "horse", "cow", "sheep", "goat", "pig",
        "bear", "lion", "tiger", "deer", "mouse", "rat", "bird", "eagle", "owl", "fish", "shark", "whale",
        "dolphin", "snake", "lizard", "frog", "spider", "insect", "butterfly",
    )),
    "14_glasses_and_eyewear": ("glasses_and_eyewear.txt", (
        "glasses", "eyewear", "sunglasses", "goggles", "monocle", "visor", "eyepatch", "blindfold",
    )),
    "15_races": ("races.txt", (
        "angel", "demon", "elf", "dwarf", "orc", "goblin", "oni", "fairy", "vampire", "werewolf", "mermaid",
        "centaur", "lamia", "harpy", "dullahan", "cyclops", "minotaur", "satyr", "shapeshifter", "monster_girl",
    )),
    "16_social_and_family_relations": ("social_and_family_relations.txt", (
        "father_and_", "mother_and_", "stepfather_and_", "stepmother_and_", "brother_and_", "sister_and_",
        "siblings", "brothers", "sisters", "twins", "triplets", "cousins", "husband_and_", "wife_and_",
        "parent_and_", "grandfather_and_", "grandmother_and_",
    )),
    "17_holidays": ("holidays.txt", (
        "christmas", "halloween", "new_year", "valentine", "easter", "thanksgiving", "tanabata", "obon", "hanami",
    )),
    "18_home_electronics": ("home_electronics.txt", (
        "headphones", "earphones", "speaker", "microphone", "computer", "laptop", "keyboard", "smartphone",
        "cellphone", "game_console", "game_controller", "camera", "camcorder", "television", "printer", "roomba",
    )),
    "19_musical_instruments": ("musical_instruments.txt", (
        "instrument", "guitar", "violin", "cello", "harp", "piano", "organ_(instrument)", "flute", "clarinet",
        "saxophone", "trumpet", "trombone", "drum", "percussion", "accordion", "harmonica",
    )),
    "20_bathing_and_showering": ("bathing_and_showering.txt", (
        "bathing", "showering", "bath", "bathtub", "bathroom", "hot_spring", "washing_hair", "washing_body",
    )),
    "21_nudity_adjacent": ("nudity_adjacent.txt", (
        "nudity", "nude", "naked", "topless", "bottomless", "casual_nudity", "convenient_censoring", "clothed_nude",
    )),
    "22_furniture": ("furniture.txt", (
        "chair", "armchair", "sofa", "couch", "stool", "bench", "table", "desk", "bookshelf", "bookcase",
        "cabinet", "dresser", "wardrobe", "bed", "futon", "crib", "furniture",
    )),
    "23_universes_and_aesthetics": ("universes_and_aesthetics.txt", (
        "science_fiction", "cyberpunk", "steampunk", "dieselpunk", "biopunk", "solarpunk", "atompunk",
        "post_apocalypse", "post-apocalyptic", "retro_futur",
    )),
    "24_social_styles": ("social_styles.txt", (
        "ganguro", "gyaru", "delinquent", "sukeban", "bancho", "punk", "rebel", "biker", "rocker",
    )),
}


def matches(tag: str, term: str) -> bool:
    if term.startswith("_") or term.endswith("_") or "_" in term or "-" in term or "(" in term:
        return term in tag
    tokens = tag.replace("-", "_").split("_")
    return term == tag or term in tokens


def main() -> None:
    tags = load_general_tags()
    existing = existing_gallery_tags(set(tags))
    summary: list[tuple[str, int, int, int]] = []
    for folder_name, (parent_filename, terms) in RULES.items():
        folder = ROOT / folder_name
        parent = folder / parent_filename
        if not parent.is_file():
            continue
        controlled = {line.strip() for line in parent.read_text(encoding="utf-8").splitlines() if line.strip()}
        reasons: dict[str, list[str]] = {}
        for tag, (post_count, _ambiguous) in tags.items():
            if post_count <= 0 or tag in existing or tag in controlled:
                continue
            hit = [term for term in terms if matches(tag, term)]
            if hit:
                reasons[tag] = hit
        extensions = sorted(reasons, key=lambda tag: (-tags[tag][0], tag))
        enlarged = sorted(controlled | set(extensions), key=lambda tag: (-tags[tag][0], tag))
        (folder / "extensions_db_a_verifier.txt").write_text("".join(f"{tag}\n" for tag in extensions), encoding="utf-8")
        (folder / "liste_elargie.txt").write_text("".join(f"{tag}\n" for tag in enlarged), encoding="utf-8")
        with (folder / "extensions_db_a_verifier.tsv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(("tag", "post_count", "ambiguous", "regles"))
            for tag in extensions:
                writer.writerow((tag, tags[tag][0], tags[tag][1], " | ".join(reasons[tag])))
        summary.append((folder_name, len(controlled), len(extensions), len(enlarged)))

    with (ROOT / "AUDIT_COMPLETUDE.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("famille", "liste_controlee", "extensions_db", "liste_elargie"))
        writer.writerows(summary)
    for row in summary:
        print(f"{row[0]}: controlee={row[1]}, extensions={row[2]}, elargie={row[3]}")


if __name__ == "__main__":
    main()
