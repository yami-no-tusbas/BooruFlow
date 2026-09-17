from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Games"

UNDERTALE_CHARACTERS = [
    "frisk_(undertale)", "chara_(undertale)", "flowey_(undertale)",
    "toriel", "sans_(undertale)", "papyrus_(undertale)", "undyne", "alphys",
    "mettaton", "asgore_dreemurr", "asriel_dreemurr", "napstablook", "muffet",
    "temmie",
]
DELTARUNE_MAIN = [
    "kris_(deltarune)", "kris_(dark_world)_(deltarune)",
    "susie_(deltarune)", "susie_(dark_world)_(deltarune)", "ralsei",
    "noelle_holiday", "berdly_(deltarune)", "soul_(deltarune)",
]
DELTARUNE_DARKNERS = [
    "lancer_(deltarune)", "king_(deltarune)", "rouxls_kaard",
    "seam_(deltarune)", "jevil", "queen_(deltarune)",
    "spamton_g._spamton", "spamton_neo", "sweet_(deltarune)",
    "cap'n_(deltarune)", "k_k_(deltarune)", "swatch_(deltarune)",
    "tasque_manager_(deltarune)",
]
PLACES_AND_CONCEPTS = [
    "hometown_(deltarune)", "dark_fountain_(deltarune)",
    "castle_town_(deltarune)", "card_kingdom_(deltarune)",
    "cyber_world_(deltarune)", "tv_world_(deltarune)",
    "weird_route_(deltarune)", "snowgrave_route_(deltarune)",
    "thorn_ring_(deltarune)", "shadow_crystal_(deltarune)",
]


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def bullets(tags: list[str]) -> list[str]:
    return [f"* [[{tag}]]" for tag in tags]


def utdr_source() -> str:
    lines = [
        "[b]UTDR[/b] is a fan abbreviation and Gelbooru umbrella tag for Toby Fox's [[undertale]] and [[deltarune]]. It is useful for crossovers, comparative fan art, shared official material and works that intentionally combine both games.",
        "",
        "UTDR is not the title of a separate game. Although Undertale and Deltarune share creators, themes, mechanics, music motifs and alternate versions of several familiar characters, their stories take place in different worlds. Toby Fox's official Deltarune FAQ states that Undertale's world and ending remain untouched.",
        "[h2]Included works[/h2]",
        "* [[undertale]] - a role-playing game released in 2015. A human falls into the Underground, where encounters can be resolved through fighting or mercy and player choices substantially affect the journey.",
        "* [[deltarune]] - a separate, chapter-based parallel story first released in 2018. Chapters 1-5 are available as of 2026, with later chapters planned as free updates to the full game.",
        "[h2]Undertale characters[/h2]",
        *bullets(UNDERTALE_CHARACTERS),
        "[h2]Deltarune characters[/h2]",
        *bullets(DELTARUNE_MAIN),
        *bullets(DELTARUNE_DARKNERS),
        "[h2]Shared names and counterparts[/h2]",
        "Deltarune includes different-world versions of familiar Undertale characters such as [[toriel]], [[asgore_dreemurr]], [[sans_(undertale)]], [[undyne]], [[alphys]], [[napstablook]] and [[temmie]]. These counterparts have different histories and relationships even when Gelbooru uses the same character tag.",
        "",
        "Kris is not Frisk or Chara, and Ralsei is not Asriel. Similar appearances, names or anagrams do not make characters interchangeable for tagging purposes.",
        "[h2]Tagging notes[/h2]",
        "Use [[utdr_(toby_fox)]] when an image genuinely combines or collectively represents Undertale and Deltarune. For an image belonging to only one game, use [[undertale]] or [[deltarune]] instead of treating UTDR as a mandatory parent tag.",
        "",
        "Tag each depicted character individually. When Deltarune provides a specific Light World or Dark World tag, use the form actually shown.",
        "[h2]External links[/h2]",
        "* Official Undertale website: https://undertale.com/",
        "* Official Deltarune website: https://deltarune.com/",
        "* Official Deltarune FAQ and continuity explanation: https://deltarune.com/help/",
    ]
    return compact("\n".join(lines))


