from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "databases" / "g_tags_260810.db"
OUT = ROOT / "var" / "wiki_drafts" / "Wuthering_Waves"


GROUPS: dict[str, list[str]] = {
    "Rover and companions": [
        "rover_(wuthering_waves)", "female_rover_(wuthering_waves)",
        "male_rover_(wuthering_waves)", "abby_(wuthering_waves)",
    ],
    "Huanglong and Jinzhou": [
        "yangyang_(wuthering_waves)", "chixia_(wuthering_waves)",
        "baizhi_(wuthering_waves)", "jiyan_(wuthering_waves)",
        "jinhsi_(wuthering_waves)", "changli_(wuthering_waves)",
        "sanhua_(wuthering_waves)", "mortefi_(wuthering_waves)",
        "jianxin_(wuthering_waves)", "lingyang_(wuthering_waves)",
        "danjin_(wuthering_waves)", "taoqi_(wuthering_waves)",
        "yuanwu_(wuthering_waves)", "yinlin_(wuthering_waves)",
        "zhezhi_(wuthering_waves)", "xiangli_yao_(wuthering_waves)",
        "youhu_(wuthering_waves)", "lumi_(wuthering_waves)",
        "calcharo_(wuthering_waves)", "verina_(wuthering_waves)",
        "jue_(wuthering_waves)", "geshu_lin_(wuthering_waves)",
    ],
    "Black Shores and Fractsidus": [
        "shorekeeper_(wuthering_waves)", "camellya_(wuthering_waves)",
        "encore_(wuthering_waves)", "aalto_(wuthering_waves)",
        "scar_(wuthering_waves)", "phrolova_(wuthering_waves)",
    ],
    "Rinascita": [
        "carlotta_(wuthering_waves)", "roccia_(wuthering_waves)",
        "phoebe_(wuthering_waves)", "brant_(wuthering_waves)",
        "cantarella_(wuthering_waves)", "cartethyia_(wuthering_waves)",
        "fleurdelys_(wuthering_waves)", "ciaccona_(wuthering_waves)",
        "zani_(wuthering_waves)", "lupa_(wuthering_waves)",
        "augusta_(wuthering_waves)", "iuno_(wuthering_waves)",
        "galbrena_(wuthering_waves)",
    ],
    "Later regions and story chapters": [
        "chisa_(wuthering_waves)", "aemeath_(wuthering_waves)",
        "mornye_(wuthering_waves)", "hiyuki_(wuthering_waves)",
        "denia_(wuthering_waves)", "lynae_(wuthering_waves)",
        "sigrika_(wuthering_waves)", "lucilla_(wuthering_waves)",
        "luuk_herssen_(wuthering_waves)", "qiuyuan_(wuthering_waves)",
        "suisui_(wuthering_waves)", "buling_(wuthering_waves)",
    ],
}


PGR = [
    "lucia_(punishing:_gray_raven)", "liv_(punishing:_gray_raven)",
    "lee_(punishing:_gray_raven)", "commandant_(punishing:_gray_raven)",
    "alpha_(punishing:_gray_raven)", "karenina_(punishing:_gray_raven)",
    "nanami_(punishing:_gray_raven)", "bianca_(punishing:_gray_raven)",
    "vera_(punishing:_gray_raven)", "no.21_(punishing:_gray_raven)",
    "selena_(punishing:_gray_raven)", "luna_(punishing:_gray_raven)",
    "lamia_(punishing:_gray_raven)", "rosetta_(punishing:_gray_raven)",
    "qu_(punishing:_gray_raven)", "watanabe_(punishing:_gray_raven)",
    "chrome_(punishing:_gray_raven)", "kamui_(punishing:_gray_raven)",
]


