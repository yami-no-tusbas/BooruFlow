from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


OUT = Path(r"D:\python\artist_by_tag\var\wiki_drafts\MSLN")
NANOHA = "https://nanoha.fandom.com/wiki/"
OFFICIAL = "https://vivid-strike.com/"
COPYRIGHT = "vivid_strike!"
INDEX = "List_of:Mahou_Shoujo_Lyrical_Nanoha_(franchise)"


# tag: (description, tagging/continuity note, Fandom page)
CHARACTERS = {
    "fuka_reventon": (
        "Fuka Reventon is the main protagonist of ViVid Strike!. An orphan and former childhood friend of Rinne Berlinetta, she survives through temporary jobs until Einhard Stratos recognizes her talent and brings her to Nakajima Gym. Fuka becomes Einhard's Kaiser Arts apprentice, competes with the Device Huracan and trains to face Rinne honestly again.",
        "Use [[fuka_reventon]], the established Gelbooru character tag. [[fuuka_reventon]] is a less-used spelling of the same character and should not be treated as a separate person.",
        "Fuka_Reventon",
    ),
    "rinne_berlinetta": (
        "Rinne Berlinetta is the secondary protagonist of ViVid Strike! and Fuka Reventon's estranged childhood friend. Adopted by the Berlinetta family, she becomes the world-ranked number-one U15 Striker at Frontier Gym under Jill Stola. Her strength and harsh public persona conceal trauma surrounding prolonged bullying and the death of her grandfather Roy.",
        "Rinne is introduced in ViVid Strike! and uses the Device Scuderia. Her later reconciliation with Fuka does not make either fighter an alternate version of the other.",
        "Rinne_Berlinetta",
    ),
    "jill_stola": (
        "Jill Stola is a former Striker Championship athlete, a trainer at Frontier Gym and Rinne Berlinetta's exclusive coach. She rescued Rinne after an abduction and developed her into an elite Total Fighting competitor through technical and theoretical training.",
        "Jill is introduced in ViVid Strike!. The local character tag [[jill_stola]] has a small post count but is valid.",
        "Jill_Stola",
    ),
    "dan_berlinetta": (
        "Dan Berlinetta is Rinne Berlinetta's adoptive father, Lorrie Berlinetta's husband and a member of the family that owns Berlinetta Brand.",
        "Dan is a minor character introduced in ViVid Strike!. No exact character tag was found locally; [[dan_berlinetta]] is the proposed canonical tag.",
        "Dan_Berlinetta",
    ),
    "lorrie_berlinetta": (
        "Lorrie Berlinetta is Rinne Berlinetta's adoptive mother, Dan Berlinetta's wife and a member of the family that owns Berlinetta Brand.",
        "Lorrie is a minor character introduced in ViVid Strike!. No exact character tag was found locally; [[lorrie_berlinetta]] is proposed, although her first name has no confirmed official Latin spelling.",
        "Lorrie_Berlinetta",
    ),
    "roy_berlinetta": (
        "Roy Berlinetta was Rinne Berlinetta's adoptive grandfather and an important source of affection and stability after her adoption. His death in 0077, while Rinne was being prevented from reaching him by school bullies, became a central part of her trauma.",
        "Roy appears in photographs and flashbacks and had already died before the main story. No exact character tag was found locally; [[roy_berlinetta]] is proposed.",
        "Roy_Berlinetta",
    ),
    "lyra_caprice": (
        "Lyra Caprice is a young Striker Championship athlete previously defeated by Rinne Berlinetta. Rinne's dismissive post-match remarks about Lyra contribute to the argument that separates Rinne and Fuka before the series begins.",
        "Lyra is a supporting character introduced in ViVid Strike!. No exact character tag was found locally; [[lyra_caprice]] is the proposed canonical tag.",
        "Lyra_Caprice",
    ),
    "carrie_tercel": (
        "Carrie Tercel is a DSAA U15 Striker ranked eighth in the world. Rinne Berlinetta knocks her out during a championship match, but Carrie later sends Rinne a friendly message asking for a rematch and an opportunity to fight Fuka.",
        "The local database contains [[carrie_tercel]] as a valid character tag.",
        "Carrie_Tercel",
    ),
    "adeel_telstar": (
        "Adeel Telstar is a minor DSAA U15 Striker who faces Vivio Takamachi in the opening round of the 0080 Winter Cup.",
        "No exact character tag was found in the local database; [[adeel_telstar]] is the proposed canonical tag.",
        "Adeel_Telstar",
    ),
    "karna_maven_(nanoha)": (
        "Karna Maven is a minor DSAA U15 Striker who faces Fuka Reventon in the opening round of the 0080 Winter Cup and is quickly knocked out.",
        "The exact local entry [[karna_maven]] is category 0 rather than a character tag. [[karna_maven_(nanoha)]] is proposed until a canonical character entry is established.",
        "Karna_Maven",
    ),
    "janice_goat": (
        "Janice Goat is a DSAA U15 Striker who faces reigning champion Einhard Stratos in the opening round of the 0080 Winter Cup. She uses a bracelet-shaped Device and enters Adult Mode for competition.",
        "No exact character tag was found in the local database; [[janice_goat]] is the proposed canonical tag.",
        "Janice_Goat",
    ),
}


