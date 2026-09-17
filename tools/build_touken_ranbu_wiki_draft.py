from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Games"
TAG = "touken_ranbu"

SANJOU = [
    "mikazuki_munechika", "kogitsunemaru", "imanotsurugi", "iwatooshi",
    "ishikirimaru",
]
AWATAGUCHI = [
    "ichigo_hitofuri", "nakigitsune", "namazuo_toushirou",
    "honebami_toushirou", "hirano_toushirou", "atsushi_toushirou",
    "maeda_toushirou", "akita_toushirou", "hakata_toushirou",
    "midare_toushirou", "gokotai", "yagen_toushirou", "gotou_toushirou",
    "shinano_toushirou", "houchou_toushirou", "mouri_toushirou",
]
RAI_AND_SAMONJI = [
    "akashi_kuniyuki", "aizen_kunitoshi", "hotarumaru",
    "kousetsu_samonji", "souza_samonji", "sayo_samonji", "taikou_samonji",
]
STARTERS = [
    "kashuu_kiyomitsu", "kasen_kanesada", "mutsunokami_yoshiyuki_(touken_ranbu)",
    "yamanbagiri_kunihiro", "hachisuka_kotetsu",
]
SHINSENGUMI = [
    "kashuu_kiyomitsu", "yamato-no-kami_yasusada", "nagasone_kotetsu",
    "izumi-no-kami_kanesada", "horikawa_kunihiro",
]
DATE = [
    "shokudaikiri_mitsutada", "ookurikara", "tsurumaru_kuninaga",
    "taikogane_sadamune",
]
OTHER_NOTABLE = [
    "tonbokiri_(touken_ranbu)", "nihongou_(touken_ranbu)", "otegine",
    "sengo_muramasa_(touken_ranbu)", "higekiri_(touken_ranbu)",
    "hizamaru_(touken_ranbu)", "shishiou_(touken_ranbu)",
    "ookanehira_(touken_ranbu)", "uguisumaru", "kogarasumaru_(touken_ranbu)",
    "heshikiri_hasebe", "fudou_yukimitsu", "yamanbagiri_chougi",
    "nansen_ichimonji", "sanchoumou", "ichimonji_norimune",
    "buzen_gou", "kuwana_gou_(touken_ranbu)", "matsui_gou", "samidare_gou",
    "murakumo_gou", "inaba_gou", "kotegiri_gou", "suishinshi_masahide",
    "minamoto_kiyomaro", "chiyoganemaru", "chatan_nakiri",
    "chiganemaru_(touken_ranbu)",
]
GROUPS = [
    "awataguchi_family_(touken_ranbu)", "sanjou_school_swords_(touken_ranbu)",
    "samonji_family_(touken_ranbu)", "starter_five_(touken_ranbu)",
    "shinsengumi_swords_(touken_ranbu)", "date_clan_blades_(touken_ranbu)",
    "four_rare_tachi_(touken_ranbu)", "nobunaga's_blades_(touken_ranbu)",
]
SYSTEM = [
    "saniwa_(touken_ranbu)", "female_saniwa_(touken_ranbu)",
    "male_saniwa_(touken_ranbu)", "kiwame_(touken_ranbu)",
    "tousou_(touken_ranbu)", "kebiishi_(touken_ranbu)",
]
ADAPTATIONS = [
    "touken_ranbu:_hanamaru", "katsugeki/touken_ranbu",
    "musical_touken_ranbu", "butai_touken_ranbu",
]
LINKS = list(dict.fromkeys([
    *SANJOU, *AWATAGUCHI, *RAI_AND_SAMONJI, *STARTERS, *SHINSENGUMI,
    *DATE, *OTHER_NOTABLE, *GROUPS, *SYSTEM, *ADAPTATIONS,
]))


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def linked(tags: list[str]) -> str:
    return ", ".join(f"[[{tag}]]" for tag in tags)


