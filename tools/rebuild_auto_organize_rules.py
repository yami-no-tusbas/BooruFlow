"""Build the canonical AutoOrganize tree from audited historical sources.

Only single positive Grabber queries become active leaves.  Compound queries are
reported and skipped; their individual tags are added only when they are also
explicitly present in the curated inventory below or in another simple monitor.
The generated JSON is a runtime snapshot and has no dependency on monitors.json.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

FAMILIES = OrderedDict((
    ("Relations", "relations"),
    ("Sexual Themes", "sexual_themes"),
    ("Races", "races"),
    ("Professions", "professions"),
    ("Weapons", "weapons"),
    ("Animal Ears", "animal_ears"),
    ("General", "general"),
    ("Piercings", "piercings"),
    ("Styles vestimentaires", "clothing_styles"),
    ("HairStyles", "hairstyles"),
))
FAMILY_BY_CASEFOLD = {name.casefold(): name for name in FAMILIES}
SITE_NAMES = {"gelbooru.com": "gelbooru", "e621.net": "e621"}
CONDITION_TAG = re.compile(r'<_?"([^" ]+)"\s*>')

EXTRA_TAGS = {
    "Relations": "brother_and_sister couple father_and_daughter mother_and_son sisters twins",
    "Races": "dark-skinned_female dark_elf android cyborg demon_girl succubus elf angel mecha_musume oni fairy centauroid monster_girl doll_joints vampire dwarf black_sclera yandere giantess blue_skin purple_skin orc black_skin spider_girl zombie na'vi",
    "Professions": "alchemist archer barbarian bard belly_dancer dancer cleric dj doctor druid fighter firefighter jester knight lancer lifeguard mage maid mechanic military_uniform monk ninja nude_model necromancer nun nurse office_lady paladin police_uniform priest priestess princess prisoner prostitution queen ranger samurai sex_slave slave shaman soldier sorceress stripper teacher thief warrior witch wizard",
    "Weapons": "anti-material_rifle anti-tank_rifle axe baseball_bat bow_(weapon) claw_(weapon) claymore_(sword) club_(weapon) crossbow dagger dual_wielding firearm gun hammer holding_weapon huge_weapon knife kunai mace pistol pistol_sword polearm scythe shield short_sword shuriken sniper_rifle spear spiked_club staff sword tonfa",
    "Animal Ears": "animal_ears aardwolf_ears alpaca_ears bat_ears bear_ears bird_ears bunny_ears rabbit_ears cat_ears cow_ears coyote_ears deer_ears dog_ears dragon_ears fake_animal_ears fin_ears fox_ears goat_ears horse_ears hyena_ears jackal_ears jaguar_ears leopard_ears lion_ears monkey_ears moose_ears mouse_ears owl_ears panda_ears raccoon_ears robot_ears sheep_ears snow_leopard_ears squirrel_ears tiger_ears weasel_ears wolf_ears",
    "Styles vestimentaires": "animal_costume animal_print arabian_clothes armlet baseball_uniform biker_clothes bodysuit boxers bustier cat_lingerie cat_panties cheerleader collar bell_collar choker cuffs-to-collar spiked_collar crop_top_overhang crotchless egyptian_clothes fishnet_legwear fishnet_pantyhose gas_mask high_heels hooded_jacket labcoat latex_suit leg_garter lolita_fashion gothic_lolita sweet_lolita mask military_uniform pajamas panties pants jeans police_uniform school_uniform shorts skirt spacesuit sundress swimsuit bikini competition_swimsuit one-piece_swimsuit school_swimsuit track_jacket wetsuit",
    "HairStyles": "short_hair floating_hair hair_bun wavy_hair drill_hair hair_blowing hair_intakes short_hair_with_long_locks messy_hair very_short_hair hair_over_shoulder absurdly_long_hair straight_hair hair_over_face tied_hair hair_strand low-tied_long_hair curly_hair hairband hair_ornament hairpin hair_rings hair_scrunchie wet_hair shiny_hair streaked_hair two-tone_hair adjusting_hair drying_hair hair_bobbles hair_stick hair_tie hair_tubes playing_with_own_hair tying_hair",
    "Sexual Themes": "rape after_rape assisted_rape broken_rape_victim imminent_rape you_gonna_get_raped ball_gag chastity_belt enema pet_play pillory shibari torture whip_marks wooden_horse",
}

FIREARMS = {"anti-material_rifle", "anti-materiel_rifle", "anti-tank_rifle", "assault_rifle", "firearm", "gun", "handgun", "holding_gun", "holster", "magazine_(weapon)", "pistol", "rifle", "shotgun", "silencer", "sniper_rifle", "submachine_gun"}
SWORDS = {"claymore_(sword)", "katana", "pistol_sword", "saber_(weapon)", "scabbard", "short_sword", "sword"}
SWIMSUITS = {"bikini", "competition_swimsuit", "frilled_bikini", "micro_bikini", "one-piece_swimsuit", "school_swimsuit", "swimsuit", "wetsuit"}
PANTIES = {"bear_panties", "bow_panties", "boxers", "cat_panties", "frilled_panties", "highleg_panties", "lace-trimmed_panties", "lowleg_panties", "micro_panties", "panties", "pearl_thong", "side-tie_panties"}
SKIRTS = {"frilled_skirt", "high-waist_skirt", "long_skirt", "micro_skirt", "microskirt", "miniskirt", "plaid_skirt", "skirt"}
RAPE_TAGS = {"rape", "after_rape", "assisted_rape", "broken_rape_victim", "imminent_rape", "you_gonna_get_raped"}
BDSM_TAGS = {"ball_gag", "chastity_belt", "enema", "pet_play", "pillory", "shibari", "torture", "whip_marks", "wooden_horse"}


@dataclass
class Leaf:
    family: str
    path: tuple[str, ...]
    label: str
    destination: str
    tags: list[str] = field(default_factory=list)
    sites: list[str] = field(default_factory=list)


def _positive_query_tags(monitor: dict) -> list[str]:
    query = monitor.get("query", {})
    raw = query.get("tags", []) if isinstance(query, dict) else []
    tokens = [token for value in raw for token in str(value).split()]
    return [token for token in tokens if token and not token.startswith("-") and ":" not in token
            and not any(marker in token for marker in ("*", "~"))]


def _sites(monitor: dict) -> list[str]:
    return [SITE_NAMES[value] for value in monitor.get("sites", ()) if value in SITE_NAMES]


def _canonical_subpath(family: str, tag: str, historical: tuple[str, ...] = ()) -> tuple[str, ...]:
    normalized = tuple("BDSM" if value.casefold() == "bdsm" else value for value in historical)
    if family == "Races" and tag in {"demon_girl", "oni", "succubus"}: return ("demon_girl",)
    if family == "Weapons" and tag in FIREARMS: return ("Firearms",)
    if family == "Weapons" and tag in SWORDS: return ("Swords",)
    if family == "Styles vestimentaires" and tag in SWIMSUITS: return ("Swimsuits",)
    if family == "Styles vestimentaires" and tag in PANTIES: return ("Panties",)
    if family == "Styles vestimentaires" and tag in SKIRTS: return ("Skirts",)
    if family == "Sexual Themes" and tag in RAPE_TAGS: return ("Non-consensual",)
    if family == "Sexual Themes" and tag in BDSM_TAGS: return ("BDSM",)
    return normalized


def _safe_id(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return value or "rule"


def collect(monitors_path: Path) -> tuple[list[Leaf], list[dict[str, object]], dict[str, int]]:
    data = json.loads(monitors_path.read_text(encoding="utf-8-sig"))
    leaves: OrderedDict[tuple[str, tuple[str, ...], str], Leaf] = OrderedDict()
    skipped: list[dict[str, object]] = []

    def add(family: str, path: tuple[str, ...], label: str, destination: str,
            tags: list[str], sites: list[str]) -> None:
        existing_tag = next((leaf for leaf in leaves.values()
                             if leaf.family == family and set(leaf.tags).intersection(tags)), None)
        if existing_tag is not None:
            for site in sites:
                if site not in existing_tag.sites: existing_tag.sites.append(site)
            return
        key = (family, path, destination.casefold())
        leaf = leaves.setdefault(key, Leaf(family, path, label, destination))
        for tag in tags:
            if tag not in leaf.tags: leaf.tags.append(tag)
        for site in sites:
            if site not in leaf.sites: leaf.sites.append(site)

    for index, monitor in enumerate(data.get("monitors", ())):
        filename = str(monitor.get("filenameOverride", ""))
        if not filename.casefold().startswith("tags ("):
            continue
        directory = filename.rsplit("/", 1)[0]
        segments = directory.split("/")
        family_index = next((i for i, value in enumerate(segments)
                             if value.split("<", 1)[0].strip().casefold() in FAMILY_BY_CASEFOLD), None)
        query_tags = _positive_query_tags(monitor)
        sites = _sites(monitor)
        if family_index is None:
            if len(query_tags) != 1:
                skipped.append({"monitor": index, "reason": "compound_query", "tags": query_tags,
                                "destination": directory})
                continue
            tag = query_tags[0]
            static_tail = [value for value in segments[1:] if "%" not in value and "<" not in value
                           and not value.startswith(("_", "n_"))]
            label = static_tail[-1] if static_tail else tag
            add("General", (), label, f"Tags/{label}", [tag], sites)
            continue

        family = FAMILY_BY_CASEFOLD[segments[family_index].split("<", 1)[0].strip().casefold()]
        tail = segments[family_index + 1:]
        conditional_tags = [tag for value in tail for tag in CONDITION_TAG.findall(value)]
        static_tail = [value.split("<", 1)[0].strip() for value in tail
                       if value.split("<", 1)[0].strip() and "%" not in value.split("<", 1)[0]]
        if conditional_tags:
            base = static_tail[-1] if static_tail else ""
            for tag in ([base] if base else []) + conditional_tags:
                path = _canonical_subpath(family, tag, tuple(static_tail[:-1]))
                destination = "/".join(("Tags", family, *path, tag))
                add(family, path, tag, destination, [tag], sites)
            continue
        if len(query_tags) != 1:
            skipped.append({"monitor": index, "reason": "compound_query", "tags": query_tags,
                            "destination": directory})
            continue
        tag = query_tags[0]
        path = _canonical_subpath(family, tag, tuple(static_tail))
        destination = "/".join(("Tags", family, *path, tag))
        add(family, path, tag, destination, [tag], sites)

    monitor_counts = {family: sum(leaf.family == family for leaf in leaves.values()) for family in FAMILIES}
    for family, values in EXTRA_TAGS.items():
        for tag in values.split():
            path = _canonical_subpath(family, tag)
            destination = "/".join(("Tags", family, *path, tag))
            existing = next((leaf for leaf in leaves.values()
                             if leaf.family == family and leaf.destination.casefold() == destination.casefold()), None)
            if existing is not None:
                if tag not in existing.tags: existing.tags.append(tag)
                continue
            add(family, path, tag, destination, [tag], ["gelbooru"])
    return list(leaves.values()), skipped, monitor_counts


def build_tree(leaves: list[Leaf]) -> dict[str, object]:
    family_nodes = OrderedDict((name, {"id": node_id, "label": name, "kind": "branch", "children": []})
                               for name, node_id in FAMILIES.items())
    def branch(parent: dict, label: str) -> dict:
        found = next((child for child in parent["children"]
                      if child["label"].casefold() == label.casefold()), None)
        if found is not None:
            found.setdefault("children", [])
            return found
        node_id = _safe_id(label)
        found = {"id": node_id, "label": label, "kind": "branch", "children": []}
        parent["children"].append(found); return found

    for leaf in leaves:
        parent = family_nodes[leaf.family]
        for label in leaf.path: parent = branch(parent, label)
        base_id = "navi" if leaf.label == "na'vi" else _safe_id(leaf.label)
        existing_parent = next((child for child in parent["children"]
                                if child["label"].casefold() == leaf.label.casefold()), None)
        if existing_parent is not None and existing_parent["kind"] == "branch":
            existing_parent.update({"kind": "rule", "tags": leaf.tags,
                                    "sites": leaf.sites or ["gelbooru"],
                                    "destination": leaf.destination})
            continue
        sibling_ids = {child["id"] for child in parent["children"]}
        node_id = base_id; suffix = 2
        while node_id in sibling_ids:
            node_id = f"{base_id}_{suffix}"; suffix += 1
        node = {"id": node_id, "label": leaf.label, "kind": "rule", "tags": leaf.tags,
                "sites": leaf.sites or ["gelbooru"], "destination": leaf.destination}
        parent["children"].append(node)

    return {"version": 3, "roots": [
        {"id": "dedicated", "label": "Branches dédiées / routage", "kind": "branch", "children": [
            {"id": "gelbooru_cl", "label": "Tags C&L", "kind": "route", "sites": ["gelbooru"],
             "tags": ["child", "loli"], "destination": "Tags C&L (gelbooru)", "special": "dedicated"},
            {"id": "e621_yl", "label": "Tags Y&L", "kind": "route", "sites": ["e621"],
             "tags": ["young", "loli"], "destination": "Tags Y&L (e621)", "special": "dedicated"},
            {"id": "boys_review", "label": "Garçons — à vérifier", "kind": "route",
             "sites": ["gelbooru"], "tags": ["shota", "loli"], "destination": "",
             "special": "ambiguous"},
        ]},
        {"id": "tags", "label": "Tags", "kind": "branch", "children": list(family_nodes.values())},
        {"id": "species", "label": "Species", "kind": "dynamic", "source": "species",
         "destination": "Species/{value}", "active": True},
        {"id": "copyright", "label": "Copyright", "kind": "dynamic", "source": "copyrights",
         "destination": "Copyright", "special": "copyright_character", "active": True},
        {"id": "artist", "label": "Artist", "kind": "dynamic", "source": "artists",
         "destination": "Artist/{value}", "active": True},
    ], "historical_candidates_validated": True,
       "generation_policy": "single positive monitors plus explicitly curated historical leaves"}


def inventory(tree: dict[str, object]) -> dict[str, object]:
    tags = next(root for root in tree["roots"] if root["id"] == "tags")
    def terminal_nodes(node: dict) -> list[dict]:
        result = [node] if node.get("kind") == "rule" else []
        for child in node.get("children", ()): result.extend(terminal_nodes(child))
        return result
    branches = {branch["label"]: len(terminal_nodes(branch)) for branch in tags["children"]}
    leaves = [leaf for branch in tags["children"] for leaf in terminal_nodes(branch)]
    return {"branches": branches, "tags_total": len(leaves),
            "gelbooru": sum("gelbooru" in leaf.get("sites", ()) for leaf in leaves),
            "e621": sum("e621" in leaf.get("sites", ()) for leaf in leaves),
            "shared": sum(set(leaf.get("sites", ())) == {"gelbooru", "e621"} for leaf in leaves)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("monitors", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    leaves, skipped, monitor_counts = collect(args.monitors)
    tree = build_tree(leaves)
    args.output.write_text(json.dumps(tree, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before = {"branches":{"Relations":5,"Sexual Themes":8,"Races":23,"Professions":3,
              "Weapons":11,"Animal Ears":0,"General":0,"Piercings":0,
              "Styles vestimentaires":0,"HairStyles":0},
              "tags_total":50,"gelbooru":50,"e621":50,"shared":50}
    report = {"before":before,"historical_monitor_leaves":monitor_counts,
              "after":inventory(tree), "skipped_historical_candidates": skipped}
    if args.report: args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["after"], ensure_ascii=False, indent=2))
    print(f"Skipped compound/non-terminal monitor candidates: {len(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
