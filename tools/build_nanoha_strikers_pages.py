from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


OUT = Path(r"D:\python\artist_by_tag\var\wiki_drafts\MSLN")
NANOHA = "https://nanoha.fandom.com/wiki/"
COPYRIGHT = "mahou_shoujo_lyrical_nanoha_strikers"
INDEX = "List_of:Mahou_Shoujo_Lyrical_Nanoha_(franchise)"


# tag: (description, continuity or tagging note, Fandom page)
CHARACTERS = {
    "subaru_nakajima": ("Subaru Nakajima is a Modern Belkan combat mage and one of Riot Force 6's four forwards. Inspired by Nanoha after being rescued during the airport fire, she specializes in close combat, Wing Road and roller-blade movement with Mach Caliber and Revolver Knuckle.", "StrikerS later reveals that Subaru and her sister Ginga are Combat Cyborgs created from Quint Nakajima's genetic material. This is the primary-continuity character; Brave Duel substantially reworks her background.", "Subaru_Nakajima"),
    "teana_lanster": ("Teana Lanster is Subaru Nakajima's partner in Forward Stars. An ambitious Midchildan shooter and illusion mage, she fights with Cross Mirage and initially seeks recognition as an Enforcer in memory of her brother Tiida.", "Her reckless training crisis and later growth under Nanoha are central to StrikerS. After Riot Force 6, she becomes an Executive Officer Assistant under Fate.", "Teana_Lanster"),
    "erio_mondial": ("Erio Mondial is a young Artificial Mage placed under Fate's guardianship and assigned to Forward Lightning. He uses the spear-shaped Armed Device Strada and combines Modern Belkan close combat with high-speed movement.", "Erio first appears in the StrikerS manga. His origin as an Artificial Mage echoes Project Fate technology but he is not a clone of Fate or her family.", "Erio_Mondial"),
    "caro_ru_lushe": ("Caro Ru Lushe is a young dragon summoner from Alzas and a member of Forward Lightning. Exiled by her clan because of her unusual power, she is taken under Fate's care and fights alongside Erio using Kerykeion, Friedrich and the guardian dragon Voltaire.", "Caro is introduced in the StrikerS manga and belongs to the primary continuity. Her Brave Duel incarnation uses a different school-age setting.", "Caro_Ru_Lushe"),
    "ginga_nakajima": ("Ginga Nakajima is Subaru's elder sister, a Ground Forces investigator and a practitioner of Shooting Arts. She uses Blitz Caliber and a Revolver Knuckle and later supervises the rehabilitation of several Numbers.", "Like Subaru, Ginga is a Combat Cyborg created from Quint Nakajima's genetic material. During StrikerS she is captured and temporarily converted into Number XIII.", "Ginga_Nakajima"),
    "shario_finieno": ("Shario Finieno, usually called Shari, is Fate's assistant, a communications operator and a skilled Device Meister. In Riot Force 6 she supports Long Arch and develops or maintains the forwards' Devices.", "Shari first appears in StrikerS THE COMICS. Reflection and Detonation show a younger movie-continuity version of her.", "Shario_Finieno"),
    "griffith_lowran": ("Griffith Lowran is Leti Lowran's son and Hayate's command assistant in Riot Force 6. He helps manage Long Arch, traffic communication, accounting and day-to-day unit operations.", "He was mentioned without a name in A's Sound Stage 03, but is introduced as a character in StrikerS THE COMICS. He later marries Lucino Liilie.", "Griffith_Lowran"),
    "vice_granscenic": ("Vice Granscenic is Riot Force 6's helicopter pilot and a former Bureau sniper. He supports the unit with the Intelligent Device Storm Raider and eventually returns to sharpshooting during the final rescue operation.", "His trauma is tied to accidentally injuring his younger sister Laguna during a hostage incident. He is introduced in StrikerS THE COMICS.", "Vice_Granscenic"),
    "alto_krauetta": ("Alto Krauetta is a Riot Force 6 maintenance officer and Long Arch operator who also holds a helicopter pilot qualification. She takes over as pilot after Vice is injured.", "Alto is introduced in StrikerS THE COMICS as a subordinate of Signum and later serves in Sound Stage X and Force.", "Alto_Krauetta"),
    "lucino_liilie": ("Lucino Liilie is a vessel helmswoman and Long Arch communications operator in Riot Force 6. She later serves aboard Hayate's ship Wolfram and marries Griffith Lowran, becoming Lucino Lowran.", "An unnamed crew member resembling Lucino appears in A's, but her named role and characterization begin in StrikerS THE COMICS.", "Lucino_Liilie"),
    "aina_triton": ("Aina Triton is the dormitory mother of Riot Force 6. She manages the members' household and helps care for Vivio when Nanoha and Fate are away on duty.", "Aina is a minor StrikerS supporting character and later becomes the Takamachi household's live-in housekeeper.", "Aina_Triton"),
    "carim_gracia": ("Carim Gracia is a Church Knight of the Saint Church, a Bureau director and one of Riot Force 6's principal sponsors. Her Ancient Belkan Rare Skill produces prophecies that help motivate the unit's creation.", "Carim first appears in StrikerS THE COMICS. Later appearances in ViVid and EXCEEDS belong to later primary or movie continuities.", "Carim_Gracia"),
    "schach_nouera": ("Schach Nouera is a Saint Church sister and knight who serves as Carim Gracia's attendant. She is an Ancient Belkan close-combat mage and supports the assault on the Saint's Cradle.", "Schach is introduced in StrikerS and later appears in ViVid. She should not be confused with Shamal despite both having support roles around Hayate's allies.", "Schach_Nouera"),
    "verossa_acous": ("Verossa Acous is a Bureau inspector, Hayate and Chrono's friend, and Carim Gracia's half-brother. His Rare Skills allow him to track targets and investigate thoughts or memories.", "Verossa first appears in StrikerS THE COMICS. He is commonly nicknamed Rossa.", "Verossa_Acous"),
    "genya_nakajima": ("Genya Nakajima is a Ground Forces officer, widower of Quint and father of Ginga and Subaru. After the JS Incident he oversees the rehabilitation of the captured Numbers, several of whom join the Nakajima family.", "Genya is introduced in StrikerS and remains an important Ground Forces commander in Sound Stage X.", "Genya_Nakajima"),
    "quint_nakajima": ("Quint Nakajima was a Ground Forces investigator, Genya's wife and the genetic mother of Ginga and Subaru. A practitioner of Shooting Arts, she died while investigating Jail Scaglietti's Combat Cyborg project.", "Quint appears through StrikerS flashbacks and records. The Numbers Nove and Wendi inherit some of her genetic and combat traits, but are distinct characters.", "Quint_Nakajima"),
    "tiida_lanster": ("Tiida Lanster was Teana's older brother and a Bureau mage who hoped to become an Enforcer. He died in the line of duty before StrikerS, and the dismissive treatment of his death drives Teana's early obsession with proving herself.", "Tiida appears only through memories, photographs and background material in StrikerS. He is not Teana's alternate form.", "Tiida_Lanster"),
    "megane_alpine": ("Megane Alpine is Lutecia's mother and a former member of Zest Grangeitz's investigation team. She is left in a coma after the team discovers Scaglietti's laboratory, and Lutecia is manipulated with the promise of restoring her.", "Megane is chiefly a background and flashback character in StrikerS and later recovers after the JS Incident.", "Megane_Alpine"),
    "karel_harlaown": ("Karel Harlaown is one of the twin children of Chrono Harlaown and Amy Limietta, and Liera's brother. He appears with the Harlaown family during the StrikerS era.", "Karel is introduced in StrikerS supplementary material and appears in the television series in a family photograph.", "Karel_Harlaown"),
    "liera_harlaown": ("Liera Harlaown is one of the twin children of Chrono Harlaown and Amy Limietta, and Karel's sister. She appears with the Harlaown family during the StrikerS era.", "Liera is introduced in StrikerS Sound Stage M and appears in the television series in a family photograph.", "Liera_Harlaown"),
    "laguna_granscenic": ("Laguna Granscenic is Vice's younger sister. She was accidentally wounded in the eye during one of his sniper missions, but later tells him that she forgave him long ago and helps him overcome his trauma.", "Laguna appears in a StrikerS flashback and in person before the final battle.", "Laguna_Granscenic"),
    "mira_barret": ("Mira Barret is a conservation officer on Supools and a colleague of Caro Ru Lushe in the Frontier Nature Conservation Corps.", "Mira is introduced in StrikerS THE COMICS and later appears briefly in Sound Stage X. The local Gelbooru database currently has no exact character tag for her.", "Mira_Barret"),
    "tanto_(lyrical_nanoha)": ("Tanto is a Frontier Nature Conservation Corps member and partner of Mira Barret who works in the same service as Caro, Erio and Friedrich after StrikerS.", "The unqualified Gelbooru tag [[tanto]] is a general tag and must not be used for this character. This proposed qualified tag is currently absent from the local database.", "Tanto"),
    "regius_gaiz": ("Regius Gaiz is the lieutenant general commanding Midchilda's Capital Defense Forces. His desire to strengthen the Ground Forces leads him into a secret arrangement with Jail Scaglietti and into conflict with Riot Force 6.", "Regius and Zest were once close friends. His actions and death belong to the primary StrikerS continuity.", "Regius_Gaiz"),
    "auris_gaiz": ("Auris Gaiz is Regius Gaiz's daughter and administrative aide. Loyal to her father, she supports his command of the Capital Defense Forces and survives the JS Incident.", "Auris is a minor StrikerS character. She is distinct from Aria Liese despite the somewhat similar names.", "Auris_Gaiz"),
    "jail_scaglietti": ("Jail Scaglietti is the principal antagonist of StrikerS, a criminal scientist obsessed with Lost Logia, Artificial Mages and Combat Cyborgs. He directs the Numbers and manipulates Lutecia and Zest while seeking control of the Saint's Cradle.", "Scaglietti is himself the product of a clandestine Bureau project. Later continuities retain the consequences of his experiments even after his capture.", "Jail_Scaglietti"),
    "lutecia_alpine": ("Lutecia Alpine is a young Modern Belkan summoner manipulated by Jail Scaglietti. Believing he can awaken her comatose mother Megane, she searches for Relics with Agito and her insect summons, especially Garyu.", "After the JS Incident Lutecia is placed under supervision and later becomes an ally in ViVid. Brave Duel gives her a separate school continuity.", "Lutecia_Alpine"),
    "agito_(nanoha)": ("Agito is a fiery Unison Device who initially accompanies Lutecia and Zest. She longs for a compatible master and eventually forms a partnership with Signum after the JS Incident.", "The qualified tag [[agito_(nanoha)]] distinguishes her from unrelated characters. She is separate from Reinforce Zwei despite both being Unison Devices.", "Agito"),
    "zest_grangeitz": ("Zest Grangeitz is a former Ground Forces investigator resurrected by Jail Scaglietti as an Artificial Mage. He seeks the truth about the mission that killed his unit and ultimately confronts his former friend Regius Gaiz.", "The local tag spells his family name [[zest_grangeitz]], while some official and fan sources romanize it Grangaitz.", "Zest_Grangeitz"),
    "garyuu_(nanoha)": ("Garyu is Lutecia Alpine's intelligent insectoid summon and closest guardian. He fights primarily at close range and often acts independently when protecting Lutecia.", "The local character tag is [[garyuu_(nanoha)]]. The unqualified [[garyu]] entry belongs to the artist category and must not be used for this character.", "Garyu"),
    "hakutenou": ("Hakutenou is a gigantic humanoid insect summon and one of Lutecia Alpine's strongest guardians. He appears when Lutecia is forced into a berserk state and serves as her counterpart to Caro's guardian dragon Voltaire.", "Hakutenou is an individual summon introduced in StrikerS, unlike the multiple Jiraiou and Insekten. The exact tag is currently absent from the local Gelbooru database.", "Hakutenou"),
    "vivio": ("Vivio is a child Artificial Mage created as a vessel for the genetic memory of the Saint King. Rescued and cared for by Nanoha, she is abducted for the activation of the Saint's Cradle before Nanoha reaches and frees her.", "After StrikerS, Nanoha adopts her as Vivio Takamachi. ViVid makes her a central protagonist, but [[vivio]] remains the established Gelbooru character tag.", "Vivio"),
    "friedrich_(nanoha)": ("Friedrich, usually called Fried, is Caro Ru Lushe's young silver dragon companion. Caro can summon his larger adult form for transport and combat support.", "The qualified tag [[friedrich_(nanoha)]] distinguishes him from unrelated uses of Friedrich. He is a summoned dragon, not a familiar or Device.", "Friedrich"),
    "voltaire_(nanoha)": ("Voltaire is the ancient Black Fire Dragon and guardian of Alzas whom Caro Ru Lushe can summon in moments of great need. His enormous humanoid dragon form provides overwhelming bombardment power.", "Use [[voltaire_(nanoha)]] rather than the less specific [[voltaire]]. He is a fully summoned guardian dragon and is distinct from Friedrich.", "Voltaire"),
}


