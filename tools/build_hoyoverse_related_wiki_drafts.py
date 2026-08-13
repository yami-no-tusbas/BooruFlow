from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "databases" / "g_tags_260810.db"
OUT = ROOT / "var" / "wiki_drafts"


PAGES: dict[str, tuple[str, list[str]]] = {
    "tears_of_themis": (
        "copyright",
        [
            "[b]Tears of Themis[/b] is a free-to-play romance and detective visual novel developed by [[mihoyo]] and published globally under the HoYoverse brand. The player controls a rookie attorney in the fictional city of Stellis, investigating cases while working with the members of the NXX Investigation Team.",
            "",
            "The story combines legal investigation, card-based debates and individual romance routes. The player character is commonly called Rosa by the community and on imageboards, although her name can be chosen in the game.",
            "[h2]Main characters[/h2]",
            "* [[rosa_(tears_of_themis)]] - the player character and attorney.",
            "* [[luke_pearce_(tears_of_themis)]] - private investigator and Rosa's childhood friend.",
            "* [[artem_wing_(tears_of_themis)]] - senior attorney at Themis Law Firm.",
            "* [[vyn_richter_(tears_of_themis)]] - psychiatrist and psychology specialist.",
            "* [[marius_von_hagen_(tears_of_themis)]] - heir to the Pax Group and an art student.",
            "[h2]Other recurring tags[/h2]",
            "* [[davis_(tears_of_themis)]]",
            "* [[aaron_yishmir_(tears_of_themis)]]",
            "[h2]Tagging notes[/h2]",
            "* Add this copyright to artwork based on the game, its cards, events or official promotional material.",
            "* Use [[rosa_(tears_of_themis)]] for the depicted protagonist even when a player-selected name is used in accompanying text.",
            "* Card outfits and event costumes supplement the base character tag; they do not replace it.",
            "[h2]Developer and related wikis[/h2]",
            "* [[mihoyo]]",
            "* [[hoyoverse]] - alias of miHoYo in Gelbooru tagging usage.",
            "* [[genshin_impact]]",
            "* [[honkai_(series)]]",
            "* [[zenless_zone_zero]]",
            "[h2]External sources[/h2]",
            "* Official site: https://tot.hoyoverse.com/",
            "* Official news and character information: https://tot.hoyoverse.com/en/information/all",
            "* Official HoYoverse support: https://support.hoyoverse.com/hc/en-us/categories/48105058557721-Tears-of-Themis",
        ],
    ),
    "petit_planet": (
        "general",
        [
            "[b]Petit Planet[/b] is a cosmic life-simulation game in development by [[mihoyo]] and published under the HoYoverse brand. Players become Planet Tenders, build and cultivate a small planet, befriend animal-like neighbors, and travel through the Starsea to visit other worlds.",
            "",
            "The game has been presented through limited, data-wipe beta tests. Names, designs and gameplay details may change before release.",
            "",
            "[b]Database note:[/b] [[petit_planet]] is currently category 0 (general) in the local Gelbooru database rather than category 3 (copyright). This wiki documents the existing tag without silently changing its category.",
            "[h2]Currently tagged characters[/h2]",
            "* [[elsasani_(petit_planet)]] - the local character tag currently uses this spelling; official and community material also renders the name as Esassani.",
            "* [[mobai_(petit_planet)]] - currently stored as a general tag in the local database.",
            "[h2]Other named neighbors seen in tests[/h2]",
            "Yunguo, Isaki, Medowlyn, Frostia, Mors, Glenn, Dorjelang and Msafiri have appeared in test or promotional material, but no validated local character tags were found for them in the current database snapshot. Do not create links merely from provisional or community spellings.",
            "[h2]Tagging notes[/h2]",
            "* Use [[petit_planet]] for screenshots, official promotional art and fan art identifiable as belonging to the game.",
            "* Use the qualified character tag where one exists. Because the game remains in development, verify names against current official material before creating new tags.",
            "* The tag's current general category is a database fact, not a recommendation that the game should remain uncategorized.",
            "[h2]Developer and related wikis[/h2]",
            "* [[mihoyo]]",
            "* [[hoyoverse]]",
            "[h2]External sources[/h2]",
            "* Official site: https://planet.hoyoverse.com/en-us/home",
            "* Official HoYoLAB channel: https://www.hoyolab.com/circles/10/100002/official?page_type=100002&page_sort=news",
            "* Stardrift Test information: https://www.hoyolab.com/article/44455649",
        ],
    ),
    "hoyoverse": (
        "general",
        [
            "[b]HoYoverse[/b] is the global publishing and services brand associated with the Chinese developer [[mihoyo]]. On Gelbooru, [[hoyoverse]] is treated as an alias/deprecated equivalent of [[mihoyo]], not as a separate fictional copyright.",
            "[h2]Tagging notes[/h2]",
            "* Use [[mihoyo]] for company-focused material, logos, studio celebrations and works explicitly grouping the developer's portfolio.",
            "* Do not add either company tag automatically to ordinary fan art from one game; use the specific copyright instead.",
            "* Existing posts using [[hoyoverse]] refer to the same company/brand relationship and should be understood through the [[mihoyo]] wiki.",
            "[h2]Major game properties[/h2]",
            "* [[genshin_impact]]",
            "* [[honkai_(series)]]",
            "* [[tears_of_themis]]",
            "* [[zenless_zone_zero]]",
            "* [[petit_planet]]",
            "[h2]Related services and projects[/h2]",
            "* [[hoyolab]]",
            "* [[hoyofair]]",
            "[h2]External sources[/h2]",
            "* miHoYo official company page: https://www.mihoyo.com/en/?page=about",
            "* HoYoverse official site: https://www.hoyoverse.com/en-us/",
            "* HoYoverse support portal: https://support.hoyoverse.com/hc/en-us",
        ],
    ),
    "hoyofair": (
        "general",
        [
            "[b]HoYoFair[/b] is an official HoYoverse program and label presenting fan-created derivative works based on HoYoverse game properties. Its activities include online fan-art special programs, creator collaborations and live fan concerts.",
            "",
            "HoYoFair works may be commissioned, sponsored or presented through official channels, but their stories and settings are generally derivative fan creations rather than canonical events from the games depicted.",
            "[h2]Featured properties[/h2]",
            "* [[genshin_impact]]",
            "* [[honkai:_star_rail]]",
            "* [[honkai_impact_3rd]]",
            "* [[zenless_zone_zero]]",
            "[h2]Tagging notes[/h2]",
            "* Use [[hoyofair]] for images, animations, character designs or promotional material identifiable as belonging to a HoYoFair program or concert.",
            "* Also add the copyright and character tags for the source property depicted.",
            "* Do not use [[official_art]] solely because a fan work was included in or sponsored by HoYoFair. Follow Gelbooru's established definition of official art and the source's actual authorship.",
            "* HoYoFair-specific alternate designs should retain the base character and source-game copyright tags where applicable.",
            "[h2]Related wikis[/h2]",
            "* [[mihoyo]]",
            "* [[hoyoverse]]",
            "* [[hoyolab]]",
            "[h2]External sources[/h2]",
            "* Official HoYoFair account on HoYoLAB: https://www.hoyolab.com/accountCenter/postList?id=244938673",
            "* Official HoYoFair YouTube channel: https://www.youtube.com/@HoYoFair",
            "* Official HoYoLAB event listing: https://www.hoyolab.com/official/5/events",
        ],
    ),
}


def compact_headings(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def main() -> None:
    con = sqlite3.connect(DB)
    rows = {
        name: (post_count, category)
        for name, post_count, category in con.execute(
            "SELECT name, post_count, category FROM tags"
        )
    }
    expected = {
        "tears_of_themis": 3,
        "petit_planet": 0,
        "hoyoverse": 6,
        "hoyofair": 0,
    }
    mismatches = [(tag, rows.get(tag), category) for tag, category in expected.items() if not rows.get(tag) or rows[tag][1] != category]
    if mismatches:
        raise SystemExit(f"Unexpected root tag categories: {mismatches}")

    OUT.mkdir(parents=True, exist_ok=True)
    for tag, (template, lines) in PAGES.items():
        source = compact_headings("\n".join(lines))
        payload = {
            "tag": tag,
            "template": template,
            "source": source,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        path = OUT / f"{tag}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