def bullets(tags: list[str]) -> str:
    return "\n".join(f"* [[{tag}]]" for tag in tags)


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def main() -> None:
    con = sqlite3.connect(DB)
    rows = {
        name: (post_count, category)
        for name, post_count, category in con.execute(
            "SELECT name, post_count, category FROM tags"
        )
    }
    resonators = [tag for tags in GROUPS.values() for tag in tags]
    invalid = [(tag, rows.get(tag)) for tag in resonators if not rows.get(tag) or rows[tag][1] != 4]
    if invalid:
        raise SystemExit(f"Invalid Wuthering Waves character tags: {invalid}")

    wuthering = [
        "[b]Wuthering Waves[/b] is a free-to-play, story-rich open-world action role-playing game developed and published by [[kuro_games]]. It launched globally in May 2024. The player awakens as the amnesiac Rover in the post-apocalyptic world of Solaris-3 and travels with people known as Resonators while confronting Tacet Discords and recovering their identity.",
        "",
        "Wuthering Waves is its own game property. It is not currently presented as part of the [[punishing:_gray_raven]] setting or a shared Kuro Games fictional universe; similarities in genre, terminology or character design do not establish a crossover continuity.",
        "[h2]Characters[/h2]",
        "The list favors playable Resonators and major recurring story characters. Form, outfit, weapon-armament and cosplay tags are omitted from this overview.",
    ]
    for title, tags in GROUPS.items():
        wuthering.append(f"[h3]{title}[/h3]" + bullets(tags))
    wuthering.extend([
        "[h2]Common setting tags[/h2]",
        "* [[tacet_mark_(wuthering_waves)]]",
        "* [[tacet_discord_(wuthering_waves)]]",
        "* [[pangu_terminal_(wuthering_waves)]]",
        "* [[fractsidus_(wuthering_waves)]]",
        "[h2]Tagging notes[/h2]",
        "* Use [[rover_(wuthering_waves)]] for the player role and add [[female_rover_(wuthering_waves)]] or [[male_rover_(wuthering_waves)]] when the selected body is identifiable.",
        "* Character forms, alternate outfits and named armaments supplement the base character tag rather than replacing it.",
        "* [[the_shorekeeper_(wuthering_waves)]] is an alias; use [[shorekeeper_(wuthering_waves)]].",
        "* Some newly introduced characters have duplicate or provisional spellings in the database. Prefer the established category-4 tag with active posts.",
        "[h2]Developer and related game[/h2]",
        "* [[kuro_games]]",
        "* [[punishing:_gray_raven]]",
        "[h2]External sources[/h2]",
        "* Official site: https://wutheringwaves.kurogames.com/",
        "* Kuro Games product page: https://www.kurogames.com/games",
        "* Official news: https://wutheringwaves.kurogames.com/en/main/news",
    ])

    kuro = [
        "[b]Kuro Games[/b] is a Chinese video game developer and publisher founded in 2014 and headquartered in Guangzhou. Its live-service games include [[punishing:_gray_raven]] and [[wuthering_waves]]. The company also develops related music, animation, merchandise and other IP projects.",
        "[h2]Games[/h2]",
        "* [[punishing:_gray_raven]] - post-apocalyptic 3D action role-playing game launched in China in 2019 and later globally.",
        "* [[wuthering_waves]] - open-world action role-playing game launched globally in 2024.",
        "* Twin Tail Battleground - an earlier mobile title; no exact local Gelbooru copyright tag was found.",
        "[h2]Tagging notes[/h2]",
        "* Use [[kuro_games]] for company logos, studio celebrations, cross-property portfolio material and company-focused promotional art.",
        "* For ordinary fan art, use the specific game copyright instead of adding the developer tag automatically.",
        "* Wuthering Waves and Punishing: Gray Raven are separate properties; do not infer that one is a sequel, spin-off or shared-universe branch of the other.",
        "[h2]External sources[/h2]",
        "* Official company profile: https://www.kurogames.com/introduction",
        "* Official games page: https://www.kurogames.com/games",
    ]

    pgr = [
        "[b]Punishing: Gray Raven[/b] is a free-to-play post-apocalyptic 3D action role-playing game developed by [[kuro_games]]. The player acts as the Commandant of Gray Raven, leading Constructs against machines corrupted by the Punishing Virus.",
        "",
        "It is a separate fictional property from [[wuthering_waves]], despite sharing a developer and some broad action-RPG themes.",
        "[h2]Principal characters[/h2]" + bullets(PGR),
        "[h2]Database note[/h2]",
        "Many established Punishing: Gray Raven character names are currently category 6 aliases in the local Gelbooru database. The links above deliberately use those existing canonical search names rather than inventing replacement character tags.",
        "[h2]Tagging notes[/h2]",
        "* Add the base character tag as well as frame, coating or form-specific tags when applicable.",
        "* Do not add [[wuthering_waves]] merely for visual similarities or Kuro Games crossover jokes.",
        "[h2]External sources[/h2]",
        "* Official site: https://pgr.kurogame.net/",
        "* Kuro Games product page: https://www.kurogames.com/games",
    ]

    pages = {
        "wuthering_waves": ("copyright", wuthering),
        "kuro_games": ("copyright", kuro),
        "punishing:_gray_raven": ("copyright", pgr),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for tag, (template, lines) in pages.items():
        if tag not in rows:
            raise SystemExit(f"Missing root tag: {tag}")
        payload = {
            "tag": tag,
            "template": template,
            "source": compact("\n".join(lines)),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        path = OUT / (tag.replace(":", "") + ".json")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Wrote", path)


if __name__ == "__main__":
    main()
