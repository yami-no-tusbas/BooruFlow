from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Sexfri Osananajimi"

EXPECTED = {
    "sexfri_osananajimi": 3,
    "orcsoft": 3,
    "akihara_shiho": 4,
    "sumeragi_kohaku": 1,
}


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def payload(tag: str, template: str, lines: list[str]) -> dict[str, str]:
    return {
        "tag": tag,
        "template": template,
        "source": compact("\n".join(lines)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    with sqlite3.connect(DB) as connection:
        rows = connection.execute(
            "SELECT name, post_count, category FROM tags WHERE name IN ({})".format(
                ",".join("?" for _ in EXPECTED)
            ),
            list(EXPECTED),
        ).fetchall()
    found = {name: (post_count, category) for name, post_count, category in rows}
    missing = [tag for tag in EXPECTED if tag not in found]
    if missing:
        raise SystemExit(f"Missing local tags: {missing}")
    wrong = [tag for tag, category in EXPECTED.items() if found[tag][1] != category]
    if wrong:
        raise SystemExit(f"Unexpected tag categories: {wrong}")

    game = payload(
        "sexfri_osananajimi",
        "copyright",
        [
            "[b]S*x Friend Osananajimi[/b] (Japanese: セクフレ幼馴染 ～処女と童貞は恥ずかしいってみんなが言うから～; romanized: [i]Sekufure Osananajimi: Shojo to Doutei wa Hazukashii tte Minna ga Iu kara[/i]) is a Japanese adult visual novel developed by [[orcsoft]] and released on June 1, 2019.",
            "",
            "The story follows two childhood friends who, embarrassed by social pressure surrounding their inexperience, begin an intimate relationship during their school years without formally becoming lovers. Its central heroine is [[akihara_shiho]].",
            "[h2]Character[/h2]",
            "* [[akihara_shiho]] - the protagonist's childhood friend and the game's sole tagged heroine.",
            "[h2]Production[/h2]",
            "* Developer: [[orcsoft]]",
            "* Character design / original artwork: [[sumeragi_kohaku]]",
            "* Original release: June 1, 2019",
            "[h2]Adaptations and merchandise[/h2]",
            "The game received a one-episode adult animation adaptation titled [i]S*x Friend Osananajimi: Shojo to Doutei wa Hazukashii tte Minna ga Iu kara THE ANIMATION[/i], released on May 29, 2020. Shiho has also received several official scale figures produced by Q-six.",
            "[h2]Tagging notes[/h2]",
            "Use [[sexfri_osananajimi]] for material from the game and its animation adaptation. Add [[akihara_shiho]] when Shiho appears, along with the relevant artist tag. The Gelbooru spelling uses 'sexfri' even though the Japanese title is セクフレ (sekufure).",
            "[h2]External links[/h2]",
            "* ORCSOFT official website: https://www.orcsoft.jp/",
            "* ORCSOFT official merchandise page: https://www.orcsoft.jp/goods_other.html",
        ],
    )

    shiho = payload(
        "akihara_shiho",
        "character",
        [
            "[b]Shiho Akihara[/b] (秋原 志穂, Akihara Shiho) is the main heroine of [[sexfri_osananajimi]]. She is the unnamed male protagonist's longtime childhood friend.",
            "",
            "During their school years, Shiho and the protagonist are both inexperienced and conscious of the pressure placed on them by their peers. They agree to begin an intimate relationship while remaining less formally committed than lovers. The later animation adaptation frames their story through a reunion after many years apart.",
            "[h2]Appearance[/h2]",
            "Shiho is depicted as a curvy young woman with brown hair and green eyes. Her hairstyle changes during the story; official figures include interchangeable longer- and shorter-haired appearances.",
            "[h2]Credits[/h2]",
            "* Character design / original artwork: [[sumeragi_kohaku]]",
            "* Voice: Minami Imaya (今谷皆美)",
            "[h2]Tagging notes[/h2]",
            "Use [[akihara_shiho]] together with [[sexfri_osananajimi]]. Do not confuse her with real people who share the name Shiho Akihara or with other characters named Shiho.",
            "[h2]External links[/h2]",
            "* ORCSOFT official merchandise page: https://www.orcsoft.jp/goods_other.html",
            "* Q-six figure listing (AmiAmi): https://www.amiami.jp/top/detail/detail?gcode=FIGURE-139148-R",
        ],
    )

    OUT.mkdir(parents=True, exist_ok=True)
    drafts = {
        "sexfri_osananajimi.json": game,
        "akihara_shiho.json": shiho,
    }
    for filename, data in drafts.items():
        destination = OUT / filename
        destination.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {destination} | bytes {len(data['source'].encode('utf-8'))}")


if __name__ == "__main__":
    main()
