from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Companies"
TAG = "arc_system_works"

OWNED_SERIES = [
    "guilty_gear", "blazblue", "battle_fantasia", "kunio-kun_series",
    "double_dragon",
]
PARTNER_DEVELOPMENT = [
    "dragon_ball_fighterz", "granblue_fantasy_versus",
    "granblue_fantasy_versus:_rising", "dnf_duel",
    "persona_4:_the_ultimate_in_mayonaka_arena", "hard_corps:_uprising",
]
PUBLISHING_PARTNERS = [
    "under_night_in-birth", "under_night_in-birth_2_sys:celes",
    "river_city_girls", "river_city_girls_2",
]
RELATED = [*OWNED_SERIES, *PARTNER_DEVELOPMENT, *PUBLISHING_PARTNERS]


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def bullets(tags: list[str]) -> list[str]:
    return [f"* [[{tag}]]" for tag in tags]


def main() -> None:
    with sqlite3.connect(DB) as connection:
        missing = [
            tag for tag in [TAG, *RELATED]
            if not connection.execute("SELECT 1 FROM tags WHERE name = ?", (tag,)).fetchone()
        ]
    if missing:
        raise SystemExit(f"Missing local tags: {missing}")

    lines = [
        "[b]Arc System Works[/b] is a Japanese video-game developer and publisher headquartered in Yokohama. It is particularly associated with 2D fighting games, but its catalogue also includes action, role-playing, adventure and licensed games.",
        "",
        "The company was founded in 1988 and adopted the Arc System Works name in 1991. It develops and owns the [[guilty_gear]] and [[blazblue]] franchises, works as a development partner on games owned by other companies, and publishes games created by outside studios. Consequently, an Arc System Works credit does not always mean that it owns or solely developed the depicted property.",
        "[h2]Principal company franchises[/h2]",
        "* [[guilty_gear]] - Arc System Works' long-running fighting-game franchise, created by Daisuke Ishiwatari.",
        "* [[blazblue]] - fighting-game and multimedia franchise created by Toshimichi Mori.",
        "* [[battle_fantasia]] - fantasy-themed fighting game developed and published by Arc System Works.",
        "[h2]Technos catalogue[/h2]",
        "Arc System Works acquired the intellectual-property rights formerly held by Technos Japan and Million in 2015. This catalogue includes:",
        "* [[kunio-kun_series]] - known internationally through titles including River City Ransom.",
        "* [[double_dragon]] - belt-scrolling action-game series.",
        "",
        "Individual later games may still be developed by partner studios. For example, the River City Girls games are developed by WayForward even though they use the Kunio-kun setting and are published by Arc System Works.",
        "[h2]Development and co-development for other owners[/h2]",
        "The following tagged games use properties owned or principally controlled by other companies. Arc System Works acted as developer or co-developer rather than becoming the owner of the underlying franchise:",
        *bullets(PARTNER_DEVELOPMENT),
        "[h2]Publishing and external development[/h2]",
        "Arc System Works also publishes games developed by partner studios. These relationships should not be described as sole Arc System Works development:",
        "* [[under_night_in-birth]] and [[under_night_in-birth_2_sys:celes]] - developed by French-Bread.",
        "* [[river_city_girls]] and [[river_city_girls_2]] - developed by WayForward using the Kunio-kun property.",
        "[h2]Tagging notes[/h2]",
        "Use [[arc_system_works]] for company branding, company-focused promotional material, crossovers centered on its catalogue, or images where the developer/publisher itself is the relevant subject. For ordinary fan art, use the specific game's copyright tag first.",
        "",
        "Do not add [[arc_system_works]] automatically to every image from a game it published or helped develop. Development, publication and ownership are different relationships; licensed projects such as Dragon Ball FighterZ remain part of their underlying franchises.",
        "[h2]External links[/h2]",
        "* Arc System Works official English website: https://www.arcsystemworks.com/",
        "* Arc System Works official Japanese company profile: https://www.arcsystemworks.jp/company/",
        "* Official game catalogue: https://www.arcsystemworks.com/games/",
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / f"{TAG}.json"
    payload = {
        "tag": TAG,
        "template": "copyright",
        "source": compact("\n".join(lines)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {destination} | validated tags {len(RELATED)}")


if __name__ == "__main__":
    main()
