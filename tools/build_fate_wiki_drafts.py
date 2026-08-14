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
    "tohsaka_rin": "Rin Tohsaka is one of the three heroines of Fate/stay night and the Master of Archer in the Fifth Holy Grail War. She is the heir to the Tohsaka line, an accomplished magus specializing in jewel magecraft, and Shirou's classmate. Her role changes substantially between the Fate, Unlimited Blade Works and Heaven's Feel routes.",
    "matou_sakura": "Sakura Matou is one of the three heroines of Fate/stay night, Shirou's underclassman and the central heroine of Heaven's Feel. Born Sakura Tohsaka, she was adopted by the Matou family and subjected to its magecraft training. Her ordinary appearance and her Dark Sakura form must be tagged separately when identifiable.",
    "emiya_shirou": "Shirou Emiya is the protagonist of Fate/stay night, the adopted son of Kiritsugu Emiya and a survivor of the Fuyuki fire. His ideal of becoming a hero of justice, his unusual projection magecraft and his relationship with Saber, Rin and Sakura shape the three routes.",
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
    filename = f"{tag}.json"
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

    # Keep the large and noisy tail requested by the user, but isolate it from the curated cast.
    rows = connection.execute(
        "SELECT name, post_count FROM tags WHERE category = 4 AND post_count >= 5 "
        "AND (name LIKE '%(fate)%' OR name LIKE '%(fate/%' OR name LIKE '%_(fate)') ORDER BY name"
    ).fetchall()
    lines.append("[h2]Extended character, form and costume index[/h2]")
    lines.append("This intentionally broad database-derived section retains alternate classes, ascensions, costumes and music/event-specific forms. It is separated from the cast lists so these variants are not mistaken for independent principal characters.")
    current = None
    for name, count in rows:
        if name in seen:
            continue
        letter = name[0].upper() if name and name[0].isalpha() else "#"
        if letter != current:
            lines.append(f"[h3]{letter}[/h3]")
            current = letter
        lines.append(f"* {styled(name, count)}")
    lines += [
        "[h2]Related terminology and equipment[/h2]",
        "The former page interleaved weapons, Noble Phantasms, classes and memes with people. Those subjects should be maintained in dedicated indexes; useful starting tags include [[command_spell]], [[holy_grail_(fate)]], [[saber_class_(fate)]], [[archer_class_(fate)]], [[lancer_class_(fate)]], [[rider_class_(fate)]], [[caster_class_(fate)]], [[assassin_class_(fate)]] and [[berserker_class_(fate)]].",
        "[h2]External links[/h2]",
        "* TYPE-MOON official website: https://typemoon.com/",
        "* Fate/Grand Order official website: https://www.fate-go.jp/",
    ]
    return "\n".join(lines)


def character_source(tag: str, description: str) -> str:
    return "\n".join([
        "[b]Description:[/b]", description,
        "[h2]Related tags[/h2]", f"* [[{tag}]]", "* [[fate/stay_night]]", "* [[fate_(series)]]",
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
        written += int(write_json(OUT, "list_of_fate_series_characters", "general", character_list_source(connection), uploaded))
        for tag, description in CHARACTERS.items():
            written += int(write_json(OUT / "characters" / "fate_stay_night", tag, "character", character_source(tag, description), uploaded))

    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "wiki_audit.tsv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t")
        writer.writerow(["tag", "status", "last_update", "action"])
        writer.writerows(AUDIT)
    print(f"Fate drafts written: {written}; uploaded exclusions: {len(uploaded)}")


if __name__ == "__main__":
    main()
