from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/World Witches"


def bullets(tags: list[str]) -> str:
    return "\n".join(f"* [[{tag}]]" for tag in tags)


STRIKE_CAST = [
    "miyafuji_yoshika", "sakamoto_mio", "minna-dietlinde_wilcke",
    "lynette_bishop", "perrine_h._clostermann", "erica_hartmann",
    "gertrud_barkhorn", "francesca_lucchini", "charlotte_e._yeager",
    "sanya_v._litvyak", "eila_ilmatar_juutilainen", "hattori_shizuka",
]

BRAVE_CAST = [
    "karibuchi_hikari", "karibuchi_takami", "kanno_naoe",
    "nikka_edvardine_katajainen", "waltrud_krupinski",
    "aleksandra_i._pokryshkin", "georgette_lemare", "shimohara_sadako",
    "edytha_rossmann", "gundula_rall",
]

LUMINOUS_CAST = [
    "virginia_robertson", "shibuya_inori", "aila_paivikki_linnamaa",
    "lyudmila_andreyevna_ruslanova", "eleonore_giovanna_gassion",
    "joanna_elizabeth_stafford", "sylvie_cariello",
    "maria_magdalena_dietrich", "manaia_matawhaura_hato",
    "grace_maitland_steward",
]


PAGES: dict[str, list[str]] = {
    "world_witches_series": [
        "[b]World Witches Series[/b] is the umbrella name for the military fantasy and alternate-history multimedia franchise created from the character and mechanical concepts of [[shimada_fumikane]]. Earlier material and the best-known 501st Joint Fighter Wing branch are commonly titled Strike Witches.",
        "",
        "In this setting, the Neuroi invade Earth instead of the nations fighting the historical Second World War. Young women with magical power, called Witches, use aircraft-inspired Striker Units to fly and oppose them. The stories follow several international units, countries and periods rather than a single cast.",
        "[h2]Animated works[/h2]",
        "* [[strike_witches]] - the central 501st Joint Fighter Wing anime branch, including its first two seasons.",
        "* [[strike_witches_gekijouban]] - the 2012 film.",
        "* [[strike_witches:_operation_victory_arrow]] - three OVAs set after the second season and before the film.",
        "* [[brave_witches]] - the 502nd Joint Fighter Wing television series.",
        "* [[strike_witches:_road_to_berlin]] - the 2020 third television season of the 501st story.",
        "* [[luminous_witches]] - a 2022 series about a non-combat aviation music band.",
        "* Strike Witches: 501st Joint Fighter Wing Take Off! and World Witches Take Off! are comedy spin-offs; no separate canonical copyright tag was found locally.",
        "[h2]Light novels and manga[/h2]",
        "* [[strike_witches:_suomus_misfits_squadron]]",
        "* [[strike_witches:_aurora_no_majo]]",
        "* [[strike_witches:_kurenai_no_majo-tachi]]",
        "* [[strike_witches:_katayoku_no_majo-tachi]]",
        "* [[strike_witches_zero]]",
        "* [[witches_of_africa]]",
        "* [[noble_witches]]",
        "[h2]Other projects[/h2]",
        "* [[strike_witches_1991]] - an unofficial Gulf War-era fan project, not part of the official continuity.",
        "* [[strike_witches_1940]] - an unofficial Battle of Britain-inspired project associated with the same fan creator.",
        "* [[strike_witches_(lionheart_witch)]] - Dan Kanemitsu's unofficial land-Witch doujinshi project.",
        "* [[world_witches_x]] - a 2025-2026 Japanese mobile game.",
        "[h2]Tagging notes[/h2]",
        "Use [[world_witches_series]] as the franchise-level copyright and add the most specific work tag that can be identified. Do not use [[strike_witches]] automatically for characters belonging only to Brave Witches, Luminous Witches or another unit.",
        "[h2]External links[/h2]",
        "* Official World Witches portal: https://w-witch.jp/",
        "* World Witches media guide: https://worldwitches.fandom.com/wiki/World_Witches_media",
    ],
    "strike_witches": [
        "[b]Strike Witches[/b] is the best-known branch of [[world_witches_series]], created from the military-themed mecha-musume concepts of [[shimada_fumikane]]. It began with magazine illustration columns and expanded into light novels, manga, games and animation. The first television anime was produced by Gonzo; later animated installments used other studios.",
        "",
        "The following overview preserves the substance of the long-standing Gelbooru article: in this alternate history, the alien Neuroi attack Earth in 1939 in place of the historical Second World War. Humanity's most effective defenders are magically gifted young women using armored, aircraft-inspired leg machines called Striker Units. Dr. Miyafuji Ichirou, father of [[miyafuji_yoshika]], helped develop the technology.",
        "",
        "Yoshika wishes to become a healer like her mother and grandmother and initially rejects warfare after losing her father. Recruited by [[sakamoto_mio]], she travels to Britannia, uses a Striker Unit during a Neuroi attack and joins the multinational 501st Joint Fighter Wing. Her compassion, healing magic and relationships with the other Witches form the emotional center of the anime. The franchise deliberately echoes historical nations, aircraft and fighter aces while replacing international war with humanity's common struggle against the Neuroi.",
        "",
        "The original Gelbooru article also highlighted two conspicuous features of the series: everyday clothing is designed around the use of Striker Units and commonly leaves the legs uncovered, and the close bonds between the female cast produce frequent yuri subtext. These observations remain useful when identifying characteristic franchise imagery.",
        "[h2]501st animated continuity[/h2]",
        "* Strike Witches - 2008 first television season.",
        "* Strike Witches 2 - 2010 second season; locally grouped under [[strike_witches]].",
        "* [[strike_witches_gekijouban]] - 2012 film.",
        "* [[strike_witches:_operation_victory_arrow]] - 2014-2015 OVA trilogy.",
        "* [[strike_witches:_road_to_berlin]] - 2020 third season.",
        "[h2]501st Joint Fighter Wing[/h2]" + bullets(STRIKE_CAST),
        "[h2]Related branches[/h2]",
        "* [[brave_witches]]",
        "* [[luminous_witches]]",
        "* [[world_witches_series]]",
        "[h2]External links[/h2]",
        "* Official World Witches portal: https://w-witch.jp/",
        "* Official Road to Berlin site: https://w-witch.jp/strike_witches-rtb/",
    ],
    "strike_witches_1991": [
        "ストライクウィッチーズ1991",
        "",
        "A Strike Witches fan project initiated by ogitsune (ankakecya-han). The project started with his creation of Strike Witches parodies of Maverick and Goose from the movie Top Gun and has since grown into a multiple artist project. The project depicts various fan-made witches who are sent to the Middle East to drive out a Neuroi invasion in a series of events roughly analogous to those of the Gulf War.",
        "",
        "The paragraph above is preserved from the 2012 Gelbooru article, which credited Danbooru's tag description as its source.",
        "[h2]Context and tagging[/h2]",
        "This is an unofficial derivative project and is not part of the canonical [[world_witches_series]] chronology. Use [[strike_witches_1991]] for its original characters and designs; add [[world_witches_series]] only when consistent with Gelbooru's established tagging practice.",
        "[h2]Related tags[/h2]",
        "* [[strike_witches]]",
        "* [[world_witches_series]]",
        "* [[strike_witches_1940]]",
        "[h2]External links[/h2]",
        "* Danbooru tag wiki: https://danbooru.donmai.us/wiki_pages/strike_witches_1991",
    ],
    "strike_witches_1940": [
        "[b]Strike Witches 1940[/b] is an unofficial fan project by ogitsune (ankakecya-han), the creator associated with [[strike_witches_1991]]. It reinterprets characters and events inspired by the 1969 film Battle of Britain as Witches and a Neuroi conflict in 1940.",
        "[h2]Tagging notes[/h2]",
        "This project is not part of the official [[world_witches_series]] continuity. Use [[strike_witches_1940]] for its fan-created cast and designs rather than the main [[strike_witches]] copyright alone.",
        "[h2]Related tags[/h2]",
        "* [[strike_witches_1991]]",
        "* [[world_witches_series]]",
    ],
    "strike_witches_(lionheart_witch)": [
        "[b]The Lionheart Witch[/b] is an unofficial World Witches-inspired doujinshi project created and written by Dan Kanemitsu. It focuses on land-combat Witches and conventional armoured forces in North Africa, drawing heavily on historical tank units and campaigns.",
        "[h2]Tagging notes[/h2]",
        "Use [[strike_witches_(lionheart_witch)]] for characters and designs belonging to this fan project. It is related to the broader setting but is not an official installment of [[world_witches_series]].",
        "[h2]External link[/h2]",
        "* Creator's project articles: https://dankanemitsu.wordpress.com/category/the-lionheart-witch/",
    ],
    "brave_witches": [
        "[b]Brave Witches[/b] is a 2016 television anime spin-off of [[world_witches_series]]. Set between the first and second Strike Witches television seasons, it follows [[karibuchi_hikari]] as she travels from Fuso to Orussia and serves with the 502nd Joint Fighter Wing after her elder sister [[karibuchi_takami]] is incapacitated.",
        "[h2]502nd Joint Fighter Wing[/h2]" + bullets(BRAVE_CAST),
        "[h2]Related tags[/h2]",
        "* [[world_witches_series]]",
        "* [[strike_witches]]",
        "* [[502nd_joint_fighter_wing]]",
        "[h2]External links[/h2]",
        "* Official site: https://w-witch.jp/brave_witches/",
        "* Official character page: https://w-witch.jp/brave_witches/character/",
    ],
    "luminous_witches": [
        "[b]League of Nations Air Force Aviation Magic Band Luminous Witches[/b] is a 2022 television anime in [[world_witches_series]]. Unlike the combat-focused Joint Fighter Wings, its multinational Witches tour the world as an aviation music band to entertain and encourage civilians and soldiers affected by the Neuroi war.",
        "[h2]Members[/h2]" + bullets(LUMINOUS_CAST),
        "[h2]Related tags[/h2]",
        "* [[world_witches_series]]",
        "* [[moffy_(luminous_witches)]]",
        "* [[kyuu-chan_(luminous_witches)]]",
        "[h2]External links[/h2]",
        "* Official site: https://w-witch.jp/luminous/",
        "* Official member directory: https://w-witch.jp/luminous/member/",
    ],
    "strike_witches:_road_to_berlin": [
        "[b]Strike Witches: Road to Berlin[/b] is the 2020 third television season of the 501st Joint Fighter Wing story within [[world_witches_series]]. Set in 1945 after the film, it reunites the 501st for the Allied operation to liberate Berlin and adds [[hattori_shizuka]] to the active unit.",
        "[h2]Characters[/h2]" + bullets(STRIKE_CAST),
        "[h2]Related tags[/h2]",
        "* [[strike_witches]]",
        "* [[world_witches_series]]",
        "[h2]External links[/h2]",
        "* Official site: https://w-witch.jp/strike_witches-rtb/",
        "* Official character page: https://w-witch.jp/strike_witches-rtb/character/",
    ],
    "strike_witches:_suomus_misfits_squadron": [
        "[b]Strike Witches: Suomus Misfits Squadron[/b] is a light-novel branch of [[world_witches_series]] about the Suomus Independent Volunteer Aerial Squadron, an initially disregarded multinational unit sent to Suomus during the Neuroi war.",
        "[h2]Notable characters[/h2]",
        "* [[anabuki_tomoko]]",
        "* [[sakomizu_haruka]]",
        "* [[elizabeth_f._beurling]]",
        "* [[katharine_o'hare]]",
        "* [[ursula_hartmann]]",
        "* [[elma_leivonen]]",
        "* [[giuseppina_ciuinni]]",
        "[h2]Related tags[/h2]",
        "* [[world_witches_series]]",
        "* [[strike_witches:_aurora_no_majo]]",
        "[h2]External link[/h2]",
        "* World Witches Series Wiki: https://worldwitches.fandom.com/wiki/Strike_Witches%3A_Suomus_Misfits_Squadron",
    ],
    "strike_witches:_aurora_no_majo": [
        "[b]Strike Witches: Aurora no Majo[/b] is a two-volume manga branch of [[world_witches_series]] about the earlier career of [[eila_ilmatar_juutilainen]] in Suomus Air Force's 24th Unit. Aurora E. Juutilainen, Eila's elder sister, and several characters connected to the Suomus Misfits Squadron also appear.",
        "[h2]Related tags[/h2]",
        "* [[strike_witches:_suomus_misfits_squadron]]",
        "* [[world_witches_series]]",
        "[h2]External link[/h2]",
        "* World Witches Series Wiki: https://worldwitches.fandom.com/wiki/Strike_Witches%3A_Aurora_no_Majo",
    ],
    "witches_of_africa": [
        "[b]Witches of Africa[/b] is a [[world_witches_series]] manga and illustrated-story branch following Witches and conventional forces on the North African front, particularly the 31st Joint Fighter Squadron Storm Witches.",
        "[h2]Notable characters[/h2]",
        "* [[hanna-justina_marseille]]",
        "* [[raisa_pottgen]]",
        "* [[katou_keiko]]",
        "[h2]Related tags[/h2]",
        "* [[strike_witches]]",
        "* [[world_witches_series]]",
        "[h2]External links[/h2]",
        "* World Witches Series Wiki manga page: https://worldwitches.fandom.com/wiki/Strike_Witches%3A_The_Witches_of_Africa",
        "* World Witches Series Wiki light-novel page: https://worldwitches.fandom.com/wiki/Strike_Witches%3A_The_Witches_of_Africa_-_Kei%27s_Report",
    ],
    "noble_witches": [
        "[b]Noble Witches[/b] is a light-novel and manga branch of [[world_witches_series]] centered on the 506th Joint Fighter Wing, a unit divided into A and B contingents amid political and social tensions in liberated Gallia.",
        "[h2]Related tags[/h2]",
        "* [[world_witches_series]]",
        "* [[strike_witches]]",
        "[h2]External links[/h2]",
        "* World Witches Series Wiki manga page: https://worldwitches.fandom.com/wiki/Noble_Witches%3A_The_506th_Joint_Fighter_Wing",
        "* World Witches Series Wiki light-novel page: https://worldwitches.fandom.com/wiki/Noble_Witches%3A_The_506th_Joint_Fighter_Wing_(light_novel)",
    ],
    "strike_witches:_kurenai_no_majo-tachi": [
        "[b]Strike Witches: Kurenai no Majo-tachi[/b] is a manga branch of [[world_witches_series]] centered on the 504th Joint Fighter Wing in Romagna.",
        "[h2]Related tags[/h2]",
        "* [[world_witches_series]]",
        "* [[strike_witches]]",
        "[h2]External link[/h2]",
        "* World Witches Series Wiki: https://worldwitches.fandom.com/wiki/Strike_Witches%3A_Kurenai_no_Majo-tachi",
    ],
    "strike_witches:_katayoku_no_majo-tachi": [
        "[b]Strike Witches: Katayoku no Majo-tachi[/b] is a manga branch of [[world_witches_series]] following the Isle of Wight Detachment Group in Britannia.",
        "[h2]Related tags[/h2]",
        "* [[world_witches_series]]",
        "* [[strike_witches]]",
        "[h2]External link[/h2]",
        "* World Witches Series Wiki: https://worldwitches.fandom.com/wiki/Strike_Witches%3A_One-Winged_Witches",
    ],
    "strike_witches_zero": [
        "[b]Strike Witches Zero[/b] is a manga prequel branch of [[world_witches_series]]. Its principal completed story, Strike Witches Zero: 1937 Fuso Sea Incident, follows [[sakamoto_mio]], Junko Takei and Tetsuko Wakamoto during their training and the battle that established crucial knowledge about Neuroi cores. A 1939 sequel began but stopped after two chapters.",
        "[h2]Related tags[/h2]",
        "* [[strike_witches]]",
        "* [[world_witches_series]]",
        "[h2]External links[/h2]",
        "* World Witches Series Wiki: https://worldwitches.fandom.com/wiki/Strike_Witches_Zero%3A1937_Fuso_Sea_Incident",
        "* Unfinished 1939 sequel: https://worldwitches.fandom.com/wiki/Strike_Witches_Zero%3A1939_Koukaku_no_majo",
    ],
    "world_witches_x": [
        "[b]World Witches X[/b], also called World Witches Cross or WitchiClo, was a Japanese mobile military-action game based on [[world_witches_series]]. It launched for iOS and Android on October 14, 2025 and ended service on July 3, 2026. It brought together Witches from several franchise units with game-original illustrations, costumes and recorded dialogue.",
        "[h2]Tagging notes[/h2]",
        "Use [[world_witches_x]] for game-specific illustrations, interfaces, promotional material and original designs. Continue to add the depicted character tags and [[world_witches_series]].",
        "[h2]External links[/h2]",
        "* Official site: https://w-witch-cross.com/",
        "* Official release announcement: https://w-witch.jp/news/20251016",
    ],
}


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def main() -> None:
    connection = sqlite3.connect(DB)
    known = {name: (count, category) for name, count, category in connection.execute(
        "SELECT name, post_count, category FROM tags"
    )}
    missing_pages = [tag for tag in PAGES if tag not in known]
    if missing_pages:
        raise SystemExit(f"Missing page tags: {missing_pages}")

    references = sorted({
        match.group(1).strip()
        for lines in PAGES.values()
        for match in re.finditer(r"\[\[([^]]+)]]", "\n".join(lines))
    })
    missing_references = [tag for tag in references if tag not in known]
    if missing_references:
        raise SystemExit(f"Missing referenced tags: {missing_references}")

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    for tag, lines in PAGES.items():
        payload = {
            "tag": tag,
            "template": "copyright",
            "source": compact("\n".join(lines)),
            "updated_at": stamp,
        }
        safe_name = tag.replace(":", "") + ".json"
        destination = OUT / safe_name
        destination.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {destination}")
    print(f"drafts {len(PAGES)} | validated references {len(references)}")


if __name__ == "__main__":
    main()
