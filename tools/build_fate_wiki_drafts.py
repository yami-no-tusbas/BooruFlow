from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Fate"

WORKS = [
    ("Original Fate/stay night continuity", ["fate/stay_night", "fate/zero", "fate/hollow_ataraxia", "lord_el-melloi_ii_case_files", "fate/strange_fake"]),
    ("Extraverse", ["fate/extra", "fate/extra_ccc", "fate/extra_ccc_fox_tail", "fate/extra_last_encore", "fate/extra_record", "fate/extella", "fate/extella_link"]),
    ("Other Holy Grail War timelines", ["fate/apocrypha", "fate/prototype", "fate/prototype:_fragments_of_blue_and_silver", "fate/type_redline", "fate/requiem", "fate:lost_einherjar", "fate/samurai_remnant"]),
    ("Kaleid Liner Prisma Illya", ["fate/kaleid_liner_prisma_illya"]),
    ("Grand Order", ["fate/grand_order", "fate/grand_order_arcade", "fate/grand_carnival", "fate/grand_order:_first_order", "fate/grand_order_waltz_in_the_moonlight/lostroom"]),
    ("Games and comedy spin-offs", ["fate/unlimited_codes", "fate/tiger_colosseum", "fate/empire_of_dirt", "fate/grail_league", "fate/dream_striker", "emiya-san_chi_no_kyou_no_gohan", "fate/mahjong_night", "fate/school_life"]),
]

CORE_CASTS = {
    "Fate/stay night": [
        "emiya_shirou", "tohsaka_rin", "matou_sakura", "saber_(fate)", "archer_(fate)",
        "illyasviel_von_einzbern", "kotomine_kirei", "fujimura_taiga", "matou_shinji",
        "matou_zouken", "kuzuki_souichirou", "mitsuzuri_ayako", "sella_(fate)",
        "leysritt_(fate)", "medea_(fate)", "medusa_(rider)_(fate)",
        "cu_chulainn_(fate/stay_night)", "gilgamesh_(fate)", "heracles_(fate)",
        "sasaki_kojirou_(fate)", "hassan_of_the_cursed_arm_(fate)",
    ],
    "Fate/hollow ataraxia": ["bazett_fraga_mcremitz", "caren_hortensia", "angra_mainyu_(fate)", "luviagelita_edelfelt"],
    "Fate/Zero": [
        "emiya_kiritsugu", "irisviel_von_einzbern", "hisau_maiya", "waver_velvet",
        "iskandar_(fate)", "diarmuid_ua_duibhne_(lancer)_(fate)", "gilles_de_rais_(caster)_(fate)",
        "lancelot_(berserker)_(fate)", "tohsaka_tokiomi", "matou_kariya", "sola-ui_nuada-re_sophia-ri",
    ],
    "Fate/Apocrypha": ["sieg_(fate)", "ruler_(fate/apocrypha)", "mordred_(fate)", "astolfo_(fate)", "amakusa_shirou_tokisada_(fate)"],
    "Fate/Extra and Extella": ["hakuno_kishinami", "nero_claudius_(fate)", "tamamo_no_mae_(fate)", "bb_(fate)", "altera_(fate)", "charlemagne_(fate)"],
    "Fate/Grand Order": ["fujimaru_ritsuka", "mash_kyrielight", "romani_archaman", "leonardo_da_vinci_(fate)", "olga_marie_animusphere", "goredolf_musik", "sion_eltnam_sokaris"],
    "Fate/Samurai Remnant": ["miyamoto_iori", "yamato_takeru_(fate)", "zheng_chenggong_(fate)", "chiemon_(fate)", "takao_dayu_(fate)"],
}

