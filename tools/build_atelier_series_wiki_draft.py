from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Atelier"
TAG = "atelier_(series)"

GAME_TAGS = [
    "atelier_marie", "atelier_elie", "atelier_lilie",
    "atelier_judie", "atelier_viorate",
    "atelier_iris", "atelier_iris_eternal_mana",
    "atelier_iris_eternal_mana_2", "atelier_iris_grand_phantasm",
    "atelier_rorona", "atelier_totori", "atelier_meruru", "atelier_lulua",
    "atelier_ayesha", "atelier_escha_&_logy", "atelier_shallie",
    "atelier_sophie", "atelier_firis", "atelier_lydie_&_suelle",
    "atelier_sophie_2",
    "atelier_ryza", "atelier_ryza_1", "atelier_ryza_2", "atelier_ryza_3",
    "atelier_yumia", "atelier_karia",
    "atelier_resleriana",
    "atelier_resleriana:_the_red_alchemist_&_the_white_guardian",
    "atelier_annie", "atelier_lise", "atelier_lina", "atelier_elkrone",
]

CHARACTER_TAGS = [
    "marie_(atelier)", "totori_(atelier)", "rorona_(atelier)",
    "lulua_(atelier)", "ayesha_altugle", "escha_malier", "logix_ficsario",
    "shallistera_(atelier)", "sophie_neuenmuller", "plachta",
    "firis_mistlud", "lydie_malen", "suelle_malen", "reisalin_stout",
    "yumia_liessfeldt", "karia_(atelier)", "valeria_(atelier)",
]