# tag: (serial, role/equipment, later outcome, Fandom page)
NUMBERS = {
    "uno_(nanoha)": (1, "Scaglietti's chief secretary and command coordinator, using the Inherent Skill Flawless Secretary", "She remains loyal to Scaglietti and is imprisoned after the incident", "Uno"),
    "due_(nanoha)": (2, "an infiltrator and assassin who uses Liar's Mask, shapeshifting and Piercing Nail", "She kills Regius Gaiz and is then killed by Zest", "Due"),
    "tre_(nanoha)": (3, "the Numbers' frontline commander, using Ride Impulse and Impulse Blade", "She remains loyal to Scaglietti and is imprisoned", "Tre"),
    "quattro_(nanoha)": (4, "a rear commander and illusion specialist using Silver Curtain", "She manipulates Vivio aboard the Cradle and is defeated by Nanoha", "Quattro"),
    "cinque_(nanoha)": (5, "an experienced infiltration fighter using Rumble Detonator, Stinger and Shell Coat", "She enters rehabilitation and is later adopted into the Nakajima family", "Cinque"),
    "sein_(nanoha)": (6, "an infiltration specialist who can pass through solid matter with Deep Diver", "She enters rehabilitation and later serves the Saint Church", "Sein"),
    "sette_(nanoha)": (7, "an aerial combat specialist using Slaughter Arms and Boomerang Blade", "She remains loyal to Scaglietti and is imprisoned", "Sette"),
    "otto_(nanoha)": (8, "a reconnaissance and area-attack specialist using Ray Storm and Stealth Jacket", "She enters rehabilitation and later serves the Saint Church", "Otto"),
    "nove_(nanoha)": (9, "a close-combat specialist using Break Liner, Gun Knuckle and Jet Edge", "She joins the Nakajima family and later coaches Vivio in Strike Arts", "Nove"),
    "dieci_(nanoha)": (10, "a long-range bombardment specialist using Heavy Barrel and Enormous Cannon", "She joins the rehabilitated Numbers and the Nakajima family", "Dieci"),
    "wendi_(nanoha)": (11, "a mobile aerial fighter using Aerial Rave and Riding Board", "She joins the rehabilitated Numbers and the Nakajima family", "Wendi"),
    "deed_(nanoha)": (12, "an aerial close-combat specialist using Twin Blades", "She remains loyal to Scaglietti and is imprisoned", "Deed"),
}