CHARACTERS = {
    "fate_stay_night": {
        "tohsaka_rin": "Rin Tohsaka is one of the three heroines of Fate/stay night and the Master of Archer in the Fifth Holy Grail War. She is the heir to the Tohsaka line, an accomplished magus specializing in jewel magecraft, and Shirou's classmate. Her role changes substantially between the Fate, Unlimited Blade Works and Heaven's Feel routes.",
        "matou_sakura": "Sakura Matou is one of the three heroines of Fate/stay night, Shirou's underclassman and the central heroine of Heaven's Feel. Born Sakura Tohsaka, she was adopted by the Matou family and subjected to its magecraft training. Her ordinary appearance and her Dark Sakura form must be tagged separately when identifiable.",
        "emiya_shirou": "Shirou Emiya is the protagonist of Fate/stay night, the adopted son of Kiritsugu Emiya and a survivor of the Fuyuki fire. His ideal of becoming a hero of justice, his unusual projection magecraft and his relationship with Saber, Rin and Sakura shape the three routes.",
        "saber_(fate)": "Saber is Shirou Emiya's Servant in Fate/stay night and the principal heroine of the Fate route. Her true identity is Artoria Pendragon, the legendary King Arthur reinterpreted as a woman. Distinguish her ordinary Saber appearance from Saber Alter, Saber Lily and later class or event variants.",
        "archer_(fate)": "Archer is Rin Tohsaka's Servant in the Fifth Holy Grail War. Cynical, pragmatic and exceptionally skilled with projection magecraft, he is closely connected to Shirou Emiya and is central to Unlimited Blade Works.",
        "illyasviel_von_einzbern": "Illyasviel von Einzbern, commonly called Illya, is the Einzbern Master of Berserker in the Fifth Holy Grail War. She is the daughter of Kiritsugu Emiya and Irisviel von Einzbern and an artificial Holy Grail vessel.",
        "kotomine_kirei": "Kirei Kotomine is the supervising priest of the Fifth Holy Grail War and a major antagonist of Fate/stay night. A former Executor and participant in the previous war, he has a long and hostile connection to Kiritsugu Emiya.",
        "fujimura_taiga": "Taiga Fujimura is Shirou's English teacher, homeroom teacher and informal guardian. Nicknamed the Tiger of Fuyuki, she provides much of the story's everyday comedy while remaining an important member of Shirou's household.",
        "matou_shinji": "Shinji Matou is Sakura's adoptive older brother, Shirou's schoolmate and the initial apparent Master of Rider. His resentment of his family's declining magecraft and his treatment of Sakura make him an antagonist in several routes.",
        "matou_zouken": "Zouken Matou is the ancient head of the Matou family and a principal antagonist of Heaven's Feel. He prolonged his life through parasitic crest worms and manipulated generations of his family in pursuit of the Holy Grail.",
        "kuzuki_souichirou": "Souichirou Kuzuki is a teacher at Homurahara Academy and the Master associated with Caster. His quiet school persona conceals the combat training of a former assassin.",
        "mitsuzuri_ayako": "Ayako Mitsuzuri is Shirou and Rin's schoolmate and captain of the archery club. She is a capable athlete and one of the principal non-magus students in the Fate/stay night cast.",
        "sella_(fate)": "Sella is one of the homunculus attendants responsible for Illyasviel at the Einzbern castle. She is the stricter and more openly responsible counterpart to Leysritt.",
        "leysritt_(fate)": "Leysritt is an Einzbern homunculus and one of Illyasviel's attendants. Despite her quiet and sometimes absent-minded demeanor, she possesses considerable physical strength and is closely tied to the Grail vessel system.",
        "medea_(fate)": "Medea is the Caster-class Servant of the Fifth Holy Grail War, based on the sorceress of Greek mythology. After escaping her original Master she forms a partnership with Souichirou Kuzuki and establishes her base at Ryuudou Temple.",
        "medusa_(rider)_(fate)": "Medusa is the Rider-class Servant summoned by Sakura Matou and initially controlled through Shinji. Her identity derives from Greek mythology, and her Mystic Eyes and Noble Phantasm Bellerophon are central to her combat appearances.",
        "cu_chulainn_(fate/stay_night)": "Cu Chulainn is the Lancer-class Servant of the Fifth Holy Grail War. The Irish hero is a fast, direct combatant who wields Gae Bolg and is forced to serve Kirei Kotomine during the war.",
        "gilgamesh_(fate)": "Gilgamesh is the Archer-class Servant who survived the Fourth Holy Grail War and remains in Fuyuki. Calling himself the King of Heroes, he attacks through the Gate of Babylon and plays a major antagonistic role in Fate/stay night.",
        "heracles_(fate)": "Heracles is Illyasviel's Berserker-class Servant. His immense strength and the multiple lives granted by God Hand make him one of the most dangerous participants in the Fifth Holy Grail War.",
        "sasaki_kojirou_(fate)": "Sasaki Kojirou is the false Assassin who guards the gate of Ryuudou Temple. Rather than a conventional Heroic Spirit, he is a nameless swordsman summoned into the role associated with the legendary Kojirou.",
        "hassan_of_the_cursed_arm_(fate)": "Hassan of the Cursed Arm is the true Assassin summoned during Heaven's Feel. He serves Zouken Matou and uses his altered arm and Zabaniya to steal an opponent's heart.",
    },
    "fate_hollow_ataraxia": {
        "bazett_fraga_mcremitz": "Bazett Fraga McRemitz is a mage of the Fraga family and the original Master assigned to Lancer. In Fate/hollow ataraxia she becomes one of the central viewpoint characters and wields the counterattack weapon Fragarach.",
        "caren_hortensia": "Caren Hortensia is a Church executor sent to Fuyuki after the Fifth Holy Grail War and a central character of Fate/hollow ataraxia. Her unusual constitution reacts physically to nearby demonic influence.",
        "angra_mainyu_(fate)": "Angra Mainyu is an Avenger-class Servant whose history is bound to the corruption of the Fuyuki Holy Grail. He is central to the repeating four-day world of Fate/hollow ataraxia.",
        "luviagelita_edelfelt": "Luviagelita Edelfelt, commonly called Luvia, is a Finnish magus and rival of Rin Tohsaka. She specializes in jewel magecraft and later appears across several Fate and wider Nasuverse works.",
    },
}

