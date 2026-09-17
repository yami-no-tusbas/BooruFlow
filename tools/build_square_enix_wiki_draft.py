from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Companies"
TAG = "square_enix"

SQUARE_HERITAGE = [
    "final_fantasy", "chrono_trigger", "chrono_cross", "seiken_densetsu",
    "saga_frontier", "front_mission", "parasite_eve", "xenogears",
    "vagrant_story",
]
ENIX_HERITAGE = [
    "dragon_quest", "star_ocean", "valkyrie_profile", "actraiser",
    "soul_blazer", "illusion_of_gaia", "terranigma",
]
SQUARE_ENIX_SERIES = [
    "kingdom_hearts", "nier", "drakengard", "octopath_traveler",
    "bravely_default_(series)", "the_world_ends_with_you", "triangle_strategy",
    "harvestella", "forspoken", "foamstars", "voice_of_cards",
    "the_last_remnant", "infinite_undiscovery",
]
OTHER_GAME_PROPERTIES = [
    "live_a_live", "romancing_saga", "lord_of_vermilion",
    "kaku-san-sei_million_arthur", "sinoalice", "grimms_notes",
    "gunslinger_stratos", "gate_of_nightmares", "engage_kill",
    "deep_insanity", "dissidia_final_fantasy", "final_fantasy_brave_exvius",
    "war_of_the_visions:_final_fantasy_brave_exvius", "mobius_final_fantasy",
    "dragon_quest_rivals", "dragon_quest_walk", "dragon_quest_tact",
    "chocobo_no_fushigi_na_dungeon", "fortune_street",
]
HISTORICAL_WESTERN = [
    "tomb_raider", "deus_ex", "legacy_of_kain", "life_is_strange",
    "guardians_of_the_galaxy", "powerwash_simulator",
]
MANGA = [
    "fullmetal_alchemist", "kuroshitsuji", "soul_eater", "pandora_hearts",
    "jibaku_shounen_hanako-kun",
]
COMPANIES = [
    "square_(company)", "enix", "taito", "crystal_dynamics", "tri-ace",
    "platinumgames",
]
LINKS = [
    *SQUARE_HERITAGE, *ENIX_HERITAGE, *SQUARE_ENIX_SERIES,
    *OTHER_GAME_PROPERTIES,
    *HISTORICAL_WESTERN, *MANGA, *COMPANIES, "space_invaders",
]


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def bullets(tags: list[str]) -> list[str]:
    return [f"* [[{tag}]]" for tag in tags]


