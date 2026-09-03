from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "var" / "wiki_drafts" / "List_of_Umamusume_characters.json"
DB = ROOT / "data" / "databases" / "g_tags_260810.db"
COPYRIGHT = "umamusume"


UMAMUSUME = """
Special Week|Silence Suzuka|Tokai Teio|Maruzensky|Fuji Kiseki|Oguri Cap|Gold Ship|Vodka|Daiwa Scarlet|Taiki Shuttle|Grass Wonder|Hishi Amazon|Mejiro McQueen|El Condor Pasa|T.M. Opera O|Narita Brian|Symboli Rudolf|Air Groove|Agnes Digital|Seiun Sky|Tamamo Cross|Fine Motion|Biwa Hayahide|Mayano Top Gun|Manhattan Cafe|Mihono Bourbon|Mejiro Ryan|Hishi Akebono|Yukino Bijin|Rice Shower|Ines Fujin|Agnes Tachyon|Admire Vega|Inari One|Winning Ticket|Air Shakur|Eishin Flash|Curren Chan|Kawakami Princess|Gold City|Sakura Bakushin O|Seeking the Pearl|Shinko Windy|Sweep Tosho|Super Creek|Smart Falcon|Zenno Rob Roy|Tosen Jordan|Nakayama Festa|Narita Taishin|Nishino Flower|Haru Urara|Bamboo Memory|Biko Pegasus|Marvelous Sunday|Matikanefukukitaru|Mr. C.B.|Meisho Doto|Mejiro Dober|Nice Nature|King Halo|Matikanetannhauser|Ikuno Dictus|Mejiro Palmer|Daitaku Helios|Twin Turbo|Satono Diamond|Kitasan Black|Sakura Chiyono O|Sirius Symboli|Mejiro Ardan|Yaeno Muteki|Tsurumaru Tsuyoshi|Mejiro Bright|Daring Tact|Sakura Laurel|Narita Top Road|Yamanin Zephyr|Furioso|Transcend|Espoir City|North Flight|Symboli Kris S|Tanino Gimlet|Daiichi Ruby|Mejiro Ramonu|Aston Machan|Satono Crown|Cheval Grand|Verxina|Vivlos|Dantsu Flame|K.S.Miracle|Jungle Pocket|Believe|No Reason|Still in Love|Copano Rickey|Hokko Tarumae|Wonder Acute|Samson Big|Sounds of Earth|Royce and Royce|Katsuragi Ace|Neo Universe|Hishi Miracle|Tap Dance City|Duramente|Rhein Kraft|Cesario|Air Messiah|Daring Heart|Fusaichi Pandora|Buena Vista|Orfevre|Gentildonna|Win Variation|Admire Groove|Dream Journey|Calstone Light O|Durandal|Bubble Gum Fellow|Sakura Chitose O|Fenomeno|Blast Onepiece|Almond Eye|Lucky Lilac|Gran Alegria|Loves Only You|Chrono Genesis|Curren Bouquetd'or|Stay Gold|Red Desire|Kiseki|Forever Young|Marche Lorraine|Epiphaneia|Logotype|Victoire Pisa|Rose Kingdom|Rulership|Titleholder
""".strip().split("|")

GAME_ORIGINAL_UMA = [
    "Happy Meek", "Bitter Glasse", "Little Cocon", "Venus Paques", "Rigantona", "Sonon Elfie",
    "Darley Arabian", "Godolphin Barb", "Byerley Turk", "Saint Lite", "Speed Symboli", "Haiseiko",
    "Yunohana Bloom", "Casino Drive",
]

GAME_STAFF = [
    "Hayakawa Tazuna", "Akikawa Yayoi", "Otonashi Etsuko", "Kiryuin Aoi", "Anshinzawa Sasami",
    "Kashimoto Riko", "Light Hello", "Satake Mei", "Tsurugi Ryoka", "Sugar Lights", "Tucker Bryne",
    "Hoshina Kiyoko", "Akasaka Misato", "Hosoe Junko",
]


OVERRIDES = {
    "T.M. Opera O": "t.m._opera_o_(umamusume)",
    "Mr. C.B.": "mr._c.b._(umamusume)",
    "K.S.Miracle": "k.s.miracle_(umamusume)",
    "Curren Bouquetd'or": "curren_bouquetd'or_(umamusume)",
    "Kiryuin Aoi": "kiryuuin_aoi_(umamusume)",
    "Hayakawa Tazuna": "hayakawa_tazuna",
    "Otonashi Etsuko": "otonashi_etsuko",
    "Anshinzawa Sasami": "anshinzawa_sasami",
    "Kashimoto Riko": "kashimoto_riko",
    "Satake Mei": "satake_mei",
    "Tsurugi Ryoka": "tsurugi_ryoka",
    "Hosoe Junko": "hosoe_junko_(umamusume)",
}


