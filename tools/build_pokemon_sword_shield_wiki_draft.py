from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Pokemon"
TAG = "pokemon_sword_and_shield"

LINKS = [
    "pokemon", "victor_(pokemon)", "gloria_(pokemon)", "hop_(pokemon)",
    "leon_(pokemon)", "marnie_(pokemon)", "bede_(pokemon)", "sonia_(pokemon)",
    "magnolia_(pokemon)", "rose_(pokemon)", "oleana_(pokemon)", "milo_(pokemon)",
    "nessa_(pokemon)", "kabu_(pokemon)", "bea_(pokemon)", "allister_(pokemon)",
    "opal_(pokemon)", "gordie_(pokemon)", "melony_(pokemon)", "piers_(pokemon)",
    "raihan_(pokemon)", "ball_guy", "klara_(pokemon)", "avery_(pokemon)",
    "mustard_(pokemon)", "peony_(pokemon)", "sordward_(pokemon)",
    "shielbert_(pokemon)", "grookey", "scorbunny", "sobble", "zacian",
    "zamazenta", "eternatus", "kubfu", "urshifu", "calyrex", "dynamax",
    "gigantamax", "nintendo_switch",
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
        "[b]Pokémon Sword and Shield[/b] are the paired main-series [[pokemon]] role-playing games developed by Game Freak and released worldwide for [[nintendo_switch]] on November 15, 2019.",
        "",
        "The games introduce the Galar region, where the player travels to challenge its stadium-based Pokémon League. Their story centers on the Gym Challenge, Champion [[leon_(pokemon)]], the energy crisis caused by Chairman [[rose_(pokemon)]], and the legendary Pokémon associated with the Darkest Day.",
        "[h2]Player characters and rivals[/h2]",
        "* [[victor_(pokemon)]] - male player character.",
        "* [[gloria_(pokemon)]] - female player character.",
        "* [[hop_(pokemon)]]",
        "* [[marnie_(pokemon)]]",
        "* [[bede_(pokemon)]]",
        "[h2]Major characters[/h2]",
        "* [[leon_(pokemon)]]",
        "* [[sonia_(pokemon)]]",
        "* [[magnolia_(pokemon)]]",
        "* [[rose_(pokemon)]]",
        "* [[oleana_(pokemon)]]",
        "* [[ball_guy]]",
        "[h2]Gym Leaders[/h2]",
        "* [[milo_(pokemon)]]",
        "* [[nessa_(pokemon)]]",
        "* [[kabu_(pokemon)]]",
        "* [[bea_(pokemon)]] - exclusive to Sword as a Gym Leader.",
        "* [[allister_(pokemon)]] - exclusive to Shield as a Gym Leader.",
        "* [[opal_(pokemon)]]",
        "* [[gordie_(pokemon)]] - exclusive to Sword as a Gym Leader.",
        "* [[melony_(pokemon)]] - exclusive to Shield as a Gym Leader.",
        "* [[piers_(pokemon)]]",
        "* [[raihan_(pokemon)]]",
        "[h2]Expansion Pass[/h2]",
        "The Expansion Pass adds two adventures: The Isle of Armor and The Crown Tundra.",
        "",
        "Isle of Armor characters:",
        "* [[mustard_(pokemon)]]",
        "* [[klara_(pokemon)]] - Sword.",
        "* [[avery_(pokemon)]] - Shield.",
        "",
        "Crown Tundra characters:",
        "* [[peony_(pokemon)]]",
        "",
        "Post-game characters:",
        "* [[sordward_(pokemon)]]",
        "* [[shielbert_(pokemon)]]",
        "[h2]Notable Pokémon[/h2]",
        "First partners:",
        "* [[grookey]]",
        "* [[scorbunny]]",
        "* [[sobble]]",
        "",
        "Legendary and expansion Pokémon:",
        "* [[zacian]]",
        "* [[zamazenta]]",
        "* [[eternatus]]",
        "* [[kubfu]]",
        "* [[urshifu]]",
        "* [[calyrex]]",
        "[h2]Related tags[/h2]",
        "* [[pokemon]]",
        "* [[dynamax]]",
        "* [[gigantamax]]",
        "[h2]External links[/h2]",
        "* Official website: https://swordshield.pokemon.com/en-us/",
        "* Official Expansion Pass overview: https://swordshield.pokemon.com/en-us/expansionpass/",
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
