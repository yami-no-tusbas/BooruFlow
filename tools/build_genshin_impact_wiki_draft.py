from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "databases" / "g_tags_260810.db"
OUT = ROOT / "var" / "wiki_drafts" / "genshin_impact.json"


GROUPS: dict[str, list[str]] = {
    "Travelers and companions": [
        "aether_(genshin_impact)", "lumine_(genshin_impact)", "paimon_(genshin_impact)",
    ],
    "Mondstadt": [
        "albedo_(genshin_impact)", "amber_(genshin_impact)", "barbara_(genshin_impact)",
        "bennett_(genshin_impact)", "dahlia_(genshin_impact)", "diluc_(genshin_impact)",
        "diona_(genshin_impact)", "eula_(genshin_impact)", "fischl_(genshin_impact)",
        "jean_(genshin_impact)", "kaeya_(genshin_impact)", "klee_(genshin_impact)",
        "lisa_(genshin_impact)", "mika_(genshin_impact)", "mona_(genshin_impact)",
        "noelle_(genshin_impact)", "razor_(genshin_impact)", "rosaria_(genshin_impact)",
        "sucrose_(genshin_impact)", "venti_(genshin_impact)", "varka_(genshin_impact)",
    ],
    "Liyue": [
        "baizhu_(genshin_impact)", "beidou_(genshin_impact)", "chongyun_(genshin_impact)",
        "gaming_(genshin_impact)", "ganyu_(genshin_impact)", "hu_tao_(genshin_impact)",
        "keqing_(genshin_impact)", "lan_yan_(genshin_impact)", "ningguang_(genshin_impact)",
        "qiqi_(genshin_impact)", "shenhe_(genshin_impact)", "xiangling_(genshin_impact)",
        "xianyun_(genshin_impact)", "xiao_(genshin_impact)", "xingqiu_(genshin_impact)",
        "xinyan_(genshin_impact)", "yanfei_(genshin_impact)", "yaoyao_(genshin_impact)",
        "yelan_(genshin_impact)", "yun_jin_(genshin_impact)", "zhongli_(genshin_impact)",
    ],
    "Inazuma": [
        "arataki_itto", "chiori_(genshin_impact)", "gorou_(genshin_impact)",
        "kaedehara_kazuha", "ayaka_(genshin_impact)", "kamisato_ayato",
        "kirara_(genshin_impact)", "kujou_sara", "kuki_shinobu", "mizuki_(genshin_impact)",
        "raiden_shogun", "sangonomiya_kokomi", "sayu_(genshin_impact)",
        "shikanoin_heizou", "thoma_(genshin_impact)", "yae_miko", "yoimiya_(genshin_impact)",
    ],
    "Sumeru": [
        "alhaitham_(genshin_impact)", "candace_(genshin_impact)", "collei_(genshin_impact)",
        "cyno_(genshin_impact)", "dehya_(genshin_impact)", "dori_(genshin_impact)",
        "faruzan_(genshin_impact)", "kaveh_(genshin_impact)", "layla_(genshin_impact)",
        "nahida_(genshin_impact)", "nilou_(genshin_impact)", "sethos_(genshin_impact)",
        "tighnari_(genshin_impact)", "wanderer_(genshin_impact)",
    ],
    "Fontaine": [
        "charlotte_(genshin_impact)", "chevreuse_(genshin_impact)", "clorinde_(genshin_impact)",
        "emilie_(genshin_impact)", "escoffier_(genshin_impact)", "freminet_(genshin_impact)",
        "furina_(genshin_impact)", "lynette_(genshin_impact)", "lyney_(genshin_impact)",
        "navia_(genshin_impact)", "neuvillette_(genshin_impact)", "sigewinne_(genshin_impact)",
        "wriothesley_(genshin_impact)",
    ],
    "Natlan": [
        "chasca_(genshin_impact)", "citlali_(genshin_impact)", "iansan_(genshin_impact)",
        "ifa_(genshin_impact)", "kachina_(genshin_impact)", "kinich_(genshin_impact)",
        "mavuika_(genshin_impact)", "mualani_(genshin_impact)", "ororon_(genshin_impact)",
        "varesa_(genshin_impact)", "xilonen_(genshin_impact)",
    ],
    "Nod-Krai and later story characters": [
        "aino_(genshin_impact)", "columbina_(genshin_impact)", "durin_(genshin_impact)",
        "flins_(genshin_impact)", "illuga_(genshin_impact)", "ineffa_(genshin_impact)",
        "jahoda_(genshin_impact)", "lauma_(genshin_impact)", "linnea_(genshin_impact)",
        "lohen_(genshin_impact)", "nefer_(genshin_impact)", "sandrone_(genshin_impact)",
        "zibai_(genshin_impact)",
    ],
    "Fatui and other major recurring figures": [
        "arlecchino_(genshin_impact)", "capitano_(genshin_impact)", "dainsleif_(genshin_impact)",
        "dottore_(genshin_impact)", "nicole_(genshin_impact)", "pantalone_(genshin_impact)",
        "pierro_(genshin_impact)", "pulcinella_(genshin_impact)", "rhinedottir_(genshin_impact)",
        "signora_(genshin_impact)", "skirk_(genshin_impact)", "tartaglia_(genshin_impact)",
        "tsaritsa_(genshin_impact)",
    ],
}


