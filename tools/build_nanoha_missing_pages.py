from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(r"D:\python\artist_by_tag\var\wiki_drafts\MSLN")
NANOHA = "https://nanoha.fandom.com/wiki/"
TRIANGLE = "https://en.wikipedia.org/wiki/Triangle_Heart"

PAGES = {
"yuuno_scrya": ("Yuuno Scrya is a Midchildan mage, archaeologist and member of the Scrya clan. After the Jewel Seeds are scattered over Earth, he searches for them and entrusts Raising Heart to Nanoha Takamachi, becoming her first teacher and partner. He later works at the Infinity Library and eventually becomes its chief librarian.", "Yuuno is native to the Nanoha continuity. His ferret transformation and narrative role were partly developed from Kuon, a transforming fox-girl in the Triangle Heart 3 Lyrical Toy Box prototype.", [NANOHA+"Yuuno_Scrya", NANOHA+"Lyrical_Toy_Box"]),
"arf": ("Arf, also romanized as Alph, is Fate Testarossa's wolf familiar. She is fiercely loyal to Fate, openly opposes Precia's abuse of her, and frequently fights alongside Fate or protects her. She can assume human, wolf and smaller puppy-like forms.", "Arf belongs to the Nanoha continuity and has no direct Triangle Heart counterpart.", [NANOHA+"Alph"]),
"linith": ("Linith, more commonly romanized as Rynith, is Precia Testarossa's mountain-cat familiar and Fate's magic tutor. She helps create Bardiche, trains Fate and Arf, and acts as a protective maternal figure toward Fate. The spelling Linith is used by the official English subtitles of The MOVIE 1st.", "Linith belongs to the Nanoha continuity and appears chiefly in Sound Stage 02, the novel and later memories or alternate continuities.", [NANOHA+"Rynith"]),
"chrono_harlaown": ("Chrono Harlaown is an Enforcer and officer of the Time-Space Administration Bureau, serving under his mother Lindy aboard the Arthra. Disciplined and pragmatic, he assists Nanoha and Fate during the Jewel Seed incident, investigates the Book of Darkness, and later becomes a ship captain and admiral.", "The primary-continuity character evolved from Chrono Harvey, Nanoha's rival in Triangle Heart 3 Lyrical Toy Box. The prototype and the Nanoha character have different histories and should not be treated as fully identical.", [NANOHA+"Chrono_Harlaown", NANOHA+"Lyrical_Toy_Box"]),
"lindy_harlaown": ("Lindy Harlaown is a senior officer of the Time-Space Administration Bureau and captain of the Arthra during the early series. She commands the Jewel Seed and Book of Darkness investigations, later adopts Fate Testarossa, transfers to the Bureau's Main Office and supports Riot Force 6.", "Lindy's earliest prototype is the winged fairy Lindy from Triangle Heart 3 Lyrical Toy Box. The Nanoha version is a human mage and Bureau officer.", [NANOHA+"Lindy_Harlaown", NANOHA+"Lyrical_Toy_Box"]),
"amy_limietta": ("Amy Limietta is an Arthra communications operator, researcher and executive-officer assistant who works closely with Chrono Harlaown. She becomes an older-sister figure to Fate and later marries Chrono, taking the name Amy Harlaown.", "Amy is a Nanoha-continuity character. Her later family status is established in the material between A's and StrikerS.", [NANOHA+"Amy_Limietta"]),
"takamachi_kyouya": ("Kyouya Takamachi is Nanoha's older brother and a practitioner of the Mikami Shinto sword style. Within Nanoha he is primarily a protective family member and is romantically linked with Shinobu Tsukimura.", "In Triangle Heart 3, Kyouya is the protagonist, a skilled bodyguard and the biological son of Shiro Fuwa and Kaori. These expanded biographical and combat details belong to the Triangle Heart continuity unless Nanoha material confirms them separately.", [NANOHA+"Kyoya_Takamachi", NANOHA+"Takamachi", TRIANGLE]),
"takamachi_miyuki": ("Miyuki Takamachi is Nanoha's older sister. She appears mainly in civilian and family scenes in Nanoha and later cameos, and helps coach Nanoha in the Brave Duel continuity.", "Triangle Heart 3 gives Miyuki a much larger role: she is Kyouya's cousin and adoptive sister, a Mikami-style swordswoman and bodyguard. Her parentage, missions and conflicts from that game must not automatically be assumed to apply unchanged to Nanoha.", [NANOHA+"Miyuki_Takamachi", NANOHA+"Takamachi", TRIANGLE]),
"takamachi_momoko": ("Momoko Takamachi is Nanoha's mother, Shiro's wife and the manager and pâtissière of the Midori-ya café. Nanoha trusts her judgment, and Momoko later becomes grandmother to Vivio through Nanoha's adoption of her.", "Momoko originated in Triangle Heart 3. Her relationship to Kyouya and Miyuki is more fully detailed there, while Nanoha retains her as the living center of the Takamachi household.", [NANOHA+"Momoko_Takamachi", NANOHA+"Takamachi", TRIANGLE]),
"takamachi_shirou": ("Shiro Takamachi is Nanoha's father, Momoko's husband, co-owner of Midori-ya and a former bodyguard. In Nanoha he survived severe injuries from his former work and is alive throughout the principal continuity.", "Triangle Heart 3 identifies him as Shiro Fuwa, a Mikami-style swordsman and bodyguard who adopted the Takamachi surname. He died before that game's main story; his survival is one of the major continuity differences in Nanoha.", [NANOHA+"Shir%C5%8D_Takamachi", NANOHA+"Takamachi", TRIANGLE]),
"alisa_bannings": ("Alisa Bannings is one of Nanoha's closest school friends, alongside Suzuka Tsukimura. Outspoken, wealthy and fond of dogs, she cares strongly for her friends and later learns about Nanoha's magical life.", "Alisa is based on Alisa Lowell from Triangle Heart 3, but the two are distinct characters with different histories. Information about Alisa Lowell should therefore be labelled as prototype material rather than Nanoha canon.", [NANOHA+"Arisa_Bunnings", TRIANGLE]),
"tsukimura_suzuka": ("Suzuka Tsukimura is Nanoha and Alisa's gentle school friend and Shinobu's younger sister. During A's she befriends Hayate Yagami without knowing Hayate's connection to the Book of Darkness incident.", "Suzuka and her household derive from Triangle Heart material. In Lyrical Toy Box she retains supernatural traits associated with that continuity, while Nanoha generally presents her as human.", [NANOHA+"Suzuka_Tsukimura", NANOHA+"Lyrical_Toy_Box", TRIANGLE]),
"noel_(lyrical_nanoha)": ("Noel K. Ehrlichkeit is the head maid of the Tsukimura household and the elder sister of Farin K. Ehrlichkeit. She cares for the household and appears as part of Suzuka and Shinobu's civilian circle.", "In Triangle Heart, Noel is a mechanical gynoid. Nanoha presents her as an apparently human maid; the mechanical origin should not be applied to Nanoha images without evidence from that continuity.", [NANOHA+"Noel_K._Ehrlichkeit", TRIANGLE]),
"farin_(lyrical_nanoha)": ("Farin K. Ehrlichkeit is Noel's younger sister and Suzuka Tsukimura's personal maid. She is a supporting member of the Tsukimura household in the original Nanoha series.", "Farin originated in the Triangle Heart setting. The Nanoha version is a minor civilian character, so detailed Triangle Heart material should be identified as belonging to that continuity.", [NANOHA+"Farin_K._Ehrlichkeit", TRIANGLE]),
"tsukimura_shinobu": ("Shinobu Tsukimura is Suzuka's older sister and a minor civilian character in Nanoha. She cares for the Takamachi family and is romantically interested in Kyouya Takamachi.", "In Triangle Heart 3, Shinobu is a vampire and a major possible love interest for Kyouya. Nanoha does not establish all of those supernatural and route-specific details as part of its principal continuity.", [NANOHA+"Shinobu_Tsukimura", TRIANGLE]),
}

