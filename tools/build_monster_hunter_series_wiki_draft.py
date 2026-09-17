from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Monster Hunter"
TAG = "monster_hunter_(series)"

SECTIONS = [
    {
        "title": "First and second generations",
        "games": ["monster_hunter_g", "monster_hunter_portable", "monster_hunter_2", "monster_hunter_portable_2nd", "monster_hunter_portable_2nd_g", "monster_hunter_freedom_unite", "monster_hunter_frontier"],
        "characters": [],
        "monsters": ["rathalos", "rathian", "fatalis", "kirin", "kushala_daora", "teostra", "tigrex", "nargacuga"],
    },
    {
        "title": "Third generation",
        "games": ["monster_hunter_3", "monster_hunter_tri", "monster_hunter_portable_3rd", "monster_hunter_3_g", "monster_hunter_3_ultimate"],
        "characters": ["cha-cha", "kayamba"],
        "monsters": ["lagiacrus", "zinogre", "deviljho", "brachydios"],
    },
    {
        "title": "Fourth generation and Generations",
        "games": ["monster_hunter_4", "monster_hunter_4_g", "monster_hunter_4_ultimate", "monster_hunter_x", "monster_hunter_xx", "monster_hunter_generations"],
        "characters": ["guildmarm_(monster_hunter)", "ace_cadet"],
        "monsters": ["gore_magala", "seregios", "dalamadur", "glavenus", "mizutsune", "gammoth", "astalos", "valstrax"],
    },
    {
        "title": "Monster Hunter: World and Iceborne",
        "games": ["monster_hunter:_world", "monster_hunter_world:_iceborne"],
        "characters": ["handler_(monster_hunter_world)", "serious_handler", "third_fleet_master_(monster_hunter_world)", "provisions_manager_(monster_hunter_world)"],
        "monsters": ["nergigante", "velkhana", "legiana", "bazelgeuse"],
    },
    {
        "title": "Monster Hunter Rise and Sunbreak",
        "games": ["monster_hunter_rise"],
        "characters": ["hinoa", "minoto", "yomogi_(monster_hunter)", "fugen_(monster_hunter_rise)", "rondine", "fiorayne_(monster_hunter)", "luchika_(monster_hunter)", "chichae_(monster_hunter)"],
        "monsters": ["magnamalo", "wind_serpent_ibushi", "thunder_serpent_narwa", "malzeno_(monster_hunter)", "gaismagorm"],
    },
    {
        "title": "Monster Hunter Wilds",
        "games": ["monster_hunter_wilds"],
        "characters": ["gemma_(monster_hunter_wilds)", "alma_(monster_hunter_wilds)", "nata_(monster_hunter_wilds)", "erik_(monster_hunter_wilds)", "nadia_(monster_hunter_wilds)", "felicita_(monster_hunter_wilds)"],
        "monsters": ["arkveld", "rey_dau", "uth_duna", "nu_udra", "jin_dahaad", "zoh_shia"],
    },
    {
        "title": "Monster Hunter Stories",
        "games": ["monster_hunter_stories", "monster_hunter_stories_2:_wings_of_ruin", "monster_hunter_stories_3:_twisted_reflection"],
        "characters": ["ryuuto_(monster_hunter_stories)", "naville_(monster_hunter_stories)", "reverto_(monster_hunter_stories)", "ena_(monster_hunter)", "kayna_(monster_hunter)", "tsukino_(monster_hunter)", "eleanor_(monster_hunter_stories:3_twisted_reflection)"],
        "monsters": [],
    },
]


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def styled(tag: str, count: int) -> str:
    link = f"[[{tag}]]"
    if count >= 10_000: return f"[b]{link}[/b]"
    if count >= 1_000: return f"[i]{link}[/i]"
    if count < 25: return f"{link}**"
    if count < 50: return f"{link}*"
    return link


def main() -> None:
    linked = ["monster_hunter", "capcom"]
    for section in SECTIONS:
        linked.extend(section["games"])
        linked.extend(section["characters"])
        linked.extend(section["monsters"])
    linked = list(dict.fromkeys(linked))
    with sqlite3.connect(DB) as connection:
        rows = connection.execute(
            f"SELECT name, post_count FROM tags WHERE name IN ({','.join('?' for _ in [TAG, *linked])})",
            [TAG, *linked],
        ).fetchall()
    counts = dict(rows)
    missing = sorted(set([TAG, *linked]) - set(counts))
    if missing:
        raise SystemExit(f"Missing local tags: {missing}")

    lines = [
        "[b]Monster Hunter[/b] is an action role-playing game franchise created and published by [[capcom]]. Players take the role of hunters who track, repel, capture or slay large monsters, then use gathered materials to craft weapons and armor suited to increasingly dangerous quests. Cooperative hunting, distinct weapon classes and an ecosystem of recurring monsters are central features of the series.",
        "",
        "This page is the modern franchise portal for [[monster_hunter_(series)]]. Gelbooru also retains the historical 2010 wiki article [[monster_hunter]], which introduced the series and named Rathian, Rathalos, Kirin, Tigrex and Deviljho as famous monsters. That earlier page is preserved and linked here rather than overwritten.",
        "",
        "[b]Legend:[/b] [b]bold[/b] = 10,000+ posts; [i]italic[/i] = 1,000+ posts; * = fewer than 50 posts; ** = fewer than 25 posts. Counts come from the local Gelbooru tag database.",
    ]
    for section in SECTIONS:
        lines.append(f"[h2]{section['title']}[/h2]")
        lines.append("[b]Games:[/b]")
        lines.extend(f"* {styled(tag, counts[tag])}" for tag in section["games"])
        if section["characters"]:
            lines.append("[b]Principal tagged characters:[/b]")
            lines.extend(f"* {styled(tag, counts[tag])}" for tag in section["characters"])
        if section["monsters"]:
            lines.append("[b]Representative monsters:[/b]")
            lines.extend(f"* {styled(tag, counts[tag])}" for tag in section["monsters"])
    lines += [
        "[h2]Other games and branches[/h2]",
        "The franchise also includes online, mobile and regional projects such as [[monster_hunter_online]], [[monster_hunter_riders]], [[monster_hunter_now]] and [[monster_hunter_mezeporta_kaitaku-ki]]. Use those specific copyright tags when the source is identifiable.",
        "[h2]Tagging notes[/h2]",
        "Use [[monster_hunter_(series)]] for franchise-wide material, cross-game compilations or images whose exact entry cannot be identified. Add the most specific game copyright whenever possible. Monsters use character-category tags on Gelbooru; armor inspired by a monster is normally a separate general tag and should not be mistaken for the monster itself.",
        "",
        "The old [[monster_hunter]] tag is an alias in the current local database, but its historical wiki page remains useful context. Use the canonical [[monster_hunter_(series)]] copyright tag for current tagging.",
        "[h2]Related tags[/h2]", "* [[capcom]]", "* [[monster_hunter]] - historical Gelbooru wiki article from 2010.",
        "[h2]External links[/h2]", "* Official Monster Hunter portal: https://www.monsterhunter.com/", "* Capcom official website: https://www.capcom.com/",
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / f"{TAG}.json"
    payload = {"tag": TAG, "template": "copyright", "source": compact("\n".join(lines)), "updated_at": datetime.now(timezone.utc).isoformat()}
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {destination} | linked tags {len(linked)} | bytes {len(payload['source'].encode('utf-8')):,}")


if __name__ == "__main__":
    main()
