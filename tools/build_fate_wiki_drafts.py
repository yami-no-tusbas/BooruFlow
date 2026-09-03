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
LEGACY_SECTIONS = ROOT / "tools/data/fate_legacy_sections.json"

BRANCH_INDEXES = {
    "list_of_fate_stay_night_characters": {
        "title": "Fate/stay night",
        "copyrights": ["fate/stay_night", "fate/hollow_ataraxia"],
        "sections": ["Fate/Stay Night", "Fate/Hollow Ataraxia"],
        "patterns": ["%(fate/stay_night)%"],
    },
    "list_of_fate_zero_characters": {
        "title": "Fate/Zero",
        "copyrights": ["fate/zero"],
        "sections": ["Fate/Zero"],
        "patterns": ["%(fate/zero)%"],
    },
    "list_of_fate_extra_characters": {
        "title": "Fate/Extra and the Extraverse",
        "copyrights": ["fate/extra", "fate/extra_ccc", "fate/extra_ccc_fox_tail", "fate/extella", "fate/extella_link"],
        "sections": ["Fate/Extra", "Fate/Extra CCC", "Fate/Extra CCC Fox Tail", "Fate/Extella", "Fate/Extella Link"],
        "patterns": ["%(fate/extra)%", "%(fate/extella)%"],
    },
    "list_of_fate_apocrypha_characters": {
        "title": "Fate/Apocrypha",
        "copyrights": ["fate/apocrypha"],
        "sections": ["Fate/Apocrypha"],
        "patterns": ["%(fate/apocrypha)%"],
    },
    "list_of_fate_grand_order_characters": {
        "title": "Fate/Grand Order",
        "copyrights": ["fate/grand_order", "fate/grand_order_arcade", "fate/grand_carnival"],
        "sections": ["Fate/Grand Order", "Fate/Grand Order Arcade", "Fate/Grand Carnival"],
        "patterns": ["%(fate/grand_order)%", "%(fate/grand_order_arcade)%"],
    },
    "list_of_lord_el-melloi_ii_case_files_characters": {
        "title": "Lord El-Melloi II Case Files",
        "copyrights": ["lord_el-melloi_ii_case_files"],
        "sections": ["Lord El-Melloi II Case Files"],
        "patterns": ["%(lord_el-melloi_ii)%"],
    },
    "list_of_fate_strange_fake_characters": {
        "title": "Fate/strange Fake",
        "copyrights": ["fate/strange_fake"],
        "sections": ["Fate/Strange Fake"],
        "patterns": ["%(fate/strange_fake)%"],
    },
}

VARIANT_WORDS = re.compile(
    r"(?:swimsuit|summer|santa|christmas|halloween|alter|lily|bride|first_ascension|"
    r"second_ascension|third_ascension|costume|dress|casual_wear|corrupted|adult|young|"
    r"child|school_uniform|maid|idol|rider\)|saber\)|archer\)|lancer\)|caster\)|"
    r"assassin\)|berserker\)|ruler\)|avenger\)|foreigner\)|mooncancer\)|alter_ego\))",
    re.IGNORECASE,
)

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
    ("fate/zero", "missing", "", "Wiki search redirected to account creation flow; prepare a new copyright page."),
    ("fate/hollow_ataraxia", "missing", "", "Wiki search redirected to account creation flow; prepare a new copyright page."),
    ("lord_el-melloi_ii_case_files", "existing-stale", "2015-08-13", "Preserve the original premise and expand the 2015 copyright page."),
    ("fate/strange_fake", "missing", "", "Wiki search redirected to account creation flow; prepare a new copyright page."),
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