RELATED_TAGS = ["atelier_kaguya", "tongari_boushi_no_atelier"]


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def main() -> None:
    with sqlite3.connect(DB) as connection:
        rows = connection.execute(
            "SELECT name, post_count, category FROM tags WHERE name IN ({})".format(
                ",".join("?" for _ in [TAG, *GAME_TAGS, *CHARACTER_TAGS, *RELATED_TAGS])
            ),
            [TAG, *GAME_TAGS, *CHARACTER_TAGS, *RELATED_TAGS],
        ).fetchall()
    found = {name: (count, category) for name, count, category in rows}
    expected = [TAG, *GAME_TAGS, *CHARACTER_TAGS, *RELATED_TAGS]
    missing = [tag for tag in expected if tag not in found]
    if missing:
        raise SystemExit(f"Missing local tags: {missing}")
    wrong = [tag for tag in GAME_TAGS if found[tag][1] != 3]
    if found[TAG][1] != 3 or wrong:
        raise SystemExit(f"Unexpected copyright categories: {[TAG] if found[TAG][1] != 3 else []}{wrong}")
    wrong_characters = [tag for tag in CHARACTER_TAGS if found[tag][1] != 4]
    if wrong_characters:
        raise SystemExit(f"Unexpected character categories: {wrong_characters}")

    lines = [
        "[b]Atelier[/b] is a fantasy role-playing game franchise developed by Gust, now a Koei Tecmo studio. Beginning with [[atelier_marie]] in 1997, the games usually follow young alchemists who gather ingredients, synthesize items and use their craft while exploring their world. Individual story arcs generally take place in separate continuities, although characters often reappear within the same subseries and in crossover titles.",
        "[h2]Main series by continuity[/h2]",
        "[h3]Salburg series[/h3]",
        "The original trilogy is set around the city of Salburg:",
        "* [[atelier_marie]] - Atelier Marie: The Alchemist of Salburg (1997), starring [[marie_(atelier)]].",
        "* [[atelier_elie]] - Atelier Elie: The Alchemist of Salburg 2 (1998).",
        "* [[atelier_lilie]] - Atelier Lilie: The Alchemist of Salburg 3 (2001).",
        "[h3]Gramnad series[/h3]",
        "* [[atelier_judie]] - Atelier Judie: The Alchemist of Gramnad (2002).",
        "* [[atelier_viorate]] - Atelier Viorate: The Alchemist of Gramnad 2 (2003).",
        "[h3]Iris and Mana Khemia era[/h3]",
        "The PlayStation 2 era placed greater emphasis on traditional party-based adventure and combat:",
        "* [[atelier_iris]] / [[atelier_iris_eternal_mana]] - Atelier Iris: Eternal Mana (2004).",
        "* [[atelier_iris_eternal_mana_2]] - Atelier Iris 2: The Azoth of Destiny (2005).",
        "* [[atelier_iris_grand_phantasm]] - Atelier Iris 3: Grand Phantasm (2006).",
        "* Mana Khemia: Alchemists of Al-Revis (2007) and Mana Khemia 2: Fall of Alchemy (2008) continue this broad era, but currently lack dedicated copyright tags in the local Gelbooru database.",
        "[h3]Arland series[/h3]",
        "* [[atelier_rorona]] - Atelier Rorona: The Alchemist of Arland (2009), starring [[rorona_(atelier)]].",
        "* [[atelier_totori]] - Atelier Totori: The Adventurer of Arland (2010), starring [[totori_(atelier)]].",
        "* [[atelier_meruru]] - Atelier Meruru: The Apprentice of Arland (2011).",
        "* [[atelier_lulua]] - Atelier Lulua: The Scion of Arland (2019), starring [[lulua_(atelier)]].",
        "[h3]Dusk series[/h3]",
        "* [[atelier_ayesha]] - Atelier Ayesha: The Alchemist of Dusk (2012), starring [[ayesha_altugle]].",
        "* [[atelier_escha_&_logy]] - Atelier Escha & Logy: Alchemists of the Dusk Sky (2013), starring [[escha_malier]] and [[logix_ficsario]].",
        "* [[atelier_shallie]] - Atelier Shallie: Alchemists of the Dusk Sea (2014), including [[shallistera_(atelier)]].",
        "[h3]Mysterious series[/h3]",
        "* [[atelier_sophie]] - Atelier Sophie: The Alchemist of the Mysterious Book (2015), starring [[sophie_neuenmuller]] and [[plachta]].",
        "* [[atelier_firis]] - Atelier Firis: The Alchemist and the Mysterious Journey (2016), starring [[firis_mistlud]].",
        "* [[atelier_lydie_&_suelle]] - Atelier Lydie & Suelle: The Alchemists and the Mysterious Paintings (2017), starring [[lydie_malen]] and [[suelle_malen]].",
        "* [[atelier_sophie_2]] - Atelier Sophie 2: The Alchemist of the Mysterious Dream (2022).",
        "[h3]Secret series[/h3]",
        "The Secret trilogy shares one continuing cast led by [[reisalin_stout]], commonly called Ryza:",
        "* [[atelier_ryza]] is the umbrella tag for Atelier Ryza material.",
        "* [[atelier_ryza_1]] - Atelier Ryza: Ever Darkness & the Secret Hideout (2019).",
        "* [[atelier_ryza_2]] - Atelier Ryza 2: Lost Legends & the Secret Fairy (2020).",
        "* [[atelier_ryza_3]] - Atelier Ryza 3: Alchemist of the End & the Secret Key (2023).",
        "The first game also received a 2023 television anime adaptation; use the available Ryza copyright tags together with character tags when appropriate.",
        "[h3]Envisioned series[/h3]",
        "* [[atelier_yumia]] - Atelier Yumia: The Alchemist of Memories & the Envisioned Land (2025), starring [[yumia_liessfeldt]].",
        "* [[atelier_karia]] - Atelier Karia: The Night Kingdom & the Guide of Memories (announced for February 25, 2027), starring [[karia_(atelier)]]. Its story takes place two years after Atelier Yumia.",
        "[h3]Resleriana[/h3]",
        "* [[atelier_resleriana]] - Atelier Resleriana: Forgotten Alchemy and the Polar Night Liberator, a crossover-oriented mobile title featuring original protagonists and returning alchemists from across the franchise.",
        "* [[atelier_resleriana:_the_red_alchemist_&_the_white_guardian]] - a 2025 offline role-playing game set in the Resleriana world. It features new protagonists and returning characters; [[valeria_(atelier)]] is among the characters associated with this branch.",
        "[h2]Spin-offs and related games[/h2]",
        "Handheld and side entries with local tags include [[atelier_annie]], [[atelier_lise]], [[atelier_lina]] and [[atelier_elkrone]]. The franchise also includes crossover projects such as Nelke & the Legendary Alchemists, which bring together characters from otherwise separate continuities.",
        "[h2]Tagging notes[/h2]",
        "Use [[atelier_(series)]] for material belonging to Gust's Atelier franchise. Add the most specific game or subseries copyright tag when the source is identifiable, plus the applicable character tags. Remakes and DX editions generally retain the tag of their original title unless a more specific Gelbooru tag exists.",
        "",
        "Do not confuse the franchise with the unrelated adult-game copyright [[atelier_kaguya]], the manga/anime [[tongari_boushi_no_atelier]] (Witch Hat Atelier), artist tags containing 'atelier', or the generic word 'atelier'.",
        "[h2]See also[/h2]",
        "* [[atelier_ryza]] - existing Gelbooru wiki for the Secret/Ryza branch.",
        "[h2]External links[/h2]",
        "* Official Atelier series portal: https://atelier.games/",
        "* Official Atelier Karia website: https://atelier.games/karia/us/",
        "* Official Atelier Yumia website: https://atelier.games/yumia/us/",
        "* Official Atelier Resleriana: The Red Alchemist & the White Guardian website: https://atelier.games/resleriana_rw/us/",
    ]

    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / "atelier_series.json"
    payload = {
        "tag": TAG,
        "template": "copyright",
        "source": compact("\n".join(lines)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {destination} | copyright tags {len(GAME_TAGS) + 1} | "
        f"character tags {len(CHARACTER_TAGS)}"
    )


if __name__ == "__main__":
    main()