def slug(name: str) -> str:
    base = name.lower().replace("'", "'")
    base = re.sub(r"[^a-z0-9'.]+", "_", base).strip("_")
    return f"{base}_(umamusume)"


def tag_for(name: str) -> str:
    return OVERRIDES.get(name, slug(name))


def decorated(tag: str, count: int | None, valid: bool) -> str:
    link = f"[[{tag}]]"
    if not valid:
        return link + " [proposed]"
    if count is not None and count >= 10_000:
        return f"[b]{link}[/b]"
    if count is not None and count >= 1_000:
        return f"[i]{link}[/i]"
    if count is not None and count < 25:
        return link + "**"
    if count is not None and count < 50:
        return link + "*"
    return link


def alphabet_groups(names: list[str]) -> list[tuple[str, list[str]]]:
    buckets: dict[str, list[str]] = {}
    for name in sorted(names, key=str.casefold):
        letter = name[0].upper()
        buckets.setdefault(letter, []).append(name)
    return list(buckets.items())


def main() -> None:
    con = sqlite3.connect(DB)
    rows = {
        name: (count, category)
        for name, count, category in con.execute("SELECT name, post_count, category FROM tags")
    }

    all_names = UMAMUSUME + GAME_ORIGINAL_UMA + GAME_STAFF
    resolved: dict[str, tuple[str, int | None, bool]] = {}
    for display_name in all_names:
        tag = tag_for(display_name)
        row = rows.get(tag)
        resolved[display_name] = (tag, row[0] if row else None, bool(row and row[1] == 4))

    lines = [
        "[b]About this list:[/b]",
        "This page lists the named characters presented by the official Uma Musume Pretty Derby portal for the mobile game. The main roster includes playable and officially announced horse girls; game-original rivals, scenario characters and staff are listed separately.",
        "",
        "Names use their official Latin spelling when available, while links use Gelbooru's established character tags. The label [proposed] marks a link that is absent from the validated local database or is not currently category 4.",
        "",
        "[b]Popularity legend:[/b]",
        "* [b][[tag]][/b]: 10,000 images or more",
        "* [i][[tag]][/i]: 1,000 to 9,999 images",
        "* [[tag]]: 50 to 999 images",
        "* [[tag]]*: 25 to 49 images",
        "* [[tag]]**: fewer than 25 images",
        "* [[tag]] [proposed]: proposed or currently unvalidated character tag",
        "",
        "The symbols describe only the local Gelbooru database snapshot, not a character's importance or current playability.",
        "[h2]Official Umamusume roster[/h2]",
    ]

    for letter, names in alphabet_groups(UMAMUSUME):
        lines.append(f"[h3]{letter}[/h3]" + "\n".join(
            f"* {decorated(*resolved[name])}" for name in names
        ))

    lines.append("[h2]Game-original and scenario Umamusume[/h2]" + "\n".join(
        f"* {decorated(*resolved[name])}" for name in GAME_ORIGINAL_UMA
    ))
    lines.append("[h2]Staff and other game characters[/h2]" + "\n".join(
        f"* {decorated(*resolved[name])}" for name in GAME_STAFF
    ))
    lines.extend([
        "[h2]See also[/h2]",
        "* [[umamusume]]",
        "[h2]External sources[/h2]",
        "* https://umamusume.jp/character/",
        "* https://umamusume.com/",
    ])

    source = "\n".join(lines)
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    source = re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)
    payload = {
        "tag": "List_of_Umamusume_characters",
        "template": "general",
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    invalid = [(name, *resolved[name]) for name in all_names if not resolved[name][2]]
    print(f"Wrote {OUT}: {len(UMAMUSUME)} official Umamusume + {len(GAME_ORIGINAL_UMA)} scenario Umamusume + {len(GAME_STAFF)} staff/other characters")
    print(f"Validated category-4 tags: {len(all_names) - len(invalid)}; proposed/unvalidated: {len(invalid)}")
    for item in invalid:
        print("UNVALIDATED", item)


if __name__ == "__main__":
    main()