def work_sources() -> dict[str, str]:
    return {
        "fate/zero": "\n".join([
            "[b]Fate/Zero[/b] is a prequel to [[fate/stay_night]], written by Gen Urobuchi under the supervision of Kinoko Nasu and illustrated by Takashi Takeuchi. The original light novels were released by TYPE-MOON between 2006 and 2007; ufotable later adapted the story as a television anime.",
            "",
            "Set ten years before Fate/stay night, it follows the Fourth Holy Grail War in Fuyuki. Seven Masters summon seven Servants and fight for the Grail, while Kiritsugu Emiya enters the conflict as the Einzbern family's representative and Saber's Master. The war establishes many of the events, relationships and disasters inherited by the Fifth Holy Grail War.",
            "[h2]Principal Masters and Servants[/h2]",
            "* [[emiya_kiritsugu]] and [[saber_(fate)]]",
            "* [[tohsaka_tokiomi]] and [[gilgamesh_(fate)]]",
            "* [[waver_velvet]] and [[iskandar_(fate)]]",
            "* [[kayneth_el-melloi_archibald]] and [[diarmuid_ua_duibhne_(lancer)_(fate)]]",
            "* [[matou_kariya]] and [[lancelot_(fate/zero)]]",
            "* [[uryuu_ryuunosuke]] and [[gilles_de_rais_(caster)_(fate)]]",
            "* [[kotomine_kirei]] and [[assassin_(fate/zero)]]",
            "[h2]Other principal characters[/h2]",
            "* [[irisviel_von_einzbern]]", "* [[hisau_maiya]]", "* [[sola-ui_nuada-re_sophia-ri]]", "* [[tohsaka_aoi]]", "* [[kotomine_risei]]",
            "[h2]Character index[/h2]", "See [[list_of_fate_zero_characters]] for supporting characters, alternate forms, Noble Phantasms and associated terminology.",
            "[h2]Tagging notes[/h2]",
            "Use [[fate/zero]] for material from the novels or their anime adaptation. Add individual character tags and established weapon or Noble Phantasm tags. Use [[fate_(series)]] only when the image is franchise-wide or crosses multiple Fate branches.",
            "[h2]Related works[/h2]", "* [[fate/stay_night]]", "* [[lord_el-melloi_ii_case_files]]", "* [[fate_(series)]]",
            "[h2]External links[/h2]", "* Official anime website: https://www.fate-zero.jp/", "* TYPE-MOON official website: https://typemoon.com/",
        ]),
        "fate/hollow_ataraxia": "\n".join([
            "[b]Fate/hollow ataraxia[/b] is a 2005 visual novel and follow-up to [[fate/stay_night]]. Rather than continuing only one of the original game's mutually exclusive routes, it places the familiar cast in a repeating four-day period that combines an everyday ensemble story with a new Holy Grail mystery.",
            "",
            "The principal new viewpoint character is [[bazett_fraga_mcremitz]], the originally appointed Master of Lancer. Her partnership with [[angra_mainyu_(fate)]] and the intervention of [[caren_hortensia]] gradually reveal the nature of the loop. The game also expands the lives of many supporting Fate/stay night characters through daytime and nighttime events.",
            "[h2]Principal new characters[/h2]", "* [[bazett_fraga_mcremitz]]", "* [[angra_mainyu_(fate)]]", "* [[caren_hortensia]]", "* [[luviagelita_edelfelt]]",
            "[h2]Character index[/h2]", "The returning and newly introduced cast is included in [[list_of_fate_stay_night_characters]]; a second Hollow Ataraxia list is unnecessary.",
            "[h2]Tagging notes[/h2]", "Use [[fate/hollow_ataraxia]] when the work's four-day-loop context, new cast, scenes or characteristic costumes are identifiable. Tag returning characters individually and add their established alternate-form tags when applicable.",
            "[h2]Related works[/h2]", "* [[fate/stay_night]]", "* [[fate_(series)]]",
            "[h2]External links[/h2]", "* TYPE-MOON official website: https://typemoon.com/",
        ]),
        "lord_el-melloi_ii_case_files": "\n".join([
            "[b]Lord El-Melloi II Case Files[/b] is a mystery light-novel series written by Makoto Sanda and illustrated by Mineji Sakamoto. It follows the adult [[lord_el-melloi_ii]], formerly [[waver_velvet]] of [[fate/zero]], as a professor in the Clock Tower's Department of Modern Magecraft.",
            "",
            "The story retains the earlier Gelbooru article's central description: Lord El-Melloi II investigates incidents involving magecraft together with [[gray_(fate)]], his apprentice and assistant whose appearance and history are connected to King Arthur. The cases emphasize the rules, families and politics of the Mage's Association rather than staging another conventional Holy Grail War.",
            "[h2]Principal characters[/h2]", "* [[lord_el-melloi_ii]]", "* [[gray_(fate)]]", "* [[reines_el-melloi_archisorte]]", "* [[flat_escardos]]", "* [[svin_glascheit]]", "* [[yvette_l._lehrman]]", "* [[melvin_weins]]", "* [[adashino_hishiri]]",
            "[h2]Adaptations and continuation[/h2]", "The novels received manga and anime adaptations. The television anime expands the opening cases and adapts the Rail Zeppelin arc. The story later continues under the title The Adventures of Lord El-Melloi II.",
            "[h2]Character index[/h2]", "See [[list_of_lord_el-melloi_ii_case_files_characters]] for the consolidated cast and associated character forms.",
            "[h2]Tagging notes[/h2]", "Use [[lord_el-melloi_ii_case_files]] for the novels, manga or anime. Add [[fate/zero]] only when the image specifically depicts Waver's Fourth Holy Grail War era rather than his adult Case Files role.",
            "[h2]Related works[/h2]", "* [[fate/zero]]", "* [[fate/stay_night]]", "* [[fate_(series)]]",
            "[h2]External links[/h2]", "* Official anime website: https://anime.elmelloi.com/", "* TYPE-MOON official website: https://typemoon.com/",
        ]),
        "fate/strange_fake": "\n".join([
            "[b]Fate/strange Fake[/b] is a Fate spin-off written by Ryohgo Narita and illustrated by Shizuki Morii. It began as an April Fools project before being developed into an ongoing light-novel and manga series.",
            "",
            "The story takes place in Snowfield, Nevada, where an imitation of Fuyuki's Holy Grail War produces an incomplete False Holy Grail War. Its flawed ritual, unusual Master-Servant pairs and the later emergence of a True Holy Grail War draw magi, the Church, police and other factions into the city.",
            "[h2]Principal characters[/h2]", "* [[tine_chelc]] and [[gilgamesh_(fate)]]", "* [[flat_escardos]] and [[jack_the_ripper_(berserker)_(fate)]]", "* [[wolf_(fate)]] and [[enkidu_(fate)]]", "* [[kuruoka_tsubaki]] and [[pale_rider_(fate)]]", "* [[orlando_reeve]] and [[francois_prelati_(fate)]]", "* [[jester_karture]] and [[no_name_assassin_(fate)]]", "* [[sigma_(fate)]] and [[watcher_(fate)]]",
            "[h2]Anime[/h2]", "The novels received the animated special Whispers of Dawn followed by a television anime adaptation. Gelbooru uses the same copyright tag for the novels, manga and animation unless a more specific tag is established.",
            "[h2]Character index[/h2]", "See [[list_of_fate_strange_fake_characters]] for the full locally validated cast and associated forms.",
            "[h2]Tagging notes[/h2]", "Use [[fate/strange_fake]] for material from the Snowfield Holy Grail War. Characters shared with other Fate works should still receive their individual tags; add another copyright only when that other continuity is actually represented.",
            "[h2]Related works[/h2]", "* [[fate/stay_night]]", "* [[fate/zero]]", "* [[lord_el-melloi_ii_case_files]]", "* [[fate_(series)]]",
            "[h2]External links[/h2]", "* Official anime website: https://fate-strange-fake.com/", "* TYPE-MOON official website: https://typemoon.com/",
        ]),
    }


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


