from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Games"
TAG = "neptune_(series)"

GODDESSES = [
    "neptune_(neptunia)", "purple_heart_(neptunia)",
    "noire_(neptunia)", "black_heart_(neptunia)",
    "blanc_(neptunia)", "white_heart_(neptunia)",
    "vert_(neptunia)", "green_heart_(neptunia)",
]
CANDIDATES = ["nepgear", "uni_(neptunia)", "rom_(neptunia)", "ram_(neptunia)"]
DIMENSIONAL = [
    "plutia", "iris_heart_(neptunia)", "peashy", "yellow_heart_(neptunia)",
    "uzume", "orange_heart_(neptunia)", "adult_neptune",
]
ALLIES = [
    "compa", "if_(neptunia)", "histoire", "croire", "rei_ryghts",
    "arfoire", "warechu", "umio_(neptunia)", "maho_(neptunia)",
]
MAKERS = [
    "nippon_ichi_(neptunia)", "nisa_(neptunia)", "5pb_(neptunia)",
    "cave_(neptunia)", "marvelousaql_(neptunia)", "falcom_(neptunia)",
    "tekken_(neptunia)", "nitroplus_(neptunia)", "gust_(neptunia)",
    "broccoli_(neptunia)", "red_(neptunia)", "tamsoft_(neptunia)",
    "million_arthur_(neptunia)", "god_eater_(neptunia)",
]
GAME_TAGS = [
    "choujigen_game_neptune", "choujigen_game_neptune_mk2",
    "choujigen_game_neptune_re;birth_1", "choujigen_game_neptune_re;birth_2",
    "choujigen_game_neptune_re;birth_3", "shin_jigen_game_neptune_vii",
    "choujigen_action_neptune_u",
    "choujigen_taisen_neptune_vs_sega_hard_girls",
    "cyberdimension_neptunia_4_goddesses_online",
    "neptunia_x_senran_kagura_ninja_wars",
    "choujigen_game_neptune_sisters_vs_sisters",
    "choujigen_game_neptune:_gamemaker_r:evolution",
    "choujigen_game_neptune_the_animation",
]
LINKS = [*GODDESSES, *CANDIDATES, *DIMENSIONAL, *ALLIES, *MAKERS, *GAME_TAGS]


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def bullets(tags: list[str]) -> list[str]:
    return [f"* [[{tag}]]" for tag in tags]