AS_PAGES = {
"yagami_hayate": ("Hayate Yagami is an orphan from Earth and the final master of the Book of Darkness, introduced as a central character in Magical Girl Lyrical Nanoha A's. Although initially unable to walk and unaware of the tome's true nature, she treats its Guardian Knights—Signum, Vita, Shamal and Zafira—as her family. During the Book of Darkness incident she reclaims control of the corrupted tome, names its will Reinforce and helps destroy its runaway Defense Program. Hayate later recovers her mobility, joins the Time-Space Administration Bureau and creates Reinforce Zwei. By StrikerS she is a high-ranking Ancient Belkan mage and the commander who establishes Lost Property Riot Force 6.", "Hayate belongs to the primary Nanoha continuity. The MOVIE 2nd A's retells the Book of Darkness incident with substantial changes, while the Portable and Brave Duel versions follow separate continuities. Her established Gelbooru character tag is [[yagami_hayate]]; the empty general entry [[hayate_yagami]] should not be used as a duplicate character tag.", [NANOHA+"Hayate_Yagami", NANOHA+"Yagami", NANOHA+"Book_of_Darkness"]),
"signum": ("Signum is the leader of the Wolkenritter and the Knight of Sword. A composed Ancient Belkan knight, she fights with the Armed Device Laevatein and is one of Hayate Yagami's guardians. She later serves in the Time-Space Administration Bureau and Riot Force 6.", "In the primary A's continuity, Signum is a guardian program of the Book of Darkness who becomes part of Hayate's family. Movie, Portable and Brave Duel appearances belong to related but distinct continuities.", [NANOHA+"Signum"]),
"vita_(nanoha)": ("Vita is the Wolkenritter's Knight of the Iron Hammer and the wielder of Graf Eisen. Although she looks like a young girl, she is an experienced Ancient Belkan combatant, fiercely protective of Hayate Yagami and especially close to her.", "The Gelbooru character tag is [[vita_(nanoha)]], since [[vita]] is not the character tag. Movie, Portable and Brave Duel versions should be identified according to their respective continuities.", [NANOHA+"Vita"]),
"shamal": ("Shamal is a member of the Wolkenritter who specializes in support, healing, transport and binding magic through her Armed Device Klarwind. Within Hayate Yagami's household she also acts as a gentle caretaker, and she later works as a medical officer for the Bureau.", "Shamal's principal role begins in A's as a guardian program of the Book of Darkness. Her movie and game appearances are alternate-continuity versions of the character.", [NANOHA+"Shamal"]),
"zafira": ("Zafira is the Wolkenritter's Guardian Beast and Knight of the Shield. He normally appears either as a large blue wolf or as a muscular humanoid fighter, using defensive barriers and close combat to protect Hayate Yagami and the other knights.", "Zafira is a guardian program in the primary A's continuity, not an ordinary familiar. His appearances in movie and game continuities may alter the surrounding events without changing his core role.", [NANOHA+"Zafira"]),
"reinforce": ("Reinforce is the will and master program of the Book of Darkness. After Hayate Yagami reaches the consciousness trapped inside the corrupted tome, Hayate gives her the name Reinforce, meaning the Blessed Wind, and the two briefly fight together.", "This page concerns the original Reinforce, later called Reinforce Eins to distinguish her from [[reinforce_zwei]]. Her fate and the circumstances of the Book of Darkness incident differ between the television and movie continuities.", [NANOHA+"Reinforce"]),
"reinforce_zwei": ("Reinforce Zwei is a small Unison Device created by Hayate Yagami as the successor to the original Reinforce. She can operate independently, assist with administrative duties and unite with a compatible mage to amplify and coordinate magic.", "She appears briefly in the epilogue of A's and becomes a major supporting character from StrikerS onward. She is a distinct character from [[reinforce]], not merely a costume or age variant.", [NANOHA+"Reinforce_Zwei"]),
"aria_liese": ("Aria Liese, officially romanized as Liesearia, is one of Gil Graham's twin cat familiars and the sister of Lotte Liese. She taught Chrono Harlaown combat magic and specializes more strongly in ranged spells. During A's, the twins act in disguise as the masked men while carrying out Graham's plan.", "Gelbooru contains both [[aria_liese]] and [[liesearia_(nanoha)]]. They refer to the same character and should not be mistaken for two sisters; Aria's actual twin is [[lotte_liese]].", [NANOHA+"Aria_Liese", NANOHA+"Liese"]),
"lotte_liese": ("Lotte Liese, officially romanized as Lieselotte, is one of Gil Graham's twin cat familiars and the sister of Aria Liese. She trained Chrono Harlaown in close combat. During A's, the twins impersonate masked men and covertly advance Graham's plan for the Book of Darkness.", "Gelbooru contains both [[lotte_liese]] and [[lieselotte_(nanoha)]]. They refer to the same character and should not be treated as separate people.", [NANOHA+"Lotte_Liese", NANOHA+"Liese"]),
"gil_graham": ("Gil Graham is an English-born Time-Space Administration Bureau admiral and the master of the Liese twins. Burdened by the earlier Book of Darkness disaster that killed Clyde Harlaown, he secretly prepares a plan to complete and permanently seal the tome and its next master.", "Graham's conspiracy is part of the television A's continuity. The MOVIE 2nd A's omits his involvement and gives the relevant parts of the incident a different history.", [NANOHA+"Gil_Graham"]),
"leti_lowran": ("Leti Lowran is a Time-Space Administration Bureau admiral in the Resources Management Department and the mother of Griffith Lowran. Introduced in the A's manga, she is a colleague of Lindy Harlaown and later makes brief appearances across StrikerS and the movie continuity.", "The local Gelbooru character tag and the reference wiki spell her name [[leti_lowran]]. The requested spelling 'Letty Lowran' should redirect here rather than create a second character tag.", [NANOHA+"Leti_Lowran"]),
"mariel_atenza": ("Mariel Atenza is a Time-Space Administration Bureau Device engineer, commonly called Mari. In A's-related material she works with Leti Lowran's maintenance staff and helps adapt Raising Heart and Bardiche to use the cartridge system. She later supports Riot Force 6.", "Mariel appears in the A's manga and related continuities before taking a larger technical-support role in StrikerS. Details specific to the movies or games should be labelled accordingly.", [NANOHA+"Mariel_Atenza"]),
}