def load_legacy_sections() -> dict[str, list[str]]:
    entries = json.loads(LEGACY_SECTIONS.read_text(encoding="utf-8"))
    return {entry["heading"]: entry["tags"] for entry in entries}


def branch_index_source(
    connection: sqlite3.Connection,
    page_tag: str,
    config: dict[str, object],
    legacy: dict[str, list[str]],
) -> tuple[str, dict[str, object]]:
    rows = connection.execute("SELECT name, post_count, category FROM tags").fetchall()
    db = {name: (count, category) for name, count, category in rows}
    discovered: set[str] = set()
    historical: set[str] = set()
    for heading in config["sections"]:
        historical.update(legacy.get(heading, []))
    discovered.update(tag for tag in historical if tag in db)

    patterns = config["patterns"]
    if patterns:
        where = " OR ".join("name LIKE ?" for _ in patterns)
        discovered.update(
            name for name, in connection.execute(
                f"SELECT name FROM tags WHERE category = 4 AND post_count >= 5 AND ({where})",
                patterns,
            )
        )

    copyrights = set(config["copyrights"])
    characters = sorted(tag for tag in discovered if db[tag][1] == 4 and tag not in copyrights)
    variants = [tag for tag in characters if VARIANT_WORDS.search(tag)]
    principals = [tag for tag in characters if tag not in set(variants)]
    related = sorted(tag for tag in discovered if db[tag][1] == 0)
    invalid = sorted(historical - set(db))
    database_additions = sorted(discovered - historical)

    lines = [
        f"This branch index covers the character tags associated with [[{config['copyrights'][0]}]] and its directly related works. It is split from [[list_of_fate_series_characters]] because the complete Fate index exceeds Gelbooru's wiki body storage limit.",
        "",
        "[b]Legend:[/b] [b]bold[/b] = 10,000+ posts; [i]italic[/i] = 1,000+ posts; * = fewer than 50 posts; ** = fewer than 25 posts. Counts are taken from the local Gelbooru tag database.",
        "[h2]Principal and supporting characters[/h2]",
    ]
    lines.extend(f"* {styled(tag, db[tag][0])}" for tag in principals)
    lines.append("[h2]Alternate classes, forms and costumes[/h2]")
    lines.append("These are established Gelbooru character tags for identifiable variants. Use the base character tag as well when Gelbooru's current tag relationships call for it.")
    lines.extend(f"* {styled(tag, db[tag][0])}" for tag in variants)
    if related:
        lines.append("[h2]Associated objects and terminology[/h2]")
        lines.append("These non-character tags were present in the former combined index and are retained separately for discovery.")
        lines.extend(f"* [[{tag}]]" for tag in related)
    lines += [
        "[h2]Related pages[/h2]",
        "* [[fate_(series)]]",
        "* [[list_of_fate_series_characters]]",
    ]
    lines.extend(f"* [[{tag}]]" for tag in config["copyrights"])
    lines += [
        "[h2]Maintenance notes[/h2]",
        "This page was rebuilt from the former imported Fate character list and checked against the local Gelbooru tag database. Historical names not present as current tags were omitted instead of creating broken links.",
        "[h2]External links[/h2]",
        "* TYPE-MOON official website: https://typemoon.com/",
        "* Fate/Grand Order official website: https://www.fate-go.jp/",
    ]
    source = "\n".join(lines)
    stats = {
        "characters": len(principals),
        "variants": len(variants),
        "related": len(related),
        "invalid_historical": len(invalid),
        "invalid_historical_tags": invalid,
        "database_additions": database_additions,
        "bytes": len(compact(source).encode("utf-8")),
    }
    if stats["bytes"] > 60_000:
        raise ValueError(f"{page_tag} is too large for Gelbooru: {stats['bytes']:,} bytes")
    return source, stats


