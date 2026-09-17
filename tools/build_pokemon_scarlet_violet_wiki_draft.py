from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Pokemon"
TAG = "pokemon_scarlet_and_violet"

LINKS = [
    "pokemon", "nintendo_switch", "florian_(pokemon)", "juliana_(pokemon)",
    "nemona_(pokemon)", "arven_(pokemon)", "penny_(pokemon)", "clavell_(pokemon)",
    "jacq_(pokemon)", "sada_(pokemon)", "turo_(pokemon)", "katy_(pokemon)",
    "brassius_(pokemon)", "iono_(pokemon)", "kofu_(pokemon)", "larry_(pokemon)",
    "ryme_(pokemon)", "tulip_(pokemon)", "grusha_(pokemon)", "rika_(pokemon)",
    "poppy_(pokemon)", "hassel_(pokemon)", "geeta_(pokemon)", "giacomo_(pokemon)",
    "mela_(pokemon)", "atticus_(pokemon)", "ortega_(pokemon)", "eri_(pokemon)",
    "carmine_(pokemon)", "kieran_(pokemon)", "briar_(pokemon)", "perrin_(pokemon)",
    "lacey_(pokemon)", "crispin_(pokemon)", "amarys_(pokemon)", "drayton_(pokemon)",
    "sprigatito", "fuecoco", "quaxly", "koraidon", "miraidon", "ogerpon",
    "okidogi", "munkidori", "fezandipiti", "terapagos", "pecharunt",
    "team_star", "terastallization",
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
        "[b]Pokémon Scarlet and Violet[/b] are paired main-series [[pokemon]] role-playing games developed by Game Freak and released worldwide for [[nintendo_switch]] on November 18, 2022.",
        "",
        "The games are set in the open-world Paldea region. As a student taking part in the academy's Treasure Hunt, the player can pursue three intertwined routes in any order: Victory Road, Path of Legends and Starfall Street. Their stories ultimately converge in Area Zero and differ in several details between Scarlet and Violet.",
        "[h2]Player characters and central companions[/h2]",
        "* [[florian_(pokemon)]] - male player character.",
        "* [[juliana_(pokemon)]] - female player character.",
        "* [[nemona_(pokemon)]] - guide and battle rival for Victory Road.",
        "* [[arven_(pokemon)]] - companion for Path of Legends.",
        "* [[penny_(pokemon)]] - central character in Starfall Street.",
        "* [[clavell_(pokemon)]]",
        "* [[jacq_(pokemon)]]",
        "* [[sada_(pokemon)]] - professor associated with Scarlet.",
        "* [[turo_(pokemon)]] - professor associated with Violet.",
        "[h2]Gym Leaders and Pokémon League[/h2]",
        "Gym Leaders:",
        "* [[katy_(pokemon)]]",
        "* [[brassius_(pokemon)]]",
        "* [[iono_(pokemon)]]",
        "* [[kofu_(pokemon)]]",
        "* [[larry_(pokemon)]]",
        "* [[ryme_(pokemon)]]",
        "* [[tulip_(pokemon)]]",
        "* [[grusha_(pokemon)]]",
        "",
        "Elite Four and leadership:",
        "* [[rika_(pokemon)]]",
        "* [[poppy_(pokemon)]]",
        "* [[larry_(pokemon)]]",
        "* [[hassel_(pokemon)]]",
        "* [[geeta_(pokemon)]]",
        "[h2]Team Star[/h2]",
        "* [[giacomo_(pokemon)]]",
        "* [[mela_(pokemon)]]",
        "* [[atticus_(pokemon)]]",
        "* [[ortega_(pokemon)]]",
        "* [[eri_(pokemon)]]",
        "[h2]The Hidden Treasure of Area Zero[/h2]",
        "The paid expansion comprises Part 1: The Teal Mask, set in Kitakami, and Part 2: The Indigo Disk, set primarily at Blueberry Academy. Its story continues into a downloadable epilogue.",
        "",
        "Major DLC characters:",
        "* [[carmine_(pokemon)]]",
        "* [[kieran_(pokemon)]]",
        "* [[briar_(pokemon)]]",
        "* [[perrin_(pokemon)]]",
        "* [[lacey_(pokemon)]]",
        "* [[crispin_(pokemon)]]",
        "* [[amarys_(pokemon)]]",
        "* [[drayton_(pokemon)]]",
        "[h2]Notable Pokémon[/h2]",
        "First partners:",
        "* [[sprigatito]]",
        "* [[fuecoco]]",
        "* [[quaxly]]",
        "",
        "Version mascots and central Pokémon:",
        "* [[koraidon]] - Scarlet.",
        "* [[miraidon]] - Violet.",
        "",
        "DLC Pokémon:",
        "* [[ogerpon]]",
        "* [[okidogi]]",
        "* [[munkidori]]",
        "* [[fezandipiti]]",
        "* [[terapagos]]",
        "* [[pecharunt]]",
        "[h2]Related tags[/h2]",
        "* [[pokemon]]",
        "* [[team_star]]",
        "* [[terastallization]]",
        "[h2]External links[/h2]",
        "* Official Nintendo overview: https://www.nintendo.com/en-gb/Games/Nintendo-Switch-games/Pokemon-Scarlet-2179556.html",
        "* Official DLC information: https://en-americas-support.nintendo.com/app/answers/detail/a_id/61306/",
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