STRIKERS_GROUPS = [
    ("Forward team", ["subaru_nakajima", "teana_lanster", "erio_mondial", "caro_ru_lushe"]),
    ("Riot Force 6 and allies", ["ginga_nakajima", "shario_finieno", "griffith_lowran", "vice_granscenic", "alto_krauetta", "lucino_liilie", "aina_triton", "carim_gracia", "schach_nouera", "verossa_acous"]),
    ("Families and background", ["genya_nakajima", "quint_nakajima", "tiida_lanster", "megane_alpine", "karel_harlaown", "liera_harlaown", "laguna_granscenic", "mira_barret", "tanto_(lyrical_nanoha)"]),
    ("Bureau leadership", ["regius_gaiz", "auris_gaiz"]),
    ("JS Incident antagonists and rescued characters", ["jail_scaglietti", "lutecia_alpine", "agito_(nanoha)", "zest_grangeitz", "garyuu_(nanoha)", "hakutenou", "vivio", "friedrich_(nanoha)", "voltaire_(nanoha)"]),
    ("Numbers", list(NUMBERS)),
]


def safe(tag: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.()+-]+", "_", tag).strip("._") or "untitled"


def uploaded_names() -> set[str]:
    uploaded = OUT / "uploaded"
    return {path.name.casefold() for path in uploaded.rglob("*") if path.is_file()} if uploaded.is_dir() else set()