def deltarune_source() -> str:
    lines = [
        "[b]Deltarune[/b] is an episodic role-playing game written and directed by Toby Fox. It is a parallel story connected to [[undertale]], but takes place in a different world with characters who have lived different lives. Chapter 1 was first released in 2018; Chapters 1-5 are available as of 2026, while Chapter 6 and further chapters remain in development.",
        "",
        "The game follows Kris, Susie and Ralsei as they travel through Dark Worlds created by Dark Fountains. Its turn-based battles combine party commands with bullet-dodging defense. Enemies can generally be fought or spared, while the game's official description nevertheless states that the completed story has one ending.",
        "[h2]Main party and Lightners[/h2]",
        "* [[kris_(deltarune)]] - the human protagonist in the Light World.",
        "* [[kris_(dark_world)_(deltarune)]] - Kris's Dark World form.",
        "* [[susie_(deltarune)]] - Kris's classmate and party member.",
        "* [[susie_(dark_world)_(deltarune)]] - Susie's Dark World form.",
        "* [[ralsei]] - a Darkner prince and party member.",
        "* [[noelle_holiday]] - Kris's classmate, with a major role beginning in Chapter 2.",
        "* [[berdly_(deltarune)]] - classmate of Kris, Susie and Noelle.",
        "* [[soul_(deltarune)]] - the red SOUL controlled by the player; it is repeatedly distinguished from Kris's body and independent actions.",
        "[h2]Chapter 1 - Card Kingdom[/h2]",
        "Kris and Susie enter a Dark World through the school supply closet and meet Ralsei. The chapter introduces:",
        "* [[lancer_(deltarune)]]",
        "* [[king_(deltarune)]]",
        "* [[rouxls_kaard]]",
        "* [[seam_(deltarune)]]",
        "* [[jevil]] - the chapter's hidden boss.",
        "[h2]Chapter 2 - Cyber World[/h2]",
        "A new Dark World appears in the library computer lab. Noelle and Berdly become central to the chapter, alongside:",
        "* [[queen_(deltarune)]]",
        "* [[spamton_g._spamton]]",
        "* [[spamton_neo]] - Spamton's hidden-boss form.",
        "* [[sweet_(deltarune)]], [[cap'n_(deltarune)]] and [[k_k_(deltarune)]]",
        "* [[swatch_(deltarune)]]",
        "* [[tasque_manager_(deltarune)]]",
        "[h2]Chapters 3-5[/h2]",
        "Chapters 3 and 4 launched with the paid full game in 2025, and Chapter 5 followed as a free update. They continue the story beyond the Cyber World and introduce additional Dark Worlds, characters and forms. Use chapter-specific character, costume and location tags where available rather than assuming that every later design belongs to Chapters 1 or 2.",
        "[h2]Locations and concepts[/h2]",
        "* [[hometown_(deltarune)]] - the principal Light World town.",
        "* [[castle_town_(deltarune)]] - Ralsei's central Dark World settlement.",
        "* [[card_kingdom_(deltarune)]] - Chapter 1 Dark World.",
        "* [[cyber_world_(deltarune)]] - Chapter 2 Dark World.",
        "* [[tv_world_(deltarune)]] - a later Dark World designation used by Gelbooru.",
        "* [[dark_fountain_(deltarune)]] - the fountains that create and sustain Dark Worlds.",
        "[h2]Alternate route and hidden bosses[/h2]",
        "Chapter 2 contains an optional coercive path commonly tagged [[weird_route_(deltarune)]] or [[snowgrave_route_(deltarune)]]. Related elements include [[thorn_ring_(deltarune)]]. These tags should only be used when the image specifically refers to that route.",
        "",
        "Jevil and Spamton NEO are optional hidden bosses connected by the recurring [[shadow_crystal_(deltarune)]] system. Later chapters continue the pattern of secret encounters.",
        "[h2]Relationship to Undertale[/h2]",
        "Deltarune contains alternate-world versions of several [[undertale]] characters and deliberately reuses visual and musical ideas. It is not a continuation that overwrites Undertale's ending. Do not tag Kris as Frisk or Chara, or Ralsei as Asriel, solely because of resemblance or fan theories.",
        "[h2]Related tags[/h2]",
        "* [[utdr_(toby_fox)]] - umbrella tag for works combining Undertale and Deltarune.",
        "* [[undertale]] - related game set in a distinct world.",
        "[h2]Tagging notes[/h2]",
        "Use [[deltarune]] for material based on the game. Add specific character and form tags, particularly the separate Light World and Dark World forms of Kris and Susie. Use route tags only when the depicted scene clearly depends on that route, and avoid turning unconfirmed theories into character-identification tags.",
        "[h2]External links[/h2]",
        "* Official website: https://deltarune.com/",
        "* Official FAQ, release status and Undertale relationship: https://deltarune.com/help/",
        "* Official Undertale website: https://undertale.com/",
    ]
    return compact("\n".join(lines))


def main() -> None:
    drafts = {
        "utdr_(toby_fox)": (
            "utdr_toby_fox.json",
            ["undertale", "deltarune", *UNDERTALE_CHARACTERS, *DELTARUNE_MAIN, *DELTARUNE_DARKNERS],
            utdr_source(),
        ),
        "deltarune": (
            "deltarune.json",
            ["undertale", "utdr_(toby_fox)", *DELTARUNE_MAIN, *DELTARUNE_DARKNERS, *PLACES_AND_CONCEPTS],
            deltarune_source(),
        ),
    }
    connection = sqlite3.connect(DB)
    OUT.mkdir(parents=True, exist_ok=True)
    for tag, (filename, links, source) in drafts.items():
        missing = [name for name in [tag, *links] if not connection.execute(
            "SELECT 1 FROM tags WHERE name=?", (name,)
        ).fetchone()]
        if missing:
            raise SystemExit(f"Missing local tags for {tag}: {missing}")
        payload = {
            "tag": tag,
            "template": "copyright",
            "source": source,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        destination = OUT / filename
        destination.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {destination} | validated tags {len(set(links))}")


if __name__ == "__main__":
    main()