def main() -> None:
    con = sqlite3.connect(DB)
    rows = {
        name: (post_count, category)
        for name, post_count, category in con.execute(
            "SELECT name, post_count, category FROM tags"
        )
    }
    characters = [tag for tags in GROUPS.values() for tag in tags]
    invalid = [(tag, rows.get(tag)) for tag in characters if not rows.get(tag) or rows[tag][1] != 4]
    if invalid:
        raise SystemExit(f"Invalid character tags: {invalid}")
    duplicates = sorted({tag for tag in characters if characters.count(tag) > 1})
    if duplicates:
        raise SystemExit(f"Duplicate character tags: {duplicates}")

    lines = [
        "[b]Genshin Impact[/b] is a free-to-play open-world action role-playing game developed by [[mihoyo]]. It was released in September 2020 and combines real-time combat, elemental reactions, character switching and gacha-based acquisition of characters and weapons.",
        "",
        "The player travels through the world of Teyvat as the Traveler while searching for their lost sibling. Each major region is associated with an element and governed by an Archon. The game achieved rapid worldwide popularity after release and continues to receive new regions, quests and characters.",
        "[h2]Characters[/h2]",
        "The sections below favor playable, announced and major recurring story characters. Minor NPCs, creatures, enemies, alternate costumes and form-specific tags are not intended to be exhaustive.",
    ]
    for title, tags in GROUPS.items():
        lines.append(f"[h3]{title}[/h3]" + "\n".join(f"* [[{tag}]]" for tag in tags))

    lines.extend([
        "[h2]Common related tags[/h2]",
        "* [[vision_(genshin_impact)]]",
        "* [[seelie_(genshin_impact)]]",
        "* [[hilichurl_(genshin_impact)]]",
        "* [[slime_(genshin_impact)]]",
        "* [[fatui]]",
        "[h2]Tagging notes[/h2]",
        "* Use the most specific established character tag. Alternate costumes, forms and identities should supplement the base character rather than replace this copyright.",
        "* [[aether_(genshin_impact)]] and [[lumine_(genshin_impact)]] identify the two Traveler designs. Do not tag both unless both characters are depicted.",
        "* Some characters have older aliases or duplicate-looking tags. Prefer the canonical tag already used by current Gelbooru posts rather than creating another spelling.",
        "[h2]Developer and related wikis[/h2]",
        "* [[mihoyo]] - developer.",
        "* [[hoyoverse]] - global publishing and services brand.",
        "* [[honkai_(series)]]",
        "* [[honkai_gakuen]]",
        "* [[honkai_impact_3rd]]",
        "* [[honkai:_star_rail]]",
        "* [[honkai:_nexus_anima]]",
        "",
        "Genshin Impact is a miHoYo property but is not itself an entry in the Honkai franchise. Do not add Honkai copyrights solely because of recurring visual motifs, names or fan comparisons.",
        "[h2]External sources[/h2]",
        "* Official site: https://genshin.hoyoverse.com/",
        "* Official character archive: https://genshin.hoyoverse.com/en/character/",
        "* Official HoYoverse support: https://support.hoyoverse.com/hc/en-us/categories/4810489706457-Genshin-Impact",
    ])

    source = "\n".join(lines)
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    source = re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)
    payload = {
        "tag": "genshin_impact",
        "template": "copyright",
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} with {len(characters)} validated character tags")


if __name__ == "__main__":
    main()
