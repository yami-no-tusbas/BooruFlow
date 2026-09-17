from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Project Moon"

CHARACTERS = {
    "2018 - [[lobotomy_corporation]]": [
        "x_(project_moon)", "angela_(project_moon)", "ayin_(project_moon)",
        "carmen_(project_moon)", "benjamin_(project_moon)", "malkuth_(project_moon)",
        "yesod_(project_moon)", "hod_(project_moon)", "netzach_(project_moon)",
        "tiphereth_a_(project_moon)", "tiphereth_b_(project_moon)",
        "gebura_(project_moon)", "chesed_(project_moon)", "binah_(project_moon)",
        "hokma_(project_moon)", "myo_(project_moon)",
    ],
    "2021 - [[library_of_ruina]]": [
        "roland_(project_moon)", "angelica_(project_moon)", "argalia_(project_moon)",
        "xiao_(project_moon)", "lowell_(project_moon)", "philip_(project_moon)",
        "yujin_(project_moon)", "iori_(project_moon)", "zena_(project_moon)",
        "baral_(project_moon)", "eileen_(project_moon)", "oswald_(project_moon)",
        "pluto_(project_moon)", "elena_(project_moon)", "tanya_(project_moon)",
        "greta_(project_moon)", "bremen_(project_moon)",
    ],
    "2022 - [[leviathan_(project_moon)]]": [
        "vergilius_(project_moon)", "lapis_(project_moon)", "garnet_(project_moon)",
        "charon_(project_moon)", "aseah_(project_moon)",
    ],
    "2023 - [[limbus_company]]": [
        "dante_(limbus_company)", "yi_sang_(project_moon)", "faust_(project_moon)",
        "don_quixote_(project_moon)", "ryoshu_(project_moon)",
        "meursault_(project_moon)", "hong_lu_(project_moon)",
        "heathcliff_(project_moon)", "ishmael_(project_moon)",
        "rodion_(project_moon)", "sinclair_(project_moon)",
        "outis_(project_moon)", "gregor_(project_moon)",
    ],
}


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def pages() -> dict[str, list[str]]:
    return {
        "project_moon": [
            "[b]Project Moon[/b] is a South Korean independent game studio and the umbrella copyright tag for its connected dark-fantasy and dystopian works. Its stories take place in the City, a vast corporate metropolis whose recurring concepts include Fixers, Wings, Abnormalities, E.G.O and Distortions.",
            "",
            "Use this tag for cross-series material and franchise-wide concepts. When an image belongs to one identifiable work, also use that work's more specific copyright tag.",
            "[h2]Main works in release order[/h2]",
            "* [[lobotomy_corporation]] - monster-management simulation; entered Early Access in 2016 and received its full release in 2018.",
            "* [[library_of_ruina]] - 2021 deck-building role-playing game and direct sequel to Lobotomy Corporation.",
            "* [[leviathan_(project_moon)]] - 2022 webcomic/web novel bridging Library of Ruina and Limbus Company.",
            "* [[limbus_company]] - 2023 turn-based role-playing game following Dante and twelve Sinners.",
            "[h2]Other official stories[/h2]",
            "* The Distortion Detective - web novel set around the period of Library of Ruina; no dedicated Gelbooru copyright tag was found in the local database.",
            "* WonderLab - side-story webcomic associated with a Lobotomy Corporation branch facility; no dedicated copyright tag was found in the local database.",
            "[h2]Recurring concepts[/h2]",
            "* [[e.g.o_(project_moon)]]",
            "* [[distortion_(project_moon)]]",
            "* [[enkephalin_(project_moon)]]",
            "* [[golden_bough_(project_moon)]]",
            "[h2]Characters[/h2]",
            "See [[List_of_Project_Moon_characters]] for major characters grouped by their first work in release order.",
            "[h2]External links[/h2]",
            "* Project Moon on Steam: https://store.steampowered.com/developer/ProjectMoon",
            "* Official Postype publications: https://projectmoon.postype.com/",
        ],
        "lobotomy_corporation": [
            "[b]Lobotomy Corporation[/b] is a roguelite monster-management simulation developed and published by [[project_moon]]. Its full Windows release was published on April 9, 2018 after an Early Access period beginning in 2016.",
            "",
            "The player manages an energy company that contains entities called Abnormalities. Employees perform work with them to produce energy while the facility's AI [[angela_(project_moon)]] and the Sephirot supervise its departments. The narrative gradually reveals the truth behind the corporation and the City.",
            "[h2]Characters[/h2]",
            "See the Lobotomy Corporation group in [[List_of_Project_Moon_characters]].",
            "[h2]Related tags[/h2]",
            "* [[project_moon]]",
            "* [[library_of_ruina]] - direct sequel.",
            "* [[employee_(project_moon)]]",
            "* [[nugget_(project_moon)]]",
            "* [[enkephalin_(project_moon)]]",
            "* [[e.g.o_(project_moon)]]",
            "[h2]External links[/h2]",
            "* Official Steam page: https://store.steampowered.com/app/568220/",
        ],
        "library_of_ruina": [
            "[b]Library of Ruina[/b] is a deck-building, turn-based role-playing game developed and published by [[project_moon]]. It is the direct sequel to [[lobotomy_corporation]] and received its full Windows and Xbox release on August 10, 2021.",
            "",
            "[[angela_(project_moon)]] becomes the director of a mysterious Library, while [[roland_(project_moon)]] assists her and its librarians. Invited guests fight in Receptions; those defeated become books that allow the Library to grow and uncover further secrets of the City.",
            "[h2]Characters[/h2]",
            "See the Library of Ruina group in [[List_of_Project_Moon_characters]].",
            "[h2]Related tags[/h2]",
            "* [[project_moon]]",
            "* [[lobotomy_corporation]]",
            "* [[leviathan_(project_moon)]]",
            "* [[librarian_(project_moon)]]",
            "* [[e.g.o_(project_moon)]]",
            "* [[distortion_(project_moon)]]",
            "[h2]External links[/h2]",
            "* Official Steam page: https://store.steampowered.com/app/1256670/",
        ],
        "leviathan_(project_moon)": [
            "[b]Leviathan[/b] is an official Project Moon webcomic and web novel published in 2022. Set after [[library_of_ruina]], it follows [[vergilius_(project_moon)]] and serves as a narrative prequel to [[limbus_company]].",
            "",
            "The story concerns Vergilius's Fixer office, the Ring's experiments and the events that lead to his association with Limbus Company. Publication began as a webcomic and continued in prose form.",
            "[h2]Characters[/h2]",
            "See the Leviathan group in [[List_of_Project_Moon_characters]].",
            "[h2]Related tags[/h2]",
            "* [[project_moon]]",
            "* [[library_of_ruina]]",
            "* [[limbus_company]]",
            "[h2]External links[/h2]",
            "* Official Project Moon Postype: https://projectmoon.postype.com/",
        ],
        "limbus_company": [
            "[b]Limbus Company[/b] is a free-to-play turn-based role-playing game developed and published by [[project_moon]], released for Windows and mobile devices in February 2023. It is set in the same City after the events of [[lobotomy_corporation]] and [[library_of_ruina]].",
            "",
            "The amnesiac executive manager [[dante_(limbus_company)]] leads twelve Sinners through buried Lobotomy Corporation facilities to recover Golden Boughs. Combat uses alternate-world Identities and manifestations called E.G.O; those variants should not be confused with separate base characters.",
            "[h2]The twelve Sinners[/h2]",
            "* [[yi_sang_(project_moon)]]",
            "* [[faust_(project_moon)]]",
            "* [[don_quixote_(project_moon)]]",
            "* [[ryoshu_(project_moon)]]",
            "* [[meursault_(project_moon)]]",
            "* [[hong_lu_(project_moon)]]",
            "* [[heathcliff_(project_moon)]]",
            "* [[ishmael_(project_moon)]]",
            "* [[rodion_(project_moon)]]",
            "* [[sinclair_(project_moon)]]",
            "* [[outis_(project_moon)]]",
            "* [[gregor_(project_moon)]]",
            "[h2]Other central characters[/h2]",
            "* [[dante_(limbus_company)]]",
            "* [[vergilius_(project_moon)]]",
            "* [[charon_(project_moon)]]",
            "[h2]Related tags[/h2]",
            "* [[project_moon]]",
            "* [[leviathan_(project_moon)]]",
            "* [[e.g.o_(project_moon)]]",
            "* [[golden_bough_(project_moon)]]",
            "* [[mephistopheles_(project_moon)]]",
            "[h2]External links[/h2]",
            "* Official Steam page: https://store.steampowered.com/app/1973530/",
            "* Official website: https://limbuscompany.com/",
        ],
    }