def character_list_source(connection: sqlite3.Connection) -> str:
    counts = dict(connection.execute("SELECT name, post_count FROM tags WHERE category = 4"))
    lines = [
        "This page is a consolidated index of characters and character-specific variants used across [[fate_(series)]]. A character is first listed under the branch where the version became prominent; appearances in later crossovers are not repeated unless Gelbooru has a distinct tag.",
        "",
        "[b]About this index:[/b] Fate has too many character, costume, class and event-form tags to fit safely into a single Gelbooru wiki article. An earlier exhaustive draft exceeded the database limit for the wiki body and was rejected with a 'Data too long' error. This central page therefore presents the principal casts and links to branch-specific indexes, where the complete lists and variants can be maintained without exceeding Gelbooru's storage limit.",
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
        "* [[list_of_lord_el-melloi_ii_case_files_characters]]",
        "* [[list_of_fate_strange_fake_characters]]",
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
    branch_stats: dict[str, dict[str, object]] = {}
    with sqlite3.connect(DB) as connection:
        written = int(write_json(OUT, "fate_(series)", "copyright", portal_source(), uploaded))
        list_source = character_list_source(connection)
        list_bytes = len(compact(list_source).encode("utf-8"))
        if list_bytes > 60_000:
            raise ValueError(f"Central character list is too large for Gelbooru: {list_bytes:,} bytes")
        written += int(write_json(OUT, "list_of_fate_series_characters", "general", list_source, uploaded))
        for tag, source in work_sources().items():
            written += int(write_json(OUT / "works", tag, "copyright", source, uploaded))
        legacy = load_legacy_sections()
        for page_tag, config in BRANCH_INDEXES.items():
            source, stats = branch_index_source(connection, page_tag, config, legacy)
            branch_stats[page_tag] = stats
            written += int(write_json(OUT / "branch_indexes", page_tag, "general", source, uploaded))
        for source, characters in CHARACTERS.items():
            for tag, description in characters.items():
                written += int(write_json(OUT / "characters" / source, tag, "character", character_source(tag, source, description), uploaded))

    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "wiki_audit.tsv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t")
        writer.writerow(["tag", "status", "last_update", "action"])
        writer.writerows(AUDIT)
    (OUT / "branch_index_report.json").write_text(
        json.dumps(branch_stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Fate drafts written: {written}; central list: {list_bytes:,} bytes; uploaded exclusions: {len(uploaded)}")


if __name__ == "__main__":
    main()