def main() -> None:
    connection = sqlite3.connect(DB)
    missing = [tag for tag in [TAG, *LINKS] if not connection.execute(
        "SELECT 1 FROM tags WHERE name=?", (tag,)
    ).fetchone()]
    if missing:
        raise SystemExit(f"Missing local tags: {missing}")

    lines = [
        "[b]Touken Ranbu[/b] is a Japanese sword-raising simulation and media franchise created by Nitroplus and DMM Games (now EXNOA). The browser game Touken Ranbu ONLINE launched in Japan in 2015 and later expanded to mobile devices. Its characters, the Touken Danshi, are personifications of historical Japanese swords.",
        "",
        "In the year 2205, History Retrograde forces attack the past in order to alter history. A player-character called the Saniwa awakens the spirits of swords as warriors and sends them through time to preserve the established course of history. The Saniwa's base is the Honmaru.",
        "[h2]Terminology and gameplay[/h2]",
        "* Touken Danshi - sword warriors based on named historical blades. Their weapon classes include tantou, wakizashi, uchigatana, tachi, ootachi, yari, naginata and tsurugi.",
        "* [[saniwa_(touken_ranbu)]] - the player character and master of a Honmaru. Official adaptations may depict their own distinct Saniwa.",
        "* [[female_saniwa_(touken_ranbu)]] and [[male_saniwa_(touken_ranbu)]] - fan depictions where the Saniwa's gender is specified.",
        "* [[kiwame_(touken_ranbu)]] - strengthened forms obtained after special training, with changed costumes and character art.",
        "* [[tousou_(touken_ranbu)]] - troop equipment accompanying the swords in battle.",
        "* [[kebiishi_(touken_ranbu)]] - powerful time-policing enemies that can appear on repeatedly cleared maps.",
        "[h2]Starter swords[/h2]",
        "The player initially chooses one of the five swords grouped by [[starter_five_(touken_ranbu)]]:",
        f"* {linked(STARTERS)}",
        "[h2]Sanjou school[/h2]",
        "The [[sanjou_school_swords_(touken_ranbu)]] group includes:",
        f"* {linked(SANJOU)}",
        "[h2]Awataguchi school[/h2]",
        "The large [[awataguchi_family_(touken_ranbu)]] group includes:",
        f"* {linked(AWATAGUCHI)}",
        "[h2]Rai and Samonji schools[/h2]",
        "Rai swords:",
        f"* {linked(RAI_AND_SAMONJI[:3])}",
        "",
        "The [[samonji_family_(touken_ranbu)]]:",
        f"* {linked(RAI_AND_SAMONJI[3:])}",
        "[h2]Historical owner and clan groupings[/h2]",
        "The [[shinsengumi_swords_(touken_ranbu)]]:",
        f"* {linked(SHINSENGUMI)}",
        "",
        "The [[date_clan_blades_(touken_ranbu)]]:",
        f"* {linked(DATE)}",
        "",
        "Gelbooru also provides collective tags such as [[nobunaga's_blades_(touken_ranbu)]] and [[four_rare_tachi_(touken_ranbu)]]. These describe a historical or fandom grouping and should supplement the individual character tags.",
        "[h2]Other prominent Touken Danshi[/h2]",
        f"* {linked(OTHER_NOTABLE)}",
        "[h2]Anime[/h2]",
        "* [[touken_ranbu:_hanamaru]] - slice-of-life-oriented anime continuity beginning in 2016, followed by a second television season and the 2022 Setsugetsuka film trilogy.",
        "* [[katsugeki/touken_ranbu]] - 2017 action-oriented anime produced by ufotable, with its own Saniwa and Honmaru continuity.",
        "* Touken Ranbu Kai: Kyoden Moyuru Honnouji - 2024 anime adaptation based on the stage-play continuity.",
        "",
        "These adaptations are separate interpretations rather than consecutive seasons of one shared anime continuity.",
        "[h2]Games, stage productions and other media[/h2]",
        "Touken Ranbu Warriors is a 2022 action game developed by Omega Force and Ruby Party. The franchise also includes [[musical_touken_ranbu]], the separate [[butai_touken_ranbu]] stage-play series, live-action films, manga, novels and numerous collaborations with museums and institutions connected to the historical swords.",
        "[h2]Tagging notes[/h2]",
        "Use [[touken_ranbu]] as the franchise copyright tag. Add every depicted Touken Danshi individually. Use an adaptation tag when the artwork specifically follows that adaptation's costume, Saniwa, cast or continuity.",
        "",
        "The historical sword, its former owner and the Touken Danshi are not interchangeable subjects. Do not tag an unrelated depiction of a historical sword or person as Touken Ranbu solely because that sword inspired a character. Add [[kiwame_(touken_ranbu)]] only for an identifiable Kiwame form.",
        "[h2]External links[/h2]",
        "* Official Touken Ranbu ONLINE website: https://www.toukenranbu.jp/",
        "* Official character list: https://www.toukenranbu.jp/character/",
        "* Official media-mix portal: https://www.toukenranbu.jp/mediamix/",
        "* Official Hanamaru overview: https://www.toukenranbu.jp/mediamix/hanamaru-toukenranbu/",
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
    print(f"wrote {destination} | validated tags {len(LINKS)}")


if __name__ == "__main__":
    main()
