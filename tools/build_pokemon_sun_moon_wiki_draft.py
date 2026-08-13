from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Pokemon"
TAG = "pokemon_sun_and_moon"

LINKS = [
    "pokemon", "nintendo_3ds", "pokemon_ultra_sun_and_ultra_moon",
    "elio_(pokemon)", "selene_(pokemon)", "hau_(pokemon)", "lillie_(pokemon)",
    "gladion_(pokemon)", "professor_kukui", "professor_burnet",
    "lusamine_(pokemon)", "wicke_(pokemon)", "faba_(pokemon)", "guzma_(pokemon)",
    "plumeria_(pokemon)", "ilima_(pokemon)", "lana_(pokemon)", "kiawe_(pokemon)",
    "mallow_(pokemon)", "sophocles_(pokemon)", "acerola_(pokemon)",
    "mina_(pokemon)", "hala_(pokemon)", "olivia_(pokemon)", "nanu_(pokemon)",
    "hapu_(pokemon)", "rowlet", "litten", "popplio", "cosmog", "solgaleo",
    "lunala", "necrozma", "tapu_koko", "tapu_lele", "tapu_bulu", "tapu_fini",
    "team_skull", "aether_foundation", "alolan_form", "ultra_beast", "z-move",
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
        "[b]Pokémon Sun and Moon[/b] are paired main-series [[pokemon]] role-playing games developed by Game Freak for [[nintendo_3ds]]. They were released in November 2016 and introduce the tropical Alola region.",
        "",
        "After moving to Alola, the player undertakes the island challenge: trials supervised by captains followed by grand trials against each island's kahuna. The story involves [[lillie_(pokemon)]], the mysterious Pokémon Cosmog, Team Skull and the Aether Foundation. The games also introduce regional forms, Ultra Beasts and Z-Moves.",
        "[h2]Player characters and companions[/h2]",
        "* [[elio_(pokemon)]] - male player character.",
        "* [[selene_(pokemon)]] - female player character.",
        "* [[hau_(pokemon)]]",
        "* [[lillie_(pokemon)]]",
        "* [[gladion_(pokemon)]]",
        "* [[professor_kukui]]",
        "* [[professor_burnet]]",
        "[h2]Aether Foundation and Team Skull[/h2]",
        "* [[lusamine_(pokemon)]]",
        "* [[wicke_(pokemon)]]",
        "* [[faba_(pokemon)]]",
        "* [[guzma_(pokemon)]]",
        "* [[plumeria_(pokemon)]]",
        "[h2]Trial Captains[/h2]",
        "* [[ilima_(pokemon)]]",
        "* [[lana_(pokemon)]]",
        "* [[kiawe_(pokemon)]]",
        "* [[mallow_(pokemon)]]",
        "* [[sophocles_(pokemon)]]",
        "* [[acerola_(pokemon)]]",
        "* [[mina_(pokemon)]]",
        "[h2]Island Kahunas[/h2]",
        "* [[hala_(pokemon)]]",
        "* [[olivia_(pokemon)]]",
        "* [[nanu_(pokemon)]]",
        "* [[hapu_(pokemon)]]",
        "[h2]Notable Pokémon[/h2]",
        "First partners:",
        "* [[rowlet]]",
        "* [[litten]]",
        "* [[popplio]]",
        "",
        "Story and legendary Pokémon:",
        "* [[cosmog]]",
        "* [[solgaleo]]",
        "* [[lunala]]",
        "* [[necrozma]]",
        "",
        "Island guardians:",
        "* [[tapu_koko]]",
        "* [[tapu_lele]]",
        "* [[tapu_bulu]]",
        "* [[tapu_fini]]",
        "[h2]Related tags[/h2]",
        "* [[pokemon]]",
        "* [[pokemon_ultra_sun_and_ultra_moon]] - expanded alternate versions released in 2017.",
        "* [[team_skull]]",
        "* [[aether_foundation]]",
        "* [[alolan_form]]",
        "* [[ultra_beast]]",
        "* [[z-move]]",
        "[h2]External links[/h2]",
        "* Official Nintendo overview: https://www.nintendo.com/en-gb/Games/Nintendo-3DS-games/Pokemon-Sun-1092368.html",
        "* Official Pokémon press release: https://press.pokemon.com/en/releases/NEW-POKEMON-REVEALED-FOR-POKEMON-SUN-AND-POKEMON-MOON-84523",
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
