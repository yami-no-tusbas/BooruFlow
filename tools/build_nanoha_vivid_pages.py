from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


OUT = Path(r"D:\python\artist_by_tag\var\wiki_drafts\MSLN")
NANOHA = "https://nanoha.fandom.com/wiki/"
COPYRIGHT = "mahou_shoujo_lyrical_nanoha_vivid"
INDEX = "List_of:Mahou_Shoujo_Lyrical_Nanoha_(franchise)"


# tag: (description, tagging/continuity note, Fandom page)
CHARACTERS = {
    "einhard_stratos": ("Einhard Stratos is a young martial artist and a descendant of the Hegemon Claus Ingvalt. She inherits fragments of his memories and Kaiser Arts, initially seeking strong opponents before becoming Vivio's friend and rival. Her Device is Asteion.", "Use [[einhard_stratos]]. The similar local entry einhart_stratos is not the established character tag.", "Einhard_Stratos"),
    "corona_timir": ("Corona Timil is one of Vivio's classmates and closest friends at St. Hilde Academy. She combines Strike Arts with Creation magic and Meist Arts, notably constructing and controlling the golem Goliath through Brunzel.", "The established Gelbooru character tag is [[corona_timir]], although some sources romanize her family name as Timil.", "Corona_Timil"),
    "rio_wezley": ("Rio Wesley is Vivio's classmate and friend, a practitioner of Strike Arts and the Spring Sunlight Fist school. She can use both fire and lightning conversion magic and fights with the Device Solfege.", "The established Gelbooru tag is [[rio_wezley]], despite the Wesley spelling used by the reference wiki.", "Rio_Wesley"),
    "miura_rinaldi": ("Miura Rinaldi is a young martial artist trained at the Yagami dojo. She specializes in powerful kicks and Yagami-style Strike Arts and competes in the Intermiddle Championship with the Device Star Saber.", "Miura is introduced in ViVid and later returns as a major competitor in ViVid Strike!.", "Miura_Rinaldi"),
    "sieglinde_eremiah": ("Sieglinde Eremiah is an elite young martial artist, Intermiddle champion and descendant of Wilfred Eremiah. Nicknamed Sieg, she practices the Eremian combat style and becomes an important rival and friend to Vivio's generation.", "Use [[sieglinde_eremiah]]. Other romanizations of the family name are not separate characters.", "Sieglinde_Eremiah"),
    "viktoria_dahlgrun": ("Viktoria Dahlgrun is a powerful Intermiddle competitor descended from the ancient Thunder Emperor Dahlgrun. She uses lightning magic, her ancestral combat style and the Device Blaue Trombe.", "Use the locally established spelling [[viktoria_dahlgrun]]; Victoria is a romanization variant.", "Viktoria_Dahlgrun"),
    "harry_tribeca": ("Harry Tribeca is a teenage magical combat athlete known as Buster Head and the leader of the Red Hawk team. Her rough appearance and forceful style hide a dependable and protective personality.", "Harry is introduced in the Intermiddle Championship storyline of ViVid.", "Harry_Tribeca"),
    "els_tasmin": ("Els Tasmin is an Intermiddle competitor nicknamed the Punisher. She specializes in force-field techniques and is both a rival and friend of Harry Tribeca.", "This is the ViVid character, not an abbreviation or an unrelated Els.", "Els_Tasmin"),
    "mikaya_chevelle": ("Mikaya Chevelle is a skilled swordfighter and magical combat athlete who practices Mikaya-style swordsmanship. She is acquainted with the Nakajima family and later helps train and support younger competitors.", "Mikaya is introduced through ViVid's martial-arts tournament cast.", "Mikaya_Chevelle"),
    "chantez_apinion": ("Chantez Apinion is a Saint Church sister and knight trained by Schach Nouera. She uses a twin-sword fighting style and competes in the Intermiddle Championship.", "Chantez is introduced in ViVid and is associated with the Saint Church, not Riot Force 6.", "Chantez_Apinion"),
    "fabia_crozelg": ("Fabia Crozelg is a young Ancient Belkan witch and magical combat athlete known as Hell Gazer. She is a descendant of the witch Crozelg and uses inherited witchcraft in tournament combat.", "Fabia is introduced in the ViVid manga; her ancestor Crozelg is a separate character.", "Fabia_Crozelg"),
    "yuna_platz": ("Yuna Platz is a minor Intermiddle Championship competitor who faces Fabia Crozelg. She fights with kunai-shaped Devices.", "No exact character tag was found in the local database; [[yuna_platz]] is the proposed canonical tag.", "Yuna_Platz"),
    "elsa_edix": ("Elsa Edix is a minor Intermiddle Championship competitor and one of Lutecia Alpine's opponents. She uses a morning-star-shaped Device.", "No exact character tag was found in the local database; [[elsa_edix]] is the proposed canonical tag.", "Elsa_Edix"),
    "elly_stout": ("Elly Stout is an elite shooter and Intermiddle competitor nicknamed the Demonic Bullet. She appears as one of Miura Rinaldi's later tournament opponents.", "No exact character tag was found in the local database; [[elly_stout]] is the proposed canonical tag.", "Elly_Stout"),
    "yumina_enclave": ("Yumina Enclave is Einhard's classmate at St. Hilde Academy. She later becomes the chief manager of Nove's gym and supports its fighters with organizational and therapeutic skills.", "Yumina is introduced in the later ViVid manga and does not appear in the twelve-episode anime adaptation.", "Yumina_Enclave"),
    "linda_(nanoha)": ("Linda is one of Harry Tribeca's school friends and followers, alongside Luca and Mia. She is distinguished visually by the mask covering the lower part of her face.", "Use [[linda_(nanoha)]] because the unqualified Linda entry belongs to another tag category.", "Linda"),
    "luca_(nanoha)": ("Luca is one of Harry Tribeca's school friends and followers, alongside Linda and Mia. She supports Harry during the Intermiddle Championship storyline.", "Use the qualified Gelbooru character tag [[luca_(nanoha)]] to distinguish her from unrelated characters with the same name.", "Luca"),
    "mia_(nanoha)": ("Mia is one of Harry Tribeca's school friends and followers, alongside Linda and Luca. She is the tallest of the trio, has long hair and is academically capable.", "Use the qualified local character tag [[mia_(nanoha)]].", "Mia"),
    "claus_ingvalt": ("Claus Ingvalt was the Hegemon of Shutra during the Ancient Belkan era and is Einhard Stratos's ancestor. His memories, grief and bond with Olivie Sagebrecht strongly shape Einhard's inherited identity.", "Claus is a historical ViVid character seen through inherited memories and flashbacks.", "Claus_Ingvalt"),
    "olivie_segbrecht": ("Olivie Sagebrecht was the last Saint King of Ancient Belka and a close companion of Claus Ingvalt. Her life and death are central to the inherited memories explored by Vivio and Einhard.", "The established local tag is [[olivie_segbrecht]], while sources also spell the surname Sagebrecht or Sägebrecht.", "Olivie_S%C3%A4gebrecht"),
    "wilfried_jeremiah": ("Wilfred Eremiah was an Ancient Belkan scholar and political visitor connected to the Sagebrecht family. Known as the Black Eremiah, he is an ancestor of Sieglinde Eremiah.", "The local character tag is [[wilfried_jeremiah]], despite the Wilfred Eremiah spelling used by the reference wiki.", "Wilfred_Eremiah"),
    "crozelg_(nanoha)": ("Crozelg was an ancient witch of Shutra's Witch Forest, remembered as the Original Witch or Witch Cat. She is the ancestor whose magic is inherited by Fabia Crozelg.", "No exact character tag exists locally. Use the proposed qualified tag [[crozelg_(nanoha)]] to avoid confusing the character with a surname or franchise-neutral term.", "Crozelg"),
    "dahlgrun_(nanoha)": ("Dahlgrun was an ancient ruler known as the Thunder Emperor and is the ancestor of Viktoria Dahlgrun. The inherited title and combat tradition remain important to Viktoria's identity.", "No exact character tag exists locally. [[dahlgrun_(nanoha)]] is proposed to distinguish the historical character from the family name.", "Dahlgrun"),
    "irene_hardin": ("Irene Hardin is the master of the Flower Phoenix Fist martial-arts school in Leuven. She trains fighters connected with the later ViVid manga and is served by Claire Lagreat.", "No exact character tag was found in the local database; [[irene_hardin]] is the proposed canonical tag.", "Irene_Hardin"),
    "claire_lagreat": ("Claire Lagreat is Irene Hardin's butleress at the Flower Phoenix Fist school in Leuven and the sister of Edgar Lagreat. She is also a capable rapier user.", "No exact character tag was found in the local database; [[claire_lagreat]] is the proposed canonical tag.", "Claire_Lagreat"),
    "edgar_lagreat": ("Edgar Lagreat is Viktoria Dahlgrun's butler and second, and the brother of Claire Lagreat. He supports Viktoria during her tournament appearances.", "No exact character tag was found in the local database; [[edgar_lagreat]] is the proposed canonical tag.", "Edgar_Lagreat"),
    "ray_tundra": ("Ray Tundra is Rio Wesley's grandfather and the head of the Spring Sunlight Fist dojo in Leuven.", "No exact character tag was found in the local database; [[ray_tundra]] is the proposed canonical tag.", "Ray_Tundra"),
    "rinna_tundra": ("Rinna Tundra is Rio Wesley's older cousin and an assistant coach at the Spring Sunlight Fist dojo in Leuven. She trains Xue Rosen and Yen Lankwai.", "No exact character tag was found in the local database; [[rinna_tundra]] is the proposed canonical tag.", "Rinna_Tundra"),
    "tao_raikaku": ("Tao Raikaku is a servant and martial-arts apprentice at the Spring Sunlight Fist dojo. Her fighting techniques include manipulating her hair.", "No exact character tag was found in the local database; [[tao_raikaku]] is the proposed canonical tag.", "Tao_Raikaku"),
    "xue_rosen_(nanoha)": ("Xue Rosen is one of Rinna Tundra's apprentices at the Spring Sunlight Fist dojo in Leuven.", "The existing [[xue_rosen]] entry is category 0, not a character tag. [[xue_rosen_(nanoha)]] is proposed until a canonical character entry is established.", "Xue_Rosen"),
    "yen_lankwai_(nanoha)": ("Yen Lankwai is one of Rinna Tundra's apprentices at the Spring Sunlight Fist dojo in Leuven.", "The existing [[yen_lankwai]] entry is category 0, not a character tag. [[yen_lankwai_(nanoha)]] is proposed until a canonical character entry is established.", "Yen_Lankwai"),
    "edelgard_barkas": ("Edelgard Barkas is a teenage martial artist from Almanac and an active under-15 world champion. She appears in the later portion of the ViVid manga as an exceptionally strong international competitor.", "No exact character tag was found in the local database; [[edelgard_barkas]] is the proposed canonical tag.", "Edelgard_Barkas"),
    "noah_earls": ("Noah Earls is a communications officer in the Bureau's Tactical Instructor Corps who provides commentary and support during later magical combat events.", "No exact character tag was found in the local database; [[noah_earls]] is the proposed canonical tag.", "Noah_Earls"),
    "goliath_(nanoha)": ("Goliath is Corona Timil's large golem, constructed and controlled through her Creation magic and Brunzel. It serves as her main heavy combat summon.", "The unqualified [[goliath]] entry is a general tag. [[goliath_(nanoha)]] is proposed for this individual character.", "Goliath"),
}


