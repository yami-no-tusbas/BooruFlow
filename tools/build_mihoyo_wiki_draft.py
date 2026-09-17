from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "databases" / "g_tags_260810.db"
OUT = ROOT / "var" / "wiki_drafts" / "mihoyo.json"


COPYRIGHT_TAGS = [
    "honkai_(series)",
    "honkai_gakuen",
    "honkai_impact_3rd",
    "honkai:_star_rail",
    "honkai:_nexus_anima",
    "genshin_impact",
    "tears_of_themis",
    "zenless_zone_zero",
]


def main() -> None:
    con = sqlite3.connect(DB)
    rows = {
        name: (post_count, category)
        for name, post_count, category in con.execute(
            "SELECT name, post_count, category FROM tags"
        )
    }
    invalid = [(tag, rows.get(tag)) for tag in COPYRIGHT_TAGS if not rows.get(tag) or rows[tag][1] != 3]
    if invalid:
        raise SystemExit(f"Invalid copyright tags: {invalid}")

    lines = [
        "[b]miHoYo[/b] is a Chinese video game developer and entertainment company headquartered in Shanghai. Founded in 2011, it develops games and produces related animation, comics, music and merchandise.",
        "",
        "The company uses the HoYoverse brand for the global publication and operation of many of its games and services. HoYoverse is not a separate fictional copyright: on Gelbooru, use [[mihoyo]] for company-focused material and the specific game or franchise copyright for ordinary fan art.",
        "[h2]Games and franchises[/h2]",
        "[h3]Honkai[/h3]",
        "* [[honkai_(series)]] - umbrella tag for the Honkai franchise.",
        "* [[honkai_gakuen]] - early Honkai games, including Houkai Gakuen 2 / Guns GirlZ. See also [[houkai_gakuen]].",
        "* [[honkai_impact_3rd]] - action role-playing game.",
        "* [[honkai:_star_rail]] - turn-based role-playing game set across multiple worlds.",
        "* [[honkai:_nexus_anima]] - creature-collection adventure game in development.",
        "[h3]Other game properties[/h3]",
        "* [[genshin_impact]] - open-world action role-playing game set in Teyvat.",
        "* [[tears_of_themis]] - romance and detective visual novel.",
        "* [[zenless_zone_zero]] - urban fantasy action role-playing game set around New Eridu.",
        "* [[petit_planet]] - life-simulation game in development. Its current local Gelbooru tag is general rather than copyright.",
        "[h3]Early work[/h3]",
        "* [[FlyMe2theMoon]] - miHoYo's debut game, released before the company's better-known franchises. The link is retained even though the local database does not currently contain a dedicated tag with this exact spelling.",
        "[h2]Related services and projects[/h2]",
        "* [[hoyoverse]] - global publishing and service brand associated with miHoYo. The local database currently classifies this tag separately from ordinary copyrights.",
        "* [[hoyolab]] - official community platform for HoYoverse games.",
        "* [[hoyofair]] - official fan-work programs and broadcasts associated with HoYoverse properties.",
        "* HoYoPlay - game launcher and account-service ecosystem; no exact local Gelbooru tag was found in the current database snapshot.",
        "[h2]Tagging notes[/h2]",
        "* Use [[mihoyo]] for the company, its logo, offices, staff-facing announcements, company celebrations or material explicitly grouping its products as a studio portfolio.",
        "* For artwork from one game, use that game's copyright tag instead of adding [[mihoyo]] automatically.",
        "* Add [[honkai_(series)]] only to material that actually concerns the Honkai franchise; [[genshin_impact]], [[tears_of_themis]] and [[zenless_zone_zero]] are miHoYo properties but are not thereby Honkai entries.",
        "[h2]External sources[/h2]",
        "* miHoYo official company page: https://www.mihoyo.com/en/?page=about",
        "* HoYoverse official site: https://www.hoyoverse.com/en-us/",
        "* HoYoverse official support portal: https://support.hoyoverse.com/hc/en-us",
    ]

    source = "\n".join(lines)
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    source = re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)
    payload = {
        "tag": "mihoyo",
        "template": "copyright",
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} with {len(COPYRIGHT_TAGS)} validated copyright tags")


if __name__ == "__main__":
    main()
