from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Pokemon"
TAG = "pokemon_diamond/pearl/platinum"
FILENAME = "pokemon_diamond_pearl_platinum.json"

LINKS = [
    "pokemon", "nintendo_ds", "lucas_(pokemon)", "dawn_(pokemon)",
    "barry_(pokemon)", "professor_rowan", "roark_(pokemon)",
    "gardenia_(pokemon)", "maylene_(pokemon)", "crasher_wake",
    "fantina_(pokemon)", "byron_(pokemon)", "candice_(pokemon)",
    "volkner_(pokemon)", "aaron_(pokemon)", "bertha_(pokemon)",
    "flint_(pokemon)", "lucian_(pokemon)", "cynthia_(pokemon)",
    "team_galactic", "cyrus_(pokemon)", "mars_(pokemon)",
    "jupiter_(pokemon)", "saturn_(pokemon)", "charon_(pokemon)",
    "looker_(pokemon)", "turtwig", "chimchar", "piplup", "dialga",
    "palkia", "giratina", "uxie", "mesprit", "azelf", "heatran",
    "regigigas", "cresselia", "manaphy", "phione", "darkrai",
    "shaymin", "arceus", "pokemon_brilliant_diamond_and_shining_pearl",
    "pokemon_legends:_arceus", "pokemon_diamond_and_pearl_(anime)",
]


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def main() -> None:
    connection = sqlite3.connect(DB)
    missing = [tag for tag in [TAG, *LINKS] if not connection.execute(
        "SELECT 1 FROM tags WHERE name=?", (tag,)
    ).fetchone()]
    if missing:
        raise SystemExit(f"Missing local tags: {missing}")

    lines = [
        "[b]Pokemon Diamond Version, Pokemon Pearl Version and Pokemon Platinum Version[/b] are main-series [[pokemon]] role-playing games developed by Game Freak for [[nintendo_ds]]. Diamond and Pearl began the fourth generation in 2006 in Japan and 2007 internationally; Platinum is their expanded companion version, released in 2008 in Japan and 2009 internationally.",
        "",
        "The games take place in Sinnoh. The player travels across the region with the goal of challenging its Pokemon League while confronting Team Galactic. Diamond and Pearl focus respectively on Dialga and Palkia, while Platinum expands the story around Giratina, introduces the Distortion World and gives several characters updated designs.",
        "[h2]Player characters and allies[/h2]",
        "* [[lucas_(pokemon)]] - male player character, or Professor Rowan's assistant when not selected.",
        "* [[dawn_(pokemon)]] - female player character, or Professor Rowan's assistant when not selected.",
        "* [[barry_(pokemon)]] - the player's energetic childhood friend and rival.",
        "* [[professor_rowan]] - the regional Pokemon Professor.",
        "* [[looker_(pokemon)]] - International Police investigator prominent in Platinum.",
        "[h2]Gym Leaders[/h2]",
        "* [[roark_(pokemon)]] - Oreburgh Gym.",
        "* [[gardenia_(pokemon)]] - Eterna Gym.",
        "* [[maylene_(pokemon)]] - Veilstone Gym.",
        "* [[crasher_wake]] - Pastoria Gym.",
        "* [[fantina_(pokemon)]] - Hearthome Gym.",
        "* [[byron_(pokemon)]] - Canalave Gym.",
        "* [[candice_(pokemon)]] - Snowpoint Gym.",
        "* [[volkner_(pokemon)]] - Sunyshore Gym.",
        "",
        "The order of the middle Gym challenges differs in Platinum.",
        "[h2]Pokemon League[/h2]",
        "Elite Four:",
        "* [[aaron_(pokemon)]]",
        "* [[bertha_(pokemon)]]",
        "* [[flint_(pokemon)]]",
        "* [[lucian_(pokemon)]]",
        "",
        "Champion:",
        "* [[cynthia_(pokemon)]]",
        "[h2]Team Galactic[/h2]",
        "* [[team_galactic]]",
        "* [[cyrus_(pokemon)]] - leader of Team Galactic.",
        "* [[mars_(pokemon)]]",
        "* [[jupiter_(pokemon)]]",
        "* [[saturn_(pokemon)]]",
        "* [[charon_(pokemon)]] - scientist and commander introduced in Platinum.",
        "[h2]Notable Pokemon[/h2]",
        "First partners:",
        "* [[turtwig]]",
        "* [[chimchar]]",
        "* [[piplup]]",
        "",
        "Version mascots and central Legendary Pokemon:",
        "* [[dialga]] - Diamond mascot, associated with time.",
        "* [[palkia]] - Pearl mascot, associated with space.",
        "* [[giratina]] - Platinum mascot and ruler of the Distortion World.",
        "* [[uxie]], [[mesprit]] and [[azelf]] - the lake guardians.",
        "",
        "Other Legendary and Mythical Pokemon introduced in this generation:",
        "* [[heatran]]",
        "* [[regigigas]]",
        "* [[cresselia]]",
        "* [[manaphy]] and [[phione]]",
        "* [[darkrai]]",
        "* [[shaymin]]",
        "* [[arceus]]",
        "[h2]Version-specific elements[/h2]",
        "Platinum retains the same central cast and Sinnoh setting but revises the plot, character outfits, regional Pokemon distribution and Gym progression. It adds Looker's investigation, Charon's larger role, the Distortion World, Giratina's Origin Forme and the Sinnoh Battle Frontier.",
        "",
        "When the image clearly uses a Platinum-specific outfit, scene or design, this can be stated in the accompanying tags or description even though Gelbooru groups all three games under [[pokemon_diamond/pearl/platinum]].",
        "[h2]Related tags and disambiguation[/h2]",
        "* [[pokemon]] - parent franchise.",
        "* [[pokemon_brilliant_diamond_and_shining_pearl]] - Nintendo Switch remakes of Diamond and Pearl; use for remake-specific designs and content.",
        "* [[pokemon_legends:_arceus]] - a separate game set in ancient Hisui, the historical Sinnoh region.",
        "* [[pokemon_diamond_and_pearl_(anime)]] - animated adaptation and its associated designs.",
        "",
        "Use [[pokemon_diamond/pearl/platinum]] for the original Nintendo DS games and their identifiable characters, outfits or settings. Do not add it solely because a Generation IV Pokemon appears in an unrelated context.",
        "[h2]External links[/h2]",
        "* Nintendo - Pokemon Platinum Version: https://www.nintendo.com/en-gb/Games/Nintendo-DS/Pokemon-Platinum-Version-272321.html",
        "* Nintendo - Pokemon Diamond and Pearl launch information: https://www.nintendo.com/en-gb/News/2007/Treasure-trove--250170.html",
        "* Official Pokemon Brilliant Diamond and Shining Pearl website: https://diamondpearl.pokemon.com/en-us/",
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "tag": TAG,
        "template": "copyright",
        "source": compact("\n".join(lines)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    destination = OUT / FILENAME
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {destination} | links {len(LINKS)}")


if __name__ == "__main__":
    main()