AUDIT = [
    ("fate_(series)", "existing-stale", "2021-05-01", "Preserve the original introduction; reorganize and extend the franchise branches."),
    ("list_of_fate_series_characters", "existing-rebuild", "2022-11-03", "Explicit full rebuild requested; current page mixes 784 character, item, class and meme links."),
    ("tohsaka_rin", "existing-empty", "2017-08-20", "Confirmed empty page."),
    ("matou_sakura", "existing-stale", "2019-03-07", "Confirmed short and outdated page."),
    ("emiya_shirou", "existing-stale", "2015-12-08", "Confirmed short and outdated page."),
]


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def payload(tag: str, template: str, source: str) -> dict[str, str]:
    return {"tag": tag, "template": template, "source": compact(source), "updated_at": datetime.now(timezone.utc).isoformat()}


def uploaded_names() -> set[str]:
    return {p.name.casefold() for folder in OUT.rglob("uploaded") if folder.is_dir() for p in folder.rglob("*.json")}


def write_json(folder: Path, tag: str, template: str, source: str, uploaded: set[str]) -> bool:
    filename = f"{tag.replace('/', '_')}.json"
    if filename.casefold() in uploaded:
        return False
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_text(json.dumps(payload(tag, template, source), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def portal_source() -> str:
    lines = [
        "[b]Fate[/b] is TYPE-MOON's longest-running multimedia franchise and one of the principal branches of Kinoko Nasu's wider Nasuverse. It began with the 2004 visual novel [[fate/stay_night]]. Its stories commonly revolve around Holy Grail Wars in which human Masters summon legendary or historical familiars called Servants, normally assigned to classes such as Saber, Archer and Lancer.",
        "",
        "The franchise contains many separate continuities. The three routes of the original Fate/stay night are mutually exclusive versions of the same conflict, while later works may be prequels, sequels, alternate histories or entirely different Holy Grail systems. Recurring characters generally retain their design and core traits, but their role and history can differ substantially between timelines.",
        "[h2]Franchise character index[/h2]",
        "See [[list_of_fate_series_characters]] for the consolidated character, form, costume, equipment and terminology index.",
    ]
    for heading, tags in WORKS:
        lines.append(f"[h2]{heading}[/h2]")
        lines.extend(f"* [[{tag}]]" for tag in tags)
    lines += [
        "[h2]Tagging notes[/h2]",
        "Use [[fate_(series)]] for franchise-wide material, crossovers between branches, or works whose specific Fate continuity cannot be identified. When a specific work is known, add its copyright tag. Tag the depicted character and any established alternate form or costume tag; do not replace a base character tag with a costume tag unless Gelbooru's tag relationship requires it.",
        "[h2]Related creators and setting[/h2]",
        "* [[type-moon]]", "* [[nasu_kinoko]]", "* [[takeuchi_takashi]]", "* [[nasuverse]]",
        "[h2]External links[/h2]",
        "* TYPE-MOON official website: https://typemoon.com/",
        "* Fate 20th Anniversary official website: https://fate-20th-anniversary.com/",
    ]
    return "\n".join(lines)


def styled(tag: str, count: int) -> str:
    link = f"[[{tag}]]"
    if count >= 10000:
        return f"[b]{link}[/b]"
    if count >= 1000:
        return f"[i]{link}[/i]"
    if count < 25:
        return f"{link}**"
    if count < 50:
        return f"{link}*"
    return link


def character_list_source(connection: sqlite3.Connection) -> str:
    counts = dict(connection.execute("SELECT name, post_count FROM tags WHERE category = 4"))
    lines = [
        "This page is a consolidated index of characters and character-specific variants used across [[fate_(series)]]. A character is first listed under the branch where the version became prominent; appearances in later crossovers are not repeated unless Gelbooru has a distinct tag.",
        "",
        "[b]Legend:[/b] [b]bold[/b] = 10,000+ posts; [i]italic[/i] = 1,000+ posts; * = fewer than 50 posts; ** = fewer than 25 posts. Counts come from the local Gelbooru tag database and are discovery aids, not exact franchise totals.",
    ]
    seen: set[str] = set()
    for heading, tags in CORE_CASTS.items():
        lines.append(f"[h2]{heading}[/h2]")
        for tag in tags:
            if tag in counts:
                lines.append(f"* {styled(tag, counts[tag])}")
                seen.add(tag)

    lines.append("[h2]Branch indexes[/h2]")
    lines.append("Costumes, ascensions, alternate classes and event-specific forms are intentionally kept out of this central page so it remains readable and fits Gelbooru's wiki body limit. They will be maintained in branch-specific indexes as each part of the franchise is audited.")
    lines.extend([
        "* [[list_of_fate_stay_night_characters]]",
        "* [[list_of_fate_zero_characters]]",
        "* [[list_of_fate_extra_characters]]",
        "* [[list_of_fate_apocrypha_characters]]",
        "* [[list_of_fate_grand_order_characters]]",
    ])
    lines += [
        "[h2]Related terminology and equipment[/h2]",
        "The former page interleaved weapons, Noble Phantasms, classes and memes with people. Those subjects should be maintained in dedicated indexes; useful starting tags include [[command_spell]], [[holy_grail_(fate)]], [[saber_class_(fate)]], [[archer_class_(fate)]], [[lancer_class_(fate)]], [[rider_class_(fate)]], [[caster_class_(fate)]], [[assassin_class_(fate)]] and [[berserker_class_(fate)]].",
        "[h2]External links[/h2]",
        "* TYPE-MOON official website: https://typemoon.com/",
        "* Fate/Grand Order official website: https://www.fate-go.jp/",
    ]
    return "\n".join(lines)


def character_source(tag: str, source: str, description: str) -> str:
    copyright_tag = {
        "fate_stay_night": "fate/stay_night",
        "fate_hollow_ataraxia": "fate/hollow_ataraxia",
    }[source]
    return "\n".join([
        "[b]Description:[/b]", description,
        "[h2]Related tags[/h2]", f"* [[{tag}]]", f"* [[{copyright_tag}]]", "* [[fate_(series)]]",
        "[h2]Tagging notes[/h2]",
        f"Use [[{tag}]] for the character. Add the precise work, route-specific form and established costume tags when identifiable.",
        "[h2]External links[/h2]",
        "* Fate/stay night REMASTERED official website: https://typemoon.com/products/f-sn/",
        "* TYPE-MOON official website: https://typemoon.com/",
    ])


def main() -> None:
    uploaded = uploaded_names()
    with sqlite3.connect(DB) as connection:
        written = int(write_json(OUT, "fate_(series)", "copyright", portal_source(), uploaded))
        list_source = character_list_source(connection)
        list_bytes = len(compact(list_source).encode("utf-8"))
        if list_bytes > 60_000:
            raise ValueError(f"Central character list is too large for Gelbooru: {list_bytes:,} bytes")
        written += int(write_json(OUT, "list_of_fate_series_characters", "general", list_source, uploaded))
        for source, characters in CHARACTERS.items():
            for tag, description in characters.items():
                written += int(write_json(OUT / "characters" / source, tag, "character", character_source(tag, source, description), uploaded))

    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "wiki_audit.tsv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t")
        writer.writerow(["tag", "status", "last_update", "action"])
        writer.writerows(AUDIT)
    print(f"Fate drafts written: {written}; central list: {list_bytes:,} bytes; uploaded exclusions: {len(uploaded)}")


if __name__ == "__main__":
    main()