def main() -> None:
    connection = sqlite3.connect(DB)
    missing = [tag for tag in [TAG, *LINKS] if not connection.execute(
        "SELECT 1 FROM tags WHERE name=?", (tag,)
    ).fetchone()]
    if missing:
        raise SystemExit(f"Missing local tags: {missing}")

    lines = [
        "[b]Neptunia[/b] is a role-playing game franchise created by Idea Factory and Compile Heart. It began with [[choujigen_game_neptune]] for PlayStation 3 in 2010. The series parodies the video-game industry through anthropomorphized consoles, companies, developers and gaming terminology.",
        "",
        "Most games take place in versions of Gamindustri, a world divided among Planeptune, Lastation, Lowee and Leanbox. These nations are protected by goddesses called CPUs, who can transform into their stronger HDD forms. Games frequently move between dimensions or use alternate continuities, so characters with the same name are not always literally the same incarnation.",
        "[h2]The four CPUs[/h2]",
        "Planeptune:",
        "* [[neptune_(neptunia)]] - CPU of Planeptune and central protagonist.",
        "* [[purple_heart_(neptunia)]] - Neptune's HDD form.",
        "",
        "Lastation:",
        "* [[noire_(neptunia)]] - CPU of Lastation.",
        "* [[black_heart_(neptunia)]] - Noire's HDD form.",
        "",
        "Lowee:",
        "* [[blanc_(neptunia)]] - CPU of Lowee.",
        "* [[white_heart_(neptunia)]] - Blanc's HDD form.",
        "",
        "Leanbox:",
        "* [[vert_(neptunia)]] - CPU of Leanbox.",
        "* [[green_heart_(neptunia)]] - Vert's HDD form.",
        "[h2]CPU Candidates[/h2]",
        "The younger sisters of the CPUs serve as CPU Candidates:",
        "* [[nepgear]] - Neptune's younger sister and Planeptune's Candidate.",
        "* [[uni_(neptunia)]] - Noire's younger sister and Lastation's Candidate.",
        "* [[rom_(neptunia)]] and [[ram_(neptunia)]] - Blanc's younger twin sisters and Lowee's Candidates.",
        "[h2]Other dimensions and goddess forms[/h2]",
        "* [[plutia]] / [[iris_heart_(neptunia)]] - Planeptune's Ultra Dimension CPU and her HDD form.",
        "* [[peashy]] / [[yellow_heart_(neptunia)]] - an Ultra Dimension CPU and her HDD form.",
        "* [[uzume]] / [[orange_heart_(neptunia)]] - the CPU associated with the Zero Dimension.",
        "* [[adult_neptune]] - an older alternate-dimension Neptune, distinct from the principal Neptune.",
        "[h2]Recurring allies and antagonists[/h2]",
        *bullets(ALLIES),
        "[h2]Maker characters[/h2]",
        "Maker characters personify real game companies, publishers, magazines or franchises. Their availability and continuity vary between games:",
        *bullets(MAKERS),
        "[h2]Main continuity and remakes[/h2]",
        "The original main releases are:",
        "* [[choujigen_game_neptune]] - Hyperdimension Neptunia (2010); its continuity differs substantially from later games.",
        "* [[choujigen_game_neptune_mk2]] - Hyperdimension Neptunia mk2 (2011), which establishes the main Hyper Dimension continuity used by later numbered games.",
        "* Hyperdimension Neptunia Victory (2012), centered partly on the Ultra Dimension.",
        "* [[shin_jigen_game_neptune_vii]] - Megadimension Neptunia VII (2015), spanning Zero, Hyper and Heart Dimensions.",
        "* Neptunia GameMaker R:Evolution (2023), tagged [[choujigen_game_neptune:_gamemaker_r:evolution]].",
        "* Neptunia Unlimited (2026) - the next numbered entry listed by Compile Heart for the franchise.",
        "",
        "The Re;Birth games are revised remakes or reworkings rather than three wholly new numbered stories:",
        "* [[choujigen_game_neptune_re;birth_1]] - remake/reinterpretation of the first game.",
        "* [[choujigen_game_neptune_re;birth_2]] - remake of mk2.",
        "* [[choujigen_game_neptune_re;birth_3]] - remake of Victory.",
        "[h2]Spin-offs and crossovers[/h2]",
        "Notable spin-offs include Hyperdevotion Noire: Goddess Black Heart; Hyperdimension Neptunia U: Action Unleashed ([[choujigen_action_neptune_u]]); MegaTagmension Blanc + Neptune VS Zombies; Superdimension Neptune VS Sega Hard Girls ([[choujigen_taisen_neptune_vs_sega_hard_girls]]); [[cyberdimension_neptunia_4_goddesses_online]]; Super Neptunia RPG; Neptunia Virtual Stars; [[neptunia_x_senran_kagura_ninja_wars]]; [[choujigen_game_neptune_sisters_vs_sisters]]; and Neptunia Riders VS Dogoos.",
        "",
        "These games often use alternate settings, game-within-a-game premises or self-contained continuities. A spin-off costume or role should not automatically be treated as the character's standard main-series design.",
        "[h2]Anime and other media[/h2]",
        "[[choujigen_game_neptune_the_animation]] is the 2013 television anime adaptation. It combines characters and concepts from the early games into its own continuity. The franchise also includes original video animations, manga, light novels, drama CDs and other merchandise.",
        "[h2]Tagging notes[/h2]",
        "Use [[neptune_(series)]] as the franchise copyright tag. Add the most specific game or adaptation tag when the source is identifiable. Use both a character's civilian-form tag and HDD-form tag only when both forms are actually depicted; transformation forms such as [[purple_heart_(neptunia)]] are distinct character tags on Gelbooru.",
        "",
        "Do not confuse [[neptune_(neptunia)]] with other characters named Neptune. Alternate costumes frequently have their own tags and should supplement, not replace, the base character tag.",
        "[h2]External links[/h2]",
        "* Official Neptunia 15th anniversary history: https://www.compileheart.com/neptune/15th/",
        "* Original game's official website: https://www.compileheart.com/neptune/",
        "* Official Re;Birth series portal: https://www.compileheart.com/neptune/re-birth123/",
        "* Idea Factory International Neptunia games: https://ifi.games/",
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "tag": TAG,
        "template": "copyright",
        "source": compact("\n".join(lines)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    destination = OUT / "neptune_series.json"
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {destination} | validated tags {len(LINKS)}")


if __name__ == "__main__":
    main()
