from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "databases" / "g_tags_260810.db"
OUT = ROOT / "var" / "wiki_drafts" / "honkai_(series).json"


CHARACTER_GROUPS = {
    "Honkai Gakuen": [
        "theresa_apocalypse_(honkai_gakuen)",
        "jyahnar_(honkai_gakuen)",
        "femirins_(honkai_gakuen)",
        "chloe_(honkai_gakuen)",
        "shion_(honkai_gakuen)",
        "silver_(honkai_gakuen)",
        "kaguya_(honkai_gakuen)",
        "totori_(honkai_gakuen)",
    ],
    "Honkai Impact 3rd": [
        "kiana_kaslana",
        "raiden_mei",
        "bronya_zaychik",
        "murata_himeko",
        "theresa_apocalypse",
        "fu_hua",
        "seele_vollerei",
        "elysia_(honkai_impact)",
        "durandal_(honkai_impact)",
        "mobius_(honkai_impact)",
        "kallen_kaslana",
        "kevin_kaslana",
        "otto_apocalypse",
        "herrscher_of_sentience",
    ],
    "Honkai: Star Rail": [
        "trailblazer_(honkai:_star_rail)",
        "stelle_(honkai:_star_rail)",
        "caelus_(honkai:_star_rail)",
        "march_7th_(honkai:_star_rail)",
        "dan_heng_(honkai:_star_rail)",
        "himeko_(honkai:_star_rail)",
        "welt_yang",
        "pom-pom_(honkai:_star_rail)",
        "silver_wolf_(honkai:_star_rail)",
        "kafka_(honkai:_star_rail)",
        "firefly_(honkai:_star_rail)",
        "acheron_(honkai:_star_rail)",
    ],
    "Honkai: Nexus Anima": [
        "kiana_kaslana_(honkai:_nexus_anima)",
        "nanafey_(honkai:_nexus_anima)",
        "parayaya_(honkai:_nexus_anima)",
        "female_animaster_(honkai:_nexus_anima)",
        "maple_manybell_(honkai:_nexus_anima)",
        "kumyo_kyo_(honkai:_nexus_anima)",
        "hua_(honkai:_nexus_anima)",
    ],
}


def links(tags: list[str]) -> str:
    return "\n".join(f"* [[{tag}]]" for tag in tags)


def main() -> None:
    con = sqlite3.connect(DB)
    rows = {
        name: (post_count, category)
        for name, post_count, category in con.execute(
            "SELECT name, post_count, category FROM tags"
        )
    }

    lines = [
        "Chinese video game franchise developed by [[miHoYo]] and published globally under the HoYoverse brand.",
        "",
        "The series uses multiple settings and timelines connected by recurring ideas, terminology and alternate incarnations of characters. A familiar name or appearance does not necessarily identify the same person in every game; use the tag belonging to the work depicted.",
        "",
        "The earlier version of this page described the franchise as the shared setting or multiverse of several miHoYo works, often called the Honkai Universe. This page keeps that useful overview while separating confirmed Honkai titles from other HoYoverse properties.",
        "[h2]Games and related early works[/h2]",
        "[h3]Early works and precursors[/h3]",
        "* [[FlyMe2theMoon]] - miHoYo's debut game. Its title refers to the song [[fly_me_to_the_moon]]. It predates the established Honkai series and is retained here as historical context from the previous wiki entry.",
        "* [[honkai_gakuen]] - the copyright tag used for the Honkai Gakuen games and their characters. The first game was also known as Houkai Gakuen or Zombiegal Kawaii; its sequel was released internationally as Guns GirlZ. See also [[houkai_gakuen]].",
        "[h3]Main games[/h3]",
        "* [[honkai_impact_3rd]] - action role-playing game centered on Valkyries fighting the Honkai. Its cast includes several names and character concepts inherited from Honkai Gakuen in a separate continuity.",
        "* [[honkai:_star_rail]] - turn-based role-playing game following the Astral Express across different worlds. It is an entry in the Honkai franchise, not merely an unrelated HoYoverse game.",
        "[h3]In development[/h3]",
        "* [[honkai:_nexus_anima]] - creature-collection adventure game currently in development. Characters and Anima should use their Nexus Anima-qualified tags where available.",
        "[h2]Recurring elements[/h2]",
        "The franchise repeatedly uses the Honkai, civilization-ending crises, parallel worlds and alternate versions of familiar characters. Honkai Impact 3rd also prominently features Herrschers, including [[herrscher_of_sentience]], while Star Rail develops its own cosmology around Aeons and Paths.",
        "",
        "Characters who resemble or share names with counterparts from another title should remain separated by the appropriate character tags. Examples include [[murata_himeko]] and [[himeko_(honkai:_star_rail)]], as well as [[kiana_kaslana]] and [[kiana_kaslana_(honkai:_nexus_anima)]].",
        "[h2]Notable characters by game[/h2]",
    ]

    for title, tags in CHARACTER_GROUPS.items():
        lines.append(f"[h3]{title}[/h3]" + links(tags))

    lines.extend(
        [
            "[h2]Tagging notes[/h2]",
            "* Use [[honkai_(series)]] for franchise-wide material, crossovers between Honkai titles, or works explicitly presented as belonging to the Honkai franchise.",
            "* Also add the most specific game copyright tag whenever the depicted source is identifiable.",
            "* Do not add this tag solely because a character resembles a counterpart from another HoYoverse title.",
            "* For alternate forms, battlesuits or named variants, combine the applicable specific tag with the base character tag according to Gelbooru's established tag relationships.",
            "[h2]Official art[/h2]",
            "* post #3768645",
            "* post #3768644",
            "[h2]See also[/h2]",
            "* [[mihoyo]]",
            "* [[hoyoverse]]",
            "[h2]External sources[/h2]",
            "* HoYoverse official support portal: https://support.hoyoverse.com/hc/en-us",
            "* Honkai Impact 3rd official site: https://honkaiimpact3.hoyoverse.com/",
            "* Honkai: Star Rail official site: https://hsr.hoyoverse.com/",
            "* Honkai: Nexus Anima official site: https://hna.hoyoverse.com/",
        ]
    )

    source = "\n".join(lines)
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    source = re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)

    referenced = [tag for tags in CHARACTER_GROUPS.values() for tag in tags]
    invalid = [(tag, rows.get(tag)) for tag in referenced if not rows.get(tag) or rows[tag][1] != 4]
    if invalid:
        raise SystemExit(f"Invalid character tags: {invalid}")

    payload = {
        "tag": "honkai_(series)",
        "template": "copyright",
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} with {len(referenced)} validated character tags")


if __name__ == "__main__":
    main()