def main() -> None:
    connection = sqlite3.connect(DB)
    missing = [tag for tag in [TAG, *LINKS] if not connection.execute(
        "SELECT 1 FROM tags WHERE name=?", (tag,)
    ).fetchone()]
    if missing:
        raise SystemExit(f"Missing local tags: {missing}")

    lines = [
        "[b]Square Enix[/b] is a Japanese entertainment company that both develops and publishes video games. It also operates publishing, merchandising and amusement businesses through the wider Square Enix Group.",
        "",
        "The present company traces its main game businesses to [[square_(company)]] and [[enix]], which merged in April 2003. A holding-company structure was introduced in 2008: Square Enix Holdings leads the group, while Square Enix Co., Ltd. conducts game planning, development, publishing and sales. A game published by Square Enix is not necessarily developed by Square Enix itself.",
        "[h2]Major current franchises[/h2]",
        "Square Enix Holdings identifies the following among the group's principal digital-entertainment series:",
        "* [[final_fantasy]]",
        "* [[dragon_quest]]",
        "* [[kingdom_hearts]]",
        "* [[nier]]",
        "* [[octopath_traveler]]",
        "* [[space_invaders]] - owned through the group company [[taito]].",
        "[h2]Square heritage[/h2]",
        "These series and games originated with Square before the 2003 merger:",
        *bullets(SQUARE_HERITAGE),
        "[h2]Enix heritage[/h2]",
        "These series and games originated with Enix or were historically published by it. External studios often performed development; for example, tri-Ace developed Star Ocean and Valkyrie Profile:",
        *bullets(ENIX_HERITAGE),
        "[h2]Square Enix-era games and series[/h2]",
        "Major properties created, developed, co-developed or published after the merger include:",
        *bullets(SQUARE_ENIX_SERIES),
        "",
        "This section describes a publishing relationship, not necessarily sole development or ownership. [[tri-ace]] and [[platinumgames]], among many other external studios, have developed individual Square Enix-published titles.",
        "[h2]Other tagged game properties[/h2]",
        "Additional Square or Square Enix-developed, operated or published properties represented by Gelbooru copyright tags include:",
        *bullets(OTHER_GAME_PROPERTIES),
        "",
        "Several entries are individual games or discontinued online and mobile services rather than active franchises. Subseries such as Dissidia and Brave Exvius remain part of Final Fantasy even when Gelbooru provides them with separate copyright tags.",
        "[h2]Taito and amusement[/h2]",
        "[[taito]] became a Square Enix subsidiary in 2005 and a wholly owned subsidiary in 2006. It operates amusement facilities and develops and publishes arcade and other entertainment products. Its properties include [[space_invaders]]. Taito remains a separate corporate brand within the group.",
        "[h2]Historical Western publishing and former properties[/h2]",
        "Square Enix acquired Eidos plc in 2009 and consequently published or owned a substantial Western catalogue. In August 2022 it sold Crystal Dynamics, Eidos-Montreal, Square Enix Montreal and associated assets to Embracer Group. The transaction included Tomb Raider, Deus Ex, Thief and Legacy of Kain; these should therefore be described as historical Square Enix properties rather than current Square Enix franchises.",
        *bullets(HISTORICAL_WESTERN),
        "",
        "[[life_is_strange]], [[guardians_of_the_galaxy]] and [[powerwash_simulator]] are examples of games or series Square Enix published in particular territories or periods. Publication alone does not make their developers Square Enix studios.",
        "[h2]Manga and publications[/h2]",
        "Square Enix is also a manga and book publisher. Notable series published through its magazines and imprints include:",
        *bullets(MANGA),
        "",
        "This is a publishing relationship and does not imply that Square Enix created every listed manga or owns every underlying right.",
        "[h2]Related company tags[/h2]",
        "* [[square_(company)]] - predecessor company; use for material specifically tied to Square before the merger.",
        "* [[enix]] - predecessor company; use for material specifically tied to Enix before the merger.",
        "* [[taito]] - current group company and distinct brand.",
        "* [[crystal_dynamics]] - former Square Enix subsidiary, sold in 2022.",
        "[h2]Tagging notes[/h2]",
        "Use [[square_enix]] for the company, its branding, company-wide crossovers, promotional material or works where Square Enix itself is the relevant copyright identity. Do not automatically add it to every image from a game or manga merely because Square Enix once developed, owned, licensed or published that work; use the work's specific copyright tag first.",
        "",
        "For older material, distinguish [[square_(company)]] and [[enix]] from the post-merger company. For divested Western properties, use their own copyright tags unless the image specifically concerns their Square Enix-era branding or publication.",
        "[h2]External links[/h2]",
        "* Square Enix Group overview: https://www.hd.square-enix.com/eng/company/",
        "* Official company history: https://www.hd.square-enix.com/eng/company/history.html",
        "* Official group companies: https://www.hd.square-enix.com/eng/company/group.html",
        "* Official digital-entertainment business and principal series: https://www.hd.square-enix.com/eng/group/digitalentertainment.html",
        "* Square Enix statement on the 2022 overseas studio and IP sale: https://www.hd.square-enix.com/eng/ir/policy/message2022_4.html",
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "tag": TAG,
        "template": "copyright",
        "source": compact("\n".join(lines)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    destination = OUT / f"{TAG}.json"
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {destination} | validated tags {len(LINKS)}")


if __name__ == "__main__":
    main()
