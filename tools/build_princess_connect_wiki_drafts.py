from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Princess Connect"

GUILDS = {
    "Central characters": ["yuuki_(princess_connect!)", "ameth_(princess_connect!)"],
    "Gourmet Guild": ["pecorine_(princess_connect!)", "kokkoro_(princess_connect!)", "karyl_(princess_connect!)", "sheffy_(princess_connect!)"],
    "Twinkle Wish": ["hiyori_(princess_connect!)", "yui_(princess_connect!)", "rei_(princess_connect!)"],
    "Labyrinth": ["labyrista_(princess_connect!)", "shizuru_(princess_connect!)", "rino_(princess_connect!)"],
    "Carmina": ["nozomi_(princess_connect!)", "chika_(princess_connect!)", "tsumugi_(princess_connect!)"],
    "Little Lyrical": ["mimi_(princess_connect!)", "kyoka_(princess_connect!)", "misogi_(princess_connect!)"],
    "Forestier": ["misato_(princess_connect!)", "aoi_(princess_connect!)", "hatsune_(princess_connect!)"],
    "Diabolos": ["illya_(princess_connect!)", "akari_(princess_connect!)", "yori_(princess_connect!)", "shinobu_(princess_connect!)", "miyako_(princess_connect!)"],
    "Nightmare": ["jun_(princess_connect!)", "christina_(princess_connect!)", "tomo_(princess_connect!)", "matsuri_(princess_connect!)"],
    "Sarendia Orphanage": ["saren_(princess_connect!)", "suzume_(princess_connect!)", "ayane_(princess_connect!)", "kurumi_(princess_connect!)"],
    "Caon": ["maho_(princess_connect!)", "makoto_(princess_connect!)", "kaori_(princess_connect!)", "kasumi_(princess_connect!)"],
    "Elizabeth Park": ["mahiru_(princess_connect!)", "lima_(princess_connect!)", "shiori_(princess_connect!)", "rin_(princess_connect!)"],
    "Mercurius Foundation": ["akino_(princess_connect!)", "mifuyu_(princess_connect!)", "yukari_(princess_connect!)", "tamaki_(princess_connect!)"],
    "Twilight Caravan": ["ruka_(princess_connect!)", "anna_(princess_connect!)", "eriko_(princess_connect!)", "mitsuki_(princess_connect!)", "nanaka_(princess_connect!)"],
    "Lucent Academy": ["io_(princess_connect!)", "misaki_(princess_connect!)", "suzuna_(princess_connect!)"],
    "Weissflügel - Landosol Branch": ["monika_weisswind", "ninon_(princess_connect!)", "kuka_(princess_connect!)", "yuki_(princess_connect!)", "ayumi_(princess_connect!)"],
    "St. Theresa's Academy - Friendship Club": ["yuni_(princess_connect!)", "chieru_(princess_connect!)", "chloe_(princess_connect!)"],
    "Dragon's Nest": ["homare_(princess_connect!)", "kaya_(princess_connect!)", "inori_(princess_connect!)"],
    "Richmond Commerce Association": ["creditta_(princess_connect!)"],
    "Rage Legion": ["zane_(princess_connect!)", "kariza_(princess_connect!)", "ranpha_(princess_connect!)", "misora_(princess_connect!)", "azold_(princess_connect!)", "nea_(princess_connect!)"],
    "Alter Maiden": ["riri_(princess_connect!)", "clear_(princess_connect!)", "precia_(princess_connect!)", "quria_(princess_connect!)"],
    "Bandit Sisters": ["yamato_(princess_connect!)", "wakana_(princess_connect!)", "fubuki_(princess_connect!)"],
    "Geo Theogonia": ["lyrael_(princess_connect!)", "kururu_(princess_connect!)", "croce_(princess_connect!)"],
    "Geo Gehenna": ["anemone_(princess_connect!)", "nephi=nera"],
    "Other major story characters": ["muimi_(princess_connect!)", "neneka_(princess_connect!)", "karin_(princess_connect!)", "mana_senri_(princess_connect!)", "eris_(princess_connect!)", "fio_(princess_connect!)", "minerva_(princess_connect!)"],
}


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def build_pages() -> dict[str, list[str]]:
    return {
        "princess_connect!": [
            "[b]Princess Connect![/b] is a Japanese multimedia franchise created by Cygames. The original browser and mobile role-playing game launched in Japan on February 18, 2015 and ended service in June 2016.",
            "",
            "Its story took place around Legend of Astrum, a virtual-reality game in which [[yuuki_(princess_connect!)]] met heroines represented by fantasy avatars. Twinkle Wish—[[hiyori_(princess_connect!)]], [[yui_(princess_connect!)]] and [[rei_(princess_connect!)]]—formed the central party of the original story.",
            "[h2]Sequel and adaptations[/h2]",
            "* [[princess_connect!_re:dive]] - 2018 sequel and the source of most current artwork.",
            "* The Princess Connect! Re:Dive television anime uses [[princess_connect!_re:dive]]; no separate anime copyright tag was found in the local database.",
            "[h2]Characters[/h2]",
            "See [[List_of_Princess_Connect!_characters]] for the cast grouped by guild.",
            "[h2]Tagging notes[/h2]",
            "Character tags use the suffix _(princess_connect!). Seasonal costumes and alternate versions may have additional qualified tags; use those only when the depicted design matches.",
            "[h2]External links[/h2]",
            "* Official Re:Dive site: https://priconne-redive.jp/",
            "* Official Cygames game page: https://cygames.com/games/priconne/",
        ],
        "princess_connect!_re:dive": [
            "[b]Princess Connect! Re:Dive[/b] is an anime-style role-playing game developed by Cygames and released in Japan on February 15, 2018. It is a sequel to the original [[princess_connect!]].",
            "",
            "After the original story's failed conclusion, the amnesiac [[yuuki_(princess_connect!)]] awakens in Astraea. Guided by [[kokkoro_(princess_connect!)]], he joins [[pecorine_(princess_connect!)]] and [[karyl_(princess_connect!)]] to form the food-seeking Gourmet Guild. The narrative later expands across numerous Landosol guilds and regions beyond Astraea.",
            "[h2]Game[/h2]",
            "Re:Dive combines character collection, real-time party battles, voiced story chapters and extensive animated sequences. The Japanese version remains the primary ongoing release. The separately operated English/global version ended service in 2023; this did not end the Japanese game.",
            "[h2]Television anime[/h2]",
            "CygamesPictures produced a television adaptation centered on Yuuki and the Gourmet Guild. Its first season began in April 2020 and its second season began in January 2022. Use [[princess_connect!_re:dive]] for anime artwork unless another applicable tag identifies the medium or design.",
            "[h2]Characters and guilds[/h2]",
            "See [[List_of_Princess_Connect!_characters]]. The list links base character tags and excludes seasonal, real-world, Princess, ceremonial and other alternate forms.",
            "[h2]Related tags[/h2]",
            "* [[princess_connect!]]",
            "* [[cygames]]",
            "[h2]External links[/h2]",
            "* Official game site: https://priconne-redive.jp/",
            "* Official character and guild directory: https://priconne-redive.jp/character/",
            "* Official anime site: https://anime.priconne-redive.jp/",
        ],
    }