VIVID_GROUPS = [
    ("Main generation", ["einhard_stratos", "corona_timir", "rio_wezley", "miura_rinaldi"]),
    ("Intermiddle competitors and support", ["sieglinde_eremiah", "viktoria_dahlgrun", "harry_tribeca", "els_tasmin", "mikaya_chevelle", "chantez_apinion", "fabia_crozelg", "yuna_platz", "elsa_edix", "elly_stout", "yumina_enclave", "linda_(nanoha)", "luca_(nanoha)", "mia_(nanoha)"]),
    ("Ancient Belkan history", ["claus_ingvalt", "olivie_segbrecht", "wilfried_jeremiah", "crozelg_(nanoha)", "dahlgrun_(nanoha)"]),
    ("Leuven martial-arts schools", ["irene_hardin", "claire_lagreat", "edgar_lagreat", "ray_tundra", "rinna_tundra", "tao_raikaku", "xue_rosen_(nanoha)", "yen_lankwai_(nanoha)"]),
    ("Later manga and other characters", ["edelgard_barkas", "noah_earls", "goliath_(nanoha)"]),
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
        f"[b]External sources:[/b]\n{NANOHA}{fandom_page}"
    )


def series_source() -> str:
    returning = ["vivio", "takamachi_nanoha", "fate_testarossa", "nove_(nanoha)", "yagami_hayate", "reinforce_zwei", "lutecia_alpine", "carim_gracia", "schach_nouera"]
    lines = [
        "魔法少女リリカルなのはViVid",
        "",
        "Magical Girl Lyrical Nanoha ViVid is a manga written by Tsuzuki Masaki and illustrated by Fujima Takuya. Serialization began in 2009. Set in Midchilda in year 0079, four years after StrikerS, it follows Vivio Takamachi as she studies Strike Arts, befriends Einhard Stratos and enters the world of organized magical combat.",
        "",
        "The story expands the franchise's Ancient Belkan history through Vivio and Einhard's inherited memories while developing a large generation of young martial artists. The 2015 television anime adapts only the early part of the manga; later tournament competitors and the Leuven story remain manga-only.",
        "",
        "[b]Returning principal characters:[/b]",
        *[f"[[{tag}]]" for tag in returning],
    ]
    for heading, tags in VIVID_GROUPS:
        lines.extend(["", f"[b]{heading}:[/b]", *[f"[[{tag}]]" for tag in tags]])
    lines.extend([
        "", "[b]Follows:[/b]", "[[mahou_shoujo_lyrical_nanoha_strikers]]", "[[mahou_shoujo_lyrical_nanoha_strikers_sound_stage_x]]",
        "", "[b]Related concurrent work:[/b]", "[[mahou_senki_lyrical_nanoha_force]]",
        "", "[b]Followed by:[/b]", "[[vivid_strike!]]",
        "", "[b]See also:[/b]", f"[[{INDEX}]]",
        "", "[b]External sources:[/b]", NANOHA + "Magical_Girl_Lyrical_Nanoha_ViVid", NANOHA + "Magical_Girl_Lyrical_Nanoha_ViVid_(anime)", "https://en.wikipedia.org/wiki/Magical_Girl_Lyrical_Nanoha_ViVid",
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
    print(f"Created {created} ViVid character drafts, skipped {skipped} uploaded drafts; series page: {series_status}")


if __name__ == "__main__":
    main()