SERIES = [
 ("Primary continuity", ["mahou_shoujo_lyrical_nanoha", "mahou_shoujo_lyrical_nanoha_a's", "mahou_shoujo_lyrical_nanoha_strikers", "mahou_shoujo_lyrical_nanoha_strikers_sound_stage_x", "mahou_shoujo_lyrical_nanoha_vivid", "vivid_strike!", "mahou_senki_lyrical_nanoha_force"]),
 ("Movie continuity", ["mahou_shoujo_lyrical_nanoha_the_movie_1st", "mahou_shoujo_lyrical_nanoha_the_movie_2nd_a's", "mahou_shoujo_lyrical_nanoha_reflection", "mahou_shoujo_lyrical_nanoha_detonation", "mahou_shoujo_lyrical_nanoha_exceeds:_gun_blaze_vengeance"]),
 ("Portable and alternate continuities", ["mahou_shoujo_lyrical_nanoha_a's_portable:_the_battle_of_aces", "mahou_shoujo_lyrical_nanoha_a's_portable:_the_gears_of_destiny", "mahou_shoujo_lyrical_nanoha_innocent"]),
 ("Origins", ["triangle_heart", "triangle_heart_3", "triangle_heart_3_lyrical_toy_box"]),
]

STRIKERS_GROUPS = [
 ("Forward team", ["subaru_nakajima", "teana_lanster", "erio_mondial", "caro_ru_lushe"]),
 ("Riot Force 6 and allies", ["ginga_nakajima", "shario_finieno", "griffith_lowran", "vice_granscenic", "alto_krauetta", "lucino_liilie", "aina_triton", "carim_gracia", "schach_nouera", "verossa_acous"]),
 ("Families and background", ["genya_nakajima", "quint_nakajima", "tiida_lanster", "megane_alpine", "karel_harlaown", "liera_harlaown", "laguna_granscenic", "mira_barret", "tanto_(lyrical_nanoha)"]),
 ("Bureau leadership", ["regius_gaiz", "auris_gaiz"]),
 ("JS Incident cast", ["jail_scaglietti", "lutecia_alpine", "agito_(nanoha)", "zest_grangeitz", "garyuu_(nanoha)", "hakutenou", "vivio", "friedrich_(nanoha)", "voltaire_(nanoha)"]),
 ("Numbers", ["uno_(nanoha)", "due_(nanoha)", "tre_(nanoha)", "quattro_(nanoha)", "cinque_(nanoha)", "sein_(nanoha)", "sette_(nanoha)", "otto_(nanoha)", "nove_(nanoha)", "dieci_(nanoha)", "wendi_(nanoha)", "deed_(nanoha)"]),
]

