from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from creer_listes_telechargement import collect_all_taxonomy_tags, collect_empty_leaf_tags, load_general_tags


OUTPUT = Path("listes_telechargement/12_clothing")
TAXONOMY = Path("tag_organization.json")


def safe_name(value: str) -> str:
    return value.casefold().replace(" ", "_").replace("/", "_").replace(":", "_").replace("&", "and")


def collect_paths(node: object, tags: dict[str, tuple[int, int]]) -> dict[str, set[tuple[str, ...]]]:
    raw = collect_all_taxonomy_tags(node)
    for tag, paths in collect_empty_leaf_tags(node, set(tags)).items():
        raw[tag].update(paths)
    canonical = {tag.casefold(): tag for tag in tags}
    result: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for tag, paths in raw.items():
        resolved = canonical.get(tag.casefold())
        if resolved is not None:
            result[resolved].update(paths)
    return result


def write_axis(axis: str, groups: dict[str, set[str]], tags: dict[str, tuple[int, int]]) -> None:
    destination = OUTPUT / axis
    destination.mkdir(parents=True, exist_ok=True)
    for group, values in groups.items():
        ordered = sorted(values, key=lambda tag: (-tags[tag][0], tag))
        (destination / f"{safe_name(group)}.txt").write_text(
            "".join(f"{tag}\n" for tag in ordered), encoding="utf-8"
        )
    with (destination / "INDEX.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("groupe", "nombre_tags", "fichier"))
        for group, values in groups.items():
            writer.writerow((group, len(values), f"{safe_name(group)}.txt"))


def tags_under(node: object, tags: dict[str, tuple[int, int]], allowed: set[str]) -> set[str]:
    return set(collect_paths(node, tags)).intersection(allowed)


def main() -> None:
    tags = load_general_tags()
    allowed = {
        line.strip()
        for line in (OUTPUT / "clothing.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    document = json.loads(TAXONOMY.read_text(encoding="utf-8"))
    attire = document["boards"]["gelbooru"]["Visual characteristics"]["Attire and body accessories"]["Attire"]

    type_mapping = {
        "Headwear": "Hats and Headgear",
        "Tops": "Shirts and Top wear",
        "Bottoms": "Pants and Bottom wear",
        "Legwear and footwear": "Legs and Feet",
        "Uniforms and costumes": "Uniforms and Costumes",
        "Swimwear and bodywear": "Swimsuit and Body",
        "Bikinis": "bikini",
        "Jewelry and accessories": "Jewelry and Accessories",
        "Dresses": "Dress",
        "Eyewear": "Eyewear",
    }
    write_axis(
        "par_type",
        {label: tags_under(attire[key], tags, allowed) for label, key in type_mapping.items()},
        tags,
    )

    fashion = attire["Fashion_style"]
    style_groups = {
        "General fashion styles": tags_under(fashion.get("General", {}), tags, allowed),
        "Japanese styles": tags_under(fashion.get("Japanese fashion styles", {}), tags, allowed),
        "Gyaru": tags_under(fashion.get("Gyaru", {}), tags, allowed),
        "Lolita fashion": tags_under(fashion.get("Lolita_fashion", {}), tags, allowed),
        "Western styles": tags_under(fashion.get("Western fashion styles", {}), tags, allowed),
        "Chinese and Korean styles": tags_under(fashion.get("Chinese & Korean fashion styles", {}), tags, allowed),
        "Historical styles": tags_under(fashion.get("Cultural and historical styles", {}), tags, allowed),
    }
    write_axis("par_style", style_groups, tags)

    traditional = attire["Traditional Clothing"]
    era_groups = {
        "Decades": tags_under(fashion.get("Decades", {}), tags, allowed),
        "Historical styles": tags_under(fashion.get("Cultural and historical styles", {}), tags, allowed),
    }
    for name, node in traditional.items():
        if not name.startswith("__") and isinstance(node, dict):
            era_groups[name.replace("_", " ")] = tags_under(node, tags, allowed)
    write_axis("par_epoque_et_culture", era_groups, tags)

    def selected(*needles: str) -> set[str]:
        return {
            tag for tag in allowed
            if any(needle in tag for needle in needles)
        }

    gender_groups = {
        "Masculine-coded garments": selected(
            "necktie", "suit", "tuxedo", "boxers", "fundoshi", "male_swimwear", "boy_clothes", "menswear"
        ),
        "Feminine-coded garments": selected(
            "dress", "skirt", "bra", "panties", "lingerie", "high_heels", "female_swimwear", "womenswear"
        ),
        "Crossdressing": selected("crossdress", "cross-dress", "gender_bender"),
        "Androgynous or unisex": selected("androgynous", "unisex"),
    }
    write_axis("par_presentation_de_genre", gender_groups, tags)

    sexual = tags_under(attire["sexual_attire"], tags, allowed)
    nudity = tags_under(attire["Nudity"], tags, allowed)
    states = tags_under(attire["Clothing States"], tags, allowed)
    exposure_groups = {
        "Formal or covering": selected("formal", "conservative", "modest", "business_suit", "full_body"),
        "Casual": selected("casual", "everyday_clothes", "loungewear"),
        "Tight or suggestive": selected("skin_tight", "tight_clothes", "cleavage", "underboob", "sideboob"),
        "Revealing": selected("revealing", "see-through", "sheer_", "micro_", "lowleg", "highleg"),
        "Lingerie and sexual attire": sexual,
        "Open torn or displaced clothes": states | selected("clothes_lift", "dress_lift", "skirt_lift", "clothes_pull"),
        "Partial nudity and exceptions": nudity,
    }
    write_axis("par_degre_exposition", exposure_groups, tags)

    usage_groups = {
        "School": selected("school_uniform", "serafuku", "school_swimsuit"),
        "Military": selected("military_uniform", "combat_uniform", "army_uniform", "naval_uniform"),
        "Medical": selected("nurse", "doctor", "medical_uniform", "surgical"),
        "Work and service": selected("work_clothes", "maid", "waitress", "chef", "office_lady", "business_suit"),
        "Sports": selected("sportswear", "sports_uniform", "gym_uniform", "track_suit", "athletic"),
        "Sleep and home": selected("pajamas", "sleepwear", "nightgown", "bathrobe", "loungewear"),
        "Ceremonial and wedding": selected("wedding", "bridal", "ceremonial", "funeral"),
    }
    write_axis("par_usage", usage_groups, tags)

    pattern_groups = {
        "Patterns": tags_under(attire["Patterns"], tags, allowed),
        "Prints": tags_under(attire["Prints"], tags, allowed),
        "Leather and latex": selected("leather", "latex", "rubber"),
        "Lace and frills": selected("lace", "frill", "ruffle"),
        "Fur and wool": selected("fur_", "wool", "knit"),
        "Transparent materials": selected("transparent", "see-through", "sheer_"),
    }
    write_axis("par_motif_et_matiere", pattern_groups, tags)

    print(f"Clothing classé sur 7 axes à partir de {len(allowed)} tags contrôlés.")


if __name__ == "__main__":
    main()