def main() -> None:
    connection = sqlite3.connect(DB)
    missing = []
    seen = set()
    character_lines = [
        "[b]About this list:[/b]",
        "This index groups major Project Moon characters by the official work in which they first appeared. Recurring characters are listed only once. Abnormalities, generic employees, E.G.O forms, Identities and cosplay tags are excluded.",
    ]
    for heading, tags in CHARACTERS.items():
        character_lines.append(f"[h2]{heading}[/h2]")
        for tag in tags:
            row = connection.execute("SELECT category FROM tags WHERE name=?", (tag,)).fetchone()
            if not row or row[0] != 4:
                missing.append(tag)
                continue
            if tag not in seen:
                character_lines.append(f"* [[{tag}]]")
                seen.add(tag)
    character_lines.extend(["[h2]See also[/h2]", "* [[project_moon]]"])
    drafts = pages()
    drafts["List_of_Project_Moon_characters"] = character_lines
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    for tag, lines in drafts.items():
        payload = {
            "tag": tag,
            "template": "general" if tag.startswith("List_of_") else "copyright",
            "source": compact("\n".join(lines)),
            "updated_at": stamp,
        }
        (OUT / f"{tag}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"drafts {len(drafts)} | characters {len(seen)} | missing {missing}")


if __name__ == "__main__":
    main()