VIVID_GROUPS = [
 ("Main generation", ["einhard_stratos", "corona_timir", "rio_wezley", "miura_rinaldi"]),
 ("Intermiddle competitors and support", ["sieglinde_eremiah", "viktoria_dahlgrun", "harry_tribeca", "els_tasmin", "mikaya_chevelle", "chantez_apinion", "fabia_crozelg", "yuna_platz", "elsa_edix", "elly_stout", "yumina_enclave", "linda_(nanoha)", "luca", "mia_(nanoha)"]),
 ("Ancient Belkan history", ["claus_ingvalt", "olivie_segbrecht", "wilfried_jeremiah", "crozelg_(nanoha)", "dahlgrun_(nanoha)"]),
 ("Leuven martial-arts schools", ["irene_hardin", "claire_lagreat", "edgar_lagreat", "ray_tundra", "rinna_tundra", "tao_raikaku", "xue_rosen_(nanoha)", "yen_lankwai_(nanoha)"]),
 ("Later manga and other characters", ["edelgard_barkas", "noah_earls", "goliath_(nanoha)"]),
]

def safe(tag: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.()+-]+", "_", tag).strip("._") or "untitled"

def save(tag: str, template: str, source: str) -> None:
    data={"tag":tag,"template":template,"source":source,"updated_at":datetime.now(timezone.utc).isoformat()}
    (OUT/f"{safe(tag)}.json").write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def main() -> None:
    OUT.mkdir(parents=True,exist_ok=True)
    uploaded = OUT / "uploaded"
    uploaded_names = {
        path.name.casefold() for path in uploaded.rglob("*") if path.is_file()
    } if uploaded.is_dir() else set()
    all_pages = {**PAGES, **AS_PAGES}
    created = 0
    skipped = 0
    for tag,(description,continuity,sources) in all_pages.items():
        if f"{safe(tag)}.json".casefold() in uploaded_names:
            skipped += 1
            continue
        source=(f"[b]Description:[/b]\n{description}\n\n[b]Continuity note:[/b]\n{continuity}\n\n"
                f"[b]Copyright:[/b]\n[[mahou_shoujo_lyrical_nanoha]]\n\n[b]See also:[/b]\n[[List_of:Mahou_Shoujo_Lyrical_Nanoha_(franchise)]]\n\n"
                "[b]External sources:[/b]\n"+"\n".join(sources))
        save(tag,"character",source)
        created += 1
    lines=["A central navigation page for the Magical Girl Lyrical Nanoha franchise, its continuities, adaptations and character pages.",""]
    for heading,tags in SERIES:
        lines.append(f"[h2]{heading}[/h2]"+"\n".join(f"* [[{tag}]]" for tag in tags))
    lines.append("[h2]Original series supporting characters[/h2]"+"\n".join(f"* [[{tag}]]" for tag in PAGES))
    lines.append("[h2]A's characters[/h2]"+"\n".join(f"* [[{tag}]]" for tag in AS_PAGES))
    lines.append("[h2]StrikerS characters[/h2]"+"".join(
        f"[h3]{heading}[/h3]"+"\n".join(f"* [[{tag}]]" for tag in tags)
        for heading,tags in STRIKERS_GROUPS
    ))
    lines.append("[h2]ViVid characters[/h2]"+"".join(
        f"[h3]{heading}[/h3]"+"\n".join(f"* [[{tag}]]" for tag in tags)
        for heading,tags in VIVID_GROUPS
    ))
    lines.extend(["[h2]External reference[/h2]", NANOHA+"Magical_Girl_Lyrical_Nanoha_Wiki", NANOHA+"Media"])
    source="\n".join(lines)
    source=re.sub(r"\n+(\[h[1-6]\])",r"\1",source)
    source=re.sub(r"(\[/h[1-6]\])\n+",r"\1",source)
    index_tag = "List_of:Mahou_Shoujo_Lyrical_Nanoha_(franchise)"
    index_created = f"{safe(index_tag)}.json".casefold() not in uploaded_names
    if index_created:
        save(index_tag,"general",source)
    snippet_name = "mahou_shoujo_lyrical_nanoha_TOP_INSERT.txt"
    snippet_created = snippet_name.casefold() not in uploaded_names
    if snippet_created:
        (OUT/snippet_name).write_text(
            "[b]Franchise index:[/b] [[List_of:Mahou_Shoujo_Lyrical_Nanoha_(franchise)]]\n",encoding="utf-8")
    print(
        f"Created {created} character drafts, skipped {skipped} uploaded character drafts; "
        f"franchise index: {'created' if index_created else 'uploaded'}; "
        f"insertion snippet: {'created' if snippet_created else 'uploaded'}"
    )

if __name__=="__main__": main()
