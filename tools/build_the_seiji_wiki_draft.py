from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Artists"
TAG = "the_seiji"

COPYRIGHT_TAGS = [
    "hakoiri_musume",
    "deep_zone",
    "karin_to.",
    "tennin_kyoushi",
    "red-zone",
    "h_na_karadatte_iwanaide",
    "yuuhei_(game)",
    "nama_musume",
]


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def main() -> None:
    expected = [TAG, *COPYRIGHT_TAGS]
    with sqlite3.connect(DB) as connection:
        rows = connection.execute(
            "SELECT name, post_count, category FROM tags WHERE name IN ({})".format(
                ",".join("?" for _ in expected)
            ),
            expected,
        ).fetchall()
    found = {name: (post_count, category) for name, post_count, category in rows}
    missing = [tag for tag in expected if tag not in found]
    if missing:
        raise SystemExit(f"Missing local tags: {missing}")
    if found[TAG][1] != 1:
        raise SystemExit(f"{TAG} is not an artist tag: category={found[TAG][1]}")
    wrong_copyrights = [tag for tag in COPYRIGHT_TAGS if found[tag][1] != 3]
    if wrong_copyrights:
        raise SystemExit(f"Unexpected copyright categories: {wrong_copyrights}")

    lines = [
        "[b]THE SEIJI[/b] is a Japanese manga artist and illustrator. He debuted in 1988 under the pen name Aozora Midori (青空みどり) and later resumed his career under the name THE SEIJI. His work includes general-audience manga, adult manga and illustrations or production work for adult games.",
        "[h2]Name[/h2]",
        "* Japanese: THE SEIJI / ザ・セイジ",
        "* Former pen name: Aozora Midori (青空みどり)",
        "[h2]Works represented on Gelbooru[/h2]",
        "Posts under [[the_seiji]] are primarily associated with:",
        "* [[hakoiri_musume]]",
        "* [[deep_zone]]",
        "",
        "Other represented copyrights include:",
        "* [[karin_to.]]",
        "* [[tennin_kyoushi]]",
        "* [[red-zone]]",
        "* [[h_na_karadatte_iwanaide]]",
        "* [[yuuhei_(game)]]",
        "* [[nama_musume]]",
        "[h2]Tagging notes[/h2]",
        "Use [[the_seiji]] for artwork created by THE SEIJI. Do not confuse this artist with [[yoshida_seiji]], [[hirasawa_seiji]], [[kikuchi_seiji]], [[seiji_(artist)]] or other artists and characters whose names contain 'Seiji'.",
        "[h2]External links[/h2]",
        "* Official website: https://the-seiji-hp.jimdosite.com/",
        "* Official profile: https://the-seiji-hp.jimdosite.com/cv/",
        "* Instagram: https://www.instagram.com/theseiji2/",
    ]

    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / "the_seiji.json"
    payload = {
        "tag": TAG,
        "template": "artist",
        "source": compact("\n".join(lines)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {destination} | artist posts {found[TAG][0]} | "
        f"copyrights {len(COPYRIGHT_TAGS)}"
    )


if __name__ == "__main__":
    main()