def save(tag: str, template: str, source: str) -> None:
    data = {"tag": tag, "template": template, "source": source, "updated_at": datetime.now(timezone.utc).isoformat()}
    (OUT / f"{safe(tag)}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def character_source(description: str, note: str, fandom_page: str) -> str:
    return (
        f"[b]Description:[/b]\n{description}\n\n[b]Continuity note:[/b]\n{note}\n\n"
        f"[b]Copyright:[/b]\n[[{COPYRIGHT}]]\n\n[b]See also:[/b]\n[[{COPYRIGHT}]]\n[[{INDEX}]]\n\n"
        f"[b]External sources:[/b]\n{NANOHA}{fandom_page}"
    )


def series_source() -> str:
    returning = ["takamachi_nanoha", "fate_testarossa", "yagami_hayate", "reinforce_zwei", "signum", "vita_(nanoha)", "shamal", "zafira"]
    lines = [
        "魔法少女リリカルなのはStrikerS",
        "",
        "The third television series in the main Magical Girl Lyrical Nanoha continuity. Created and written by Tsuzuki Masaki, directed by Kusakawa Keizo and animated by Seven Arcs, its 26 episodes aired in Japan from April 1 to September 23, 2007. A two-volume companion manga, StrikerS THE COMICS, supplies events before and alongside the television story.",
        "",
        "Set ten years after Magical Girl Lyrical Nanoha A's, the story follows the adult Nanoha, Fate and Hayate in the Time-Space Administration Bureau. Hayate establishes Lost Property Riot Force 6 to investigate Relics and the threat foreseen by Carim Gracia. Nanoha and Fate train four young forwards while the unit confronts Jail Scaglietti, his Numbers and the activation of the Saint's Cradle.",
        "",
        "The series shifts the franchise toward an ensemble military and rescue structure, with team training, Bureau politics, Artificial Mages and Combat Cyborgs sharing the focus with magical combat.",
        "",
        "[b]Returning command and Yagami family:[/b]",
        *[f"[[{tag}]]" for tag in returning],
    ]
    for heading, tags in STRIKERS_GROUPS:
        lines.extend(["", f"[b]{heading}:[/b]", *[f"[[{tag}]]" for tag in tags]])
    lines.extend([
        "", "[b]Follows:[/b]", "[[mahou_shoujo_lyrical_nanoha_a's]]",
        "", "[b]Side-story sequel:[/b]", "[[mahou_shoujo_lyrical_nanoha_strikers_sound_stage_x]]",
        "", "[b]Later primary-continuity works:[/b]", "[[mahou_shoujo_lyrical_nanoha_vivid]]", "[[mahou_senki_lyrical_nanoha_force]]",
        "", "[b]See also:[/b]", f"[[{INDEX}]]", "[[numbers_(nanoha)]]",
        "", "[b]External sources:[/b]", NANOHA + "Magical_Girl_Lyrical_Nanoha_StrikerS", "https://en.wikipedia.org/wiki/Magical_Girl_Lyrical_Nanoha_StrikerS",
    ])
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    uploaded = uploaded_names()
    created = skipped = 0
    pages = dict(CHARACTERS)
    for tag, (serial, role, outcome, fandom_page) in NUMBERS.items():
        description = f"{tag.split('_(nanoha)', 1)[0].capitalize()} is Number {serial}, {role}. She is one of the twelve Combat Cyborgs created by Jail Scaglietti in StrikerS."
        note = f"{outcome}. The qualified Gelbooru tag distinguishes her from unrelated characters with the same short name."
        pages[tag] = (description, note, fandom_page)
    for tag, (description, note, fandom_page) in pages.items():
        if f"{safe(tag)}.json".casefold() in uploaded:
            skipped += 1
            continue
        save(tag, "character", character_source(description, note, fandom_page))
        created += 1
    series_name = f"{safe(COPYRIGHT)}.json".casefold()
    if series_name not in uploaded:
        save(COPYRIGHT, "copyright", series_source())
        series_status = "created"
    else:
        series_status = "uploaded"
    print(f"Created {created} StrikerS character drafts, skipped {skipped} uploaded drafts; series page: {series_status}")


if __name__ == "__main__":
    main()
