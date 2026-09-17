from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Pokemon"
TAG = "pokemon_black_and_white"

LINKS = [
    "pokemon", "nintendo_ds", "hilbert_(pokemon)", "hilda_(pokemon)",
    "cheren_(pokemon)", "bianca_(pokemon)", "professor_juniper",
    "cilan_(pokemon)", "chili_(pokemon)", "cress_(pokemon)",
    "lenora_(pokemon)", "burgh_(pokemon)", "elesa_(pokemon)",
    "clay_(pokemon)", "skyla_(pokemon)", "brycen_(pokemon)",
    "drayden_(pokemon)", "iris_(pokemon)", "shauntal_(pokemon)",
    "marshal_(pokemon)", "grimsley_(pokemon)", "caitlin_(pokemon)",
    "alder_(pokemon)", "n_(pokemon)", "ghetsis_(pokemon)", "team_plasma",
    "snivy", "tepig", "oshawott", "reshiram", "zekrom", "victini",
    "cobalion", "terrakion", "virizion", "tornadus", "thundurus",
    "landorus", "kyurem", "keldeo", "meloetta", "genesect",
    "pokemon_black_2_and_white_2", "pokemon_black_and_white_(anime)",
]


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def main() -> None:
    connection = sqlite3.connect(DB)
    missing = [tag for tag in LINKS if not connection.execute(
        "SELECT 1 FROM tags WHERE name=?", (tag,)
    ).fetchone()]
    if missing:
        raise SystemExit(f"Missing local tags: {missing}")

    lines = [
        "[b]Pokemon Black Version and Pokemon White Version[/b] are paired main-series [[pokemon]] role-playing games developed by Game Freak for [[nintendo_ds]]. They were first released in Japan in 2010 and internationally in 2011.",
        "",
        "The games introduce the Unova region and the fifth generation of Pokemon. The player travels with childhood friends Cheren and Bianca, challenges the Pokemon League and becomes involved in the conflict between Team Plasma's stated ideal of Pokemon liberation and Ghetsis's actual plans. N's role and the opposing Legendary Dragon are central to the story.",
        "[h2]Player characters and companions[/h2]",
        "* [[hilbert_(pokemon)]] - male player character.",
        "* [[hilda_(pokemon)]] - female player character.",
        "* [[cheren_(pokemon)]] - childhood friend and rival.",
        "* [[bianca_(pokemon)]] - childhood friend and rival.",
        "* [[professor_juniper]] - the regional Pokemon Professor.",
        "[h2]Gym Leaders[/h2]",
        "* [[cilan_(pokemon)]], [[chili_(pokemon)]] and [[cress_(pokemon)]] - Striaton Gym; the opponent depends on the player's first partner.",
        "* [[lenora_(pokemon)]] - Nacrene Gym.",
        "* [[burgh_(pokemon)]] - Castelia Gym.",
        "* [[elesa_(pokemon)]] - Nimbasa Gym.",
        "* [[clay_(pokemon)]] - Driftveil Gym.",
        "* [[skyla_(pokemon)]] - Mistralton Gym.",
        "* [[brycen_(pokemon)]] - Icirrus Gym.",
        "* [[drayden_(pokemon)]] - Opelucid Gym in Black.",
        "* [[iris_(pokemon)]] - Opelucid Gym in White.",
        "[h2]Pokemon League[/h2]",
        "Elite Four:",
        "* [[shauntal_(pokemon)]]",
        "* [[marshal_(pokemon)]]",
        "* [[grimsley_(pokemon)]]",
        "* [[caitlin_(pokemon)]]",
        "",
        "Champion:",
        "* [[alder_(pokemon)]]",
        "[h2]Team Plasma[/h2]",
        "* [[team_plasma]]",
        "* [[n_(pokemon)]] - the public king of Team Plasma, whose ideals drive much of the plot.",
        "* [[ghetsis_(pokemon)]] - one of the Seven Sages and the architect of Team Plasma's true plan.",
        "[h2]Notable Pokemon[/h2]",
        "First partners:",
        "* [[snivy]]",
        "* [[tepig]]",
        "* [[oshawott]]",
        "",
        "Version mascots and story Pokemon:",
        "* [[reshiram]] - mascot and obtainable story dragon in Black.",
        "* [[zekrom]] - mascot and obtainable story dragon in White.",
        "* [[victini]]",
        "",
        "Other Legendary and Mythical Pokemon introduced in this generation:",
        "* [[cobalion]], [[terrakion]] and [[virizion]]",
        "* [[tornadus]], [[thundurus]] and [[landorus]]",
        "* [[kyurem]]",
        "* [[keldeo]]",
        "* [[meloetta]]",
        "* [[genesect]]",
        "[h2]Related tags and disambiguation[/h2]",
        "* [[pokemon]] - parent franchise.",
        "* [[pokemon_black_2_and_white_2]] - direct sequels set in Unova two years later; use this for sequel-specific characters, outfits and settings.",
        "* [[pokemon_black_and_white_(anime)]] - the corresponding anime era; use it when the work is specifically based on the animated adaptation.",
        "",
        "Use [[pokemon_black_and_white]] for the original games and their identifiable designs. Do not use it solely because a Pokemon introduced in Generation V appears in an unrelated context.",
        "[h2]External links[/h2]",
        "* Official Pokemon game overview: https://www.pokemon.com/uk/pokemon-video-games/pokemon-black-version-and-pokemon-white-version",
        "* Nintendo game overview: https://www.nintendo.com/en-gb/Games/Nintendo-DS/Pokemon-Black-Version-272332.html",
        "* Iwata Asks - Pokemon Black Version and Pokemon White Version: https://www.nintendo.com/en-gb/Iwata-Asks/Iwata-Asks-Pokemon-Black-Version-and-Pokemon-White-Version/Pokemon-Black-Version-and-Pokemon-White-Version/1-Making-a-Completely-New-Sequel-for-the-Nintendo-DS/1-Making-a-Completely-New-Sequel-for-the-Nintendo-DS-209957.html",
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
    print(f"wrote {destination} | links {len(LINKS)}")


if __name__ == "__main__":
    main()