def main() -> None:
    connection = sqlite3.connect(DB)
    missing: list[str] = []
    seen: set[str] = set()
    list_lines = [
        "[b]About this list:[/b]",
        "This index groups Princess Connect! characters by their principal guild or story role in Re:Dive. It uses established Gelbooru character tags from the local database. Seasonal costumes, alternate forms, collaboration guests and duplicate aliases are excluded.",
    ]
    for guild, tags in GUILDS.items():
        list_lines.append(f"[h2]{guild}[/h2]")
        for tag in tags:
            row = connection.execute("SELECT category FROM tags WHERE name=?", (tag,)).fetchone()
            if not row or row[0] != 4:
                missing.append(tag)
                continue
            if tag not in seen:
                list_lines.append(f"* [[{tag}]]")
                seen.add(tag)
    list_lines.extend([
        "[h2]See also[/h2]",
        "* [[princess_connect!]]",
        "* [[princess_connect!_re:dive]]",
        "* Official guild directory: https://priconne-redive.jp/character/",
    ])
    pages = build_pages()
    pages["List_of_Princess_Connect!_characters"] = list_lines
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    for tag, lines in pages.items():
        payload = {
            "tag": tag,
            "template": "general" if tag.startswith("List_of_") else "copyright",
            "source": compact("\n".join(lines)),
            "updated_at": stamp,
        }
        safe_name = tag.replace(":", "") + ".json"
        (OUT / safe_name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"drafts {len(pages)} | characters {len(seen)} | missing {missing}")


if __name__ == "__main__":
    main()