VIVID_STRIKE_GROUPS = [
    ("Protagonists and coaches", ["fuka_reventon", "rinne_berlinetta", "jill_stola"]),
    ("Berlinetta family", ["dan_berlinetta", "lorrie_berlinetta", "roy_berlinetta"]),
    ("Other Striker athletes", ["lyra_caprice", "carrie_tercel", "adeel_telstar", "karna_maven_(nanoha)", "janice_goat"]),
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
        f"[b]Description:[/b]\n{description}\n\n[b]Tagging and continuity note:[/b]\n{note}\n\n"
        f"[b]Copyright:[/b]\n[[{COPYRIGHT}]]\n\n[b]See also:[/b]\n[[{COPYRIGHT}]]\n[[{INDEX}]]\n\n"
        f"[b]External sources:[/b]\n{NANOHA}{fandom_page}\n{OFFICIAL}character/"
    )


def series_source() -> str:
    returning = [
        "vivio", "einhard_stratos", "nove_(nanoha)", "rio_wezley", "corona_timir", "miura_rinaldi",
        "yumina_enclave", "mikaya_chevelle", "viktoria_dahlgrun", "harry_tribeca", "els_tasmin",
        "chantez_apinion", "fabia_crozelg", "sieglinde_eremiah", "ixveria", "sein_(nanoha)", "lutecia_alpine",
    ]
    lines = [
        "ViVid Strike!（ヴィヴィッド ストライク）",
        "",
        "ViVid Strike! is a twelve-episode anime series created and written by Tsuzuki Masaki, directed by Nishimura Junji and produced by Seven Arcs Pictures. It aired in Japan from October 2 to December 18, 2016, with additional bonus episodes released on home video.",
        "",
        "Set in Midchilda in year 0080, one year after the principal events of Magical Girl Lyrical Nanoha ViVid, the story follows Fuka Reventon. After joining Nakajima Gym and training under Einhard Stratos, Fuka enters magical martial-arts competition while trying to reconnect with her estranged childhood friend Rinne Berlinetta, the leading U15 athlete of Frontier Gym.",
        "",
        "The series belongs to the primary Nanoha continuity but, like Sound Stage X, omits 'Magical Girl Lyrical Nanoha' from its displayed title and focuses on a later generation.",
    ]
    for heading, tags in VIVID_STRIKE_GROUPS:
        lines.extend(["", f"[b]{heading}:[/b]", *[f"[[{tag}]]" for tag in tags]])
    lines.extend([
        "", "[b]Returning ViVid characters:[/b]", *[f"[[{tag}]]" for tag in returning],
        "", "[b]Devices:[/b]", "[[huracan]]", "[[scuderia]]", "[[asteion]]", "[[solfege_(nanoha)]]",
        "", "[b]Follows:[/b]", "[[mahou_shoujo_lyrical_nanoha_vivid]]", "[[mahou_shoujo_lyrical_nanoha_strikers_sound_stage_x]]",
        "", "[b]See also:[/b]", f"[[{INDEX}]]", "[[nakajima_gym]]", "[[frontier_gym]]",
        "", "[b]External sources:[/b]", NANOHA + "ViVid_Strike%21", OFFICIAL, OFFICIAL + "character/",
    ])
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    uploaded = uploaded_names()
    created = skipped = 0
    for tag, (description, note, fandom_page) in CHARACTERS.items():
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
    print(f"Created {created} ViVid Strike! character drafts, skipped {skipped} uploaded drafts; series page: {series_status}")


if __name__ == "__main__":
    main()
