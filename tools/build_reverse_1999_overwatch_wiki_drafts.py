from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Games"

REVERSE_CHARACTERS = [
    "vertin_(reverse:1999)", "sonetto_(reverse:1999)", "regulus_(reverse:1999)",
    "apple_(reverse:1999)", "schneider_(reverse:1999)", "arcana_(reverse:1999)",
    "constantine_(reverse:1999)", "tooth_fairy_(reverse:1999)",
    "lilya_(reverse:1999)", "x_(reverse:1999)", "37_(reverse:1999)",
    "6_(reverse:1999)", "sophia_(reverse:1999)", "isolde_(reverse:1999)",
    "kakania_(reverse:1999)", "marcus_(reverse:1999)", "vila_(reverse:1999)",
    "windsong_(reverse:1999)", "lucy_(reverse:1999)",
    "semmelweis_(reverse:1999)", "jessica_(reverse:1999)",
    "voyager_(reverse:1999)", "melania_(reverse:1999)",
    "eternity_(reverse:1999)", "a_knight_(reverse:1999)",
    "pickles_(reverse:1999)",
]

OVERWATCH_CHARACTERS = [
    "d.va_(overwatch)", "doomfist_(overwatch)", "junker_queen_(overwatch)",
    "mauga_(overwatch)", "orisa_(overwatch)", "ramattra_(overwatch)",
    "reinhardt_(overwatch)", "roadhog_(overwatch)", "sigma_(overwatch)",
    "winston_(overwatch)", "wrecking_ball_(overwatch)", "zarya_(overwatch)",
    "ashe_(overwatch)", "bastion_(overwatch)", "cassidy_(overwatch)",
    "echo_(overwatch)", "freja_(overwatch)", "genji_(overwatch)",
    "hanzo_(overwatch)", "hazard_(overwatch)", "junkrat_(overwatch)",
    "mei_(overwatch)", "pharah_(overwatch)", "reaper_(overwatch)",
    "sojourn_(overwatch)", "soldier:_76_(overwatch)", "sombra_(overwatch)",
    "symmetra_(overwatch)", "torbjorn_(overwatch)", "tracer_(overwatch)",
    "venture_(overwatch)", "vendetta_(overwatch)", "widowmaker_(overwatch)",
    "ana_(overwatch)", "baptiste_(overwatch)", "brigitte_(overwatch)",
    "illari_(overwatch)", "juno_(overwatch)", "kiriko_(overwatch)",
    "lifeweaver_(overwatch)", "lucio_(overwatch)", "mercy_(overwatch)",
    "mizuki_(overwatch)", "moira_(overwatch)", "wuyang_(overwatch)",
    "zenyatta_(overwatch)", "anran_(overwatch)", "domina_(overwatch)",
    "emre_(overwatch)", "jetpack_cat_(overwatch)", "shion_(overwatch)",
    "sierra_(overwatch)",
]


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def bullets(tags: list[str]) -> list[str]:
    return [f"* [[{tag}]]" for tag in tags]


def reverse_source() -> str:
    central = REVERSE_CHARACTERS[:9]
    others = REVERSE_CHARACTERS[9:]
    lines = [
        "[b]Reverse: 1999[/b] is a turn-based tactical role-playing game developed and published by Bluepoch. It was first released in China in 2023, followed by an international release later that year.",
        "",
        "At the final moment of 1999, a mysterious phenomenon known as the Storm begins reversing time and erasing eras. Vertin, the Timekeeper, can survive its effects and travels between periods to rescue arcanists while investigating the Storm, the St. Pavlov Foundation and the extremist Manus Vindictae.",
        "[h2]Setting and terminology[/h2]",
        "Arcanists are people or other beings capable of using arcane skills. The St. Pavlov Foundation researches and regulates arcanists, while Manus Vindictae promotes arcanist supremacy and opposes the Foundation. Vertin shelters companions inside her suitcase, which remains protected from the Storm.",
        "[h2]Central characters[/h2]",
        *bullets(central),
        "[h2]Other prominent arcanists and characters[/h2]",
        *bullets(others),
        "[h2]Tagging notes[/h2]",
        "Use [[reverse:1999]] for characters, costumes and settings originating from the game. Many characters have additional tags for individual garments or alternate versions; use those together with the base character tag when the depicted design is identifiable.",
        "[h2]External links[/h2]",
        "* Official website: https://reverse1999.bluepoch.com/",
        "* Official version and event information: https://reverse1999.bluepoch.com/en/home/",
    ]
    return compact("\n".join(lines))


def overwatch_source() -> str:
    # Keep the roster alphabetical and version-neutral: Blizzard can change roles over time.
    roster = sorted(OVERWATCH_CHARACTERS)
    lines = [
        "[b]Overwatch[/b] is a science-fiction hero-shooter franchise developed by Blizzard Entertainment. The original [[overwatch_1]] was released in 2016; [[overwatch_2]] replaced it as the active game in 2022 and continues the same setting and cast.",
        "",
        "The story takes place on a near-future Earth after the Omnic Crisis. The international task force Overwatch once helped end the conflict, but was later disbanded under the Petras Act. Winston's recall brings former agents and new allies together as Null Sector and Talon threaten the world.",
        "[h2]Organizations and factions[/h2]",
        "* Overwatch - the former international peacekeeping organization and its recalled successor.",
        "* Blackwatch - Overwatch's former covert-operations division.",
        "* Talon - a global terrorist organization and a principal enemy of Overwatch.",
        "* Null Sector - a militant omnic faction.",
        "* MEKA - South Korea's Mobile Exo-Force.",
        "* The Junkers - inhabitants and fighters associated with Junkertown.",
        "[h2]Playable heroes[/h2]",
        "The live roster changes as heroes are added. The following character tags were present in Gelbooru's local tag database when this article was prepared:",
        *bullets(roster),
        "[h2]Related tags and disambiguation[/h2]",
        "* [[overwatch_1]] - material specifically associated with the original 2016 game.",
        "* [[overwatch_2]] - sequel-era material, including designs and heroes introduced after its launch.",
        "* [[overwatch_league]] - the former official esports league and its team branding.",
        "",
        "Use [[overwatch]] as the franchise-level copyright tag. Add the numbered game tag when the image clearly depicts a game-specific design or piece of promotional material. Character aliases and obsolete names should not replace the current canonical character tags.",
        "[h2]External links[/h2]",
        "* Official Overwatch website: https://overwatch.blizzard.com/",
        "* Official hero roster: https://overwatch.blizzard.com/en-us/heroes/",
        "* Official story overview: https://overwatch.blizzard.com/en-us/media/stories/",
    ]
    return compact("\n".join(lines))


def main() -> None:
    drafts = {
        "reverse:1999": ("reverse_1999.json", REVERSE_CHARACTERS, reverse_source()),
        "overwatch": (
            "overwatch.json",
            ["overwatch_1", "overwatch_2", "overwatch_league", *OVERWATCH_CHARACTERS],
            overwatch_source(),
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
        print(f"wrote {destination} | validated tags {len(links)}")


if __name__ == "__main__":
    main()
