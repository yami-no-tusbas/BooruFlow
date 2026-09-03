from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Science Adventure"

BRANCHES = [
    "chaos;head", "steins;gate", "robotics;notes", "chaos;child",
    "occultic;nine", "anonymous;code",
]
STEINS = [
    "okabe_rintarou", "makise_kurisu", "shiina_mayuri", "hashida_itaru",
    "kiryuu_moeka", "urushibara_ruka", "faris_nyannyan", "amane_suzuha",
    "tennouji_yuugo", "tennouji_nae",
]
ZERO = [
    "hiyajou_maho", "amane_yuki", "shiina_kagari", "judy_reyes",
    "alexis_leskinen",
]
CHAOS_HEAD = [
    "nishijou_takumi", "sakihata_rimi", "aoi_sena", "kusunoki_yua",
    "kishimoto_ayase", "nishijou_nanami", "orihara_kozue", "seira_orgel",
    "hazuki_shino_(chaos;head)",
]
ROBOTICS = [
    "yashio_kaito", "senomiya_akiho", "koujiro_frau", "hidaka_subaru",
    "daitoku_junna", "airi_(robotics;notes)", "tennouji_nae_(robotics;notes)",
]
CHAOS_CHILD = [
    "miyashiro_takuru", "onoe_serika", "kurusu_nono", "arimura_hinae",
    "kazuki_hana", "yamazoe_uki", "kunosato_mio",
    "itou_shinji_(chaos;child)",
]
OCCULTIC = [
    "gamon_yuuta", "narusawa_ryouka", "hashigami_sarai", "aikawa_miyuu",
    "sumikaze_touko", "kurenaino_aria", "moritsuka_shun",
    "zonko_(occultic;nine)", "izumi_kouhei_(occultic;nine)",
]
DERIVATIVES = [
    "steins;gate_0", "steins;gate:_linear_bounded_phenogram",
    "steins;gate:_my_darling's_embrace", "steins;gate:_fuka_ryouiki_no_dejavu",
    "steins;gate:_the_committee_of_antimatter",
    "chaos;head_love_chu_chu!", "robotics;notes_dash",
    "chaos;child_love_chu_chu!!", "chaos;child:_children's_collapse",
    "chaos;child:_children's_revive", "steins;gate_re:boot",
]

CHARACTER_DESCRIPTIONS = {
    "steins_gate": {
        "okabe_rintarou": "Founder of the Future Gadget Laboratory, self-styled mad scientist Hououin Kyouma, and the principal viewpoint character. His Reading Steiner lets him retain memories across changes to world lines.",
        "makise_kurisu": "A brilliant young neuroscience researcher and Future Gadget Lab Member 004. Her theories and relationship with Okabe are central to the development and consequences of time travel.",
        "shiina_mayuri": "Okabe's childhood friend and Future Gadget Lab Member 002. Her warmth holds the laboratory together, while her fate becomes a central fixed point in the Alpha world lines.",
        "hashida_itaru": "Future Gadget Lab Member 003, usually called Daru. He is a highly capable hacker and the principal engineer behind many of the laboratory's devices.",
        "kiryuu_moeka": "Future Gadget Lab Member 005, an obsessive phone user searching for the IBN 5100. Her dependence on the mysterious FB places her at the center of SERN's Rounder operation.",
        "urushibara_ruka": "Future Gadget Lab Member 006 and an exceptionally gentle shrine attendant. Ruka admires Okabe and is closely involved with one of the story's major D-Mail changes.",
        "faris_nyannyan": "Future Gadget Lab Member 007, MayQueen+Nyan² maid and successful Rai-Net player. Her public persona conceals her identity as Rumiho Akiha and her influence over Akihabara.",
        "amane_suzuha": "Future Gadget Lab Member 008 and a part-time worker at the CRT shop. Her true origin, mission and connection to Daru are crucial to the struggle over the future.",
        "tennouji_yuugo": "Owner of the CRT shop beneath the laboratory and Nae's father. Okabe calls him Mr. Braun; his hidden responsibilities connect the laboratory to SERN.",
        "tennouji_nae": "Yuugo Tennouji's young daughter. She later returns as an adult JAXA employee in Robotics;Notes, linking the two Science Adventure branches.",
    },
    "steins_gate_0": {
        "hiyajou_maho": "A neuroscientist at Viktor Chondria University, Kurisu's senior colleague and one of the developers of Amadeus. She becomes a key ally to Okabe.",
        "amane_yuki": "Suzuha's mother and Daru's future wife. In 2010 she is a friend of Mayuri and works at the same cosplay circle.",
        "shiina_kagari": "Mayuri's adopted daughter in the war-torn future who travels back in time with Suzuha. Her disappearance and altered memories form a major mystery.",
        "judy_reyes": "A professor associated with Viktor Chondria University whose research interests and affiliations draw her into the conflict surrounding Amadeus and time-machine technology.",
        "alexis_leskinen": "A Viktor Chondria professor and leading member of the Amadeus project. His jovial academic persona conceals a major role in the struggle over artificial intelligence and time travel.",
    },
    "chaos_head": {
        "nishijou_takumi": "An isolated Shibuya student and obsessive otaku who becomes the principal suspect and viewpoint character in the New Generation Madness case.",
        "sakihata_rimi": "A mysterious girl Takumi first encounters at a murder scene. She is a powerful Gigalomaniac whose history is tied to Shogun and the Noah project.",
        "aoi_sena": "A combative Gigalomaniac who investigates the NOZOMI Technology Group and uses a DI-sword. Her search is deeply personal.",
        "kusunoki_yua": "A student who approaches Takumi while investigating his possible connection to the New Generation murders and the death of her sister.",
        "kishimoto_ayase": "Vocalist of the band Phantasm under the name FES and a Gigalomaniac. Her songs and visions appear to predict events surrounding New Generation.",
        "nishijou_nanami": "Takumi's younger sister. Although frustrated by his withdrawn behavior, she continues to care for him and becomes a target in the conspiracy.",
        "orihara_kozue": "A quiet transfer student and Gigalomaniac who communicates telepathically. She forms a close friendship with Sena.",
        "seira_orgel": "The heroine of Takumi's favorite fictional anime, Blood Tune. His imagined version of Seira acts as a companion and embodiment of his escapism.",
        "hazuki_shino_(chaos;head)": "A nurse at AH Tokyo General Hospital whose apparent supporting role hides a direct connection to the New Generation incidents.",
    },
    "robotics_notes": {
        "yashio_kaito": "A laid-back member of the Robot Research Club and expert at the fighting game Kill-Ballad. The Elephant-Mouse Syndrome gives him moments of accelerated perception.",
        "senomiya_akiho": "Energetic president of the Robot Research Club and driving force behind the attempt to complete the giant Gunvarrel robot.",
        "koujiro_frau": "Genius programmer of Kill-Ballad and daughter of Gunvarrel's director. Her real name is Kona Furugoori and she speaks heavily in internet slang.",
        "hidaka_subaru": "A skilled robotics student who initially hides his involvement with the club from his strict father and favors practical machine design.",
        "daitoku_junna": "A timid karate student recruited into the Robot Research Club. She gradually gains confidence while confronting memories involving robots.",
        "airi_(robotics;notes)": "An artificial-intelligence interface encountered through the Iru-O augmented-reality system. Her identity and memories connect to Kimijima Kou's plans.",
        "tennouji_nae_(robotics;notes)": "The adult Nae Tennouji, now working for JAXA. She assists the Robot Research Club and directly connects Robotics;Notes to Steins;Gate.",
    },
    "chaos_child": {
        "miyashiro_takuru": "President of Hekiho Academy's newspaper club and protagonist of Chaos;Child. He investigates the Return of the New Generation Madness murders.",
        "onoe_serika": "Takuru's childhood friend and fellow newspaper-club member. Her origin and devotion to Takuru lie at the center of the case.",
        "kurusu_nono": "Takuru's foster sister, student-council president and a protective member of the Aoba Dormitory family.",
        "arimura_hinae": "A newspaper-club member and Gigalomaniac whose ability allows her to detect lies in another person's statements.",
        "kazuki_hana": "A quiet newspaper-club member, gamer and Gigalomaniac whose spoken delusions can manifest in extraordinary ways.",
        "yamazoe_uki": "A young survivor connected to the AH Tokyo General Hospital experiments who comes to live with the Aoba Dormitory family.",
        "kunosato_mio": "A sharp-tongued neuroscientist investigating Gigalomaniacs and the Return of New Generation case, with prior ties to Kurisu and Viktor Chondria University.",
        "itou_shinji_(chaos;child)": "Takuru's close friend and newspaper-club colleague. His involvement in the case places severe strain on their friendship.",
    },
    "occultic_nine": {
        "gamon_yuuta": "Operator of the occult-aggregation blog Kiri Kiri Basara and the central character whose attempt to profit from paranormal stories draws nine people together.",
        "narusawa_ryouka": "Yuuta's exuberant childhood friend, nicknamed Ryotas. She carries an unusual radio-like gun and remains close to him throughout the mystery.",
        "hashigami_sarai": "A rational university student and son of Professor Hashigami who rejects occult explanations while investigating his father's death.",
        "aikawa_miyuu": "A popular fortune-teller known online as Myu who performs divinations together with her close friend Chiizu.",
        "sumikaze_touko": "A reporter for the occult magazine Mumuu who investigates the incidents linking the principal cast.",
        "kurenaino_aria": "Owner of the black-magic shop Black Magic Proxy Shop, where she claims to place curses for clients.",
        "moritsuka_shun": "An eccentric detective investigating the deaths and supernatural-looking events surrounding Yuuta's group.",
        "zonko_(occultic;nine)": "The mysterious personality who speaks to Yuuta through his radio and guides or provokes him at crucial moments.",
        "izumi_kouhei_(occultic;nine)": "A young man associated with Aria and her curse business, commonly called the Devil or Kusakabe Kiryuu in different contexts.",
    },
    "anonymous_code": {
        "pollon": "A young hacker living in Nakano in 2037 and the protagonist of Anonymous;Code. His Save & Load ability lets him reload points in reality while pursuing Momo and the truth of the world layers.",
    },
}


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def bullets(tags: list[str]) -> list[str]:
    return [f"* [[{tag}]]" for tag in tags]


def common_notes(tag: str) -> list[str]:
    return [
        "[h2]Tagging notes[/h2]",
        f"Use [[{tag}]] for material from either the visual novel or its direct anime adaptation; Gelbooru does not maintain separate copyright tags for those versions. Add a more specific sequel, spin-off, film or manga tag when one exists and the source is identifiable.",
        "",
        "Tag characters individually. Adaptation-only costume differences do not create a separate character unless Gelbooru has an established tag for that form.",
    ]


def science_source() -> str:
    lines = [
        "[b]Science Adventure[/b], commonly abbreviated SciADV, is a science-fiction visual-novel and multimedia franchise planned and originally created by Chiyomaru Shikura and produced principally by MAGES. Its official concept is '99% science and 1% fantasy': each branch builds a suspense story around real or speculative scientific and technological subjects.",
        "",
        "The principal stories share a setting, organizations, technologies and recurring characters, but each branch has its own protagonists and central mystery. The official portal lists six lines:",
        "[h2]Main branches[/h2]",
        "* [[chaos;head]] - Delusional Science Adventure; Shibuya, delusions and reality alteration. Began in 2008.",
        "* [[steins;gate]] - Hypothetical Science Adventure; Akihabara, time travel and world lines. Began in 2009.",
        "* [[robotics;notes]] - Augmented Science Adventure; Tanegashima, robotics and augmented reality. Began in 2012.",
        "* [[chaos;child]] - Delusional Science Adventure and thematic successor to Chaos;Head. Began in 2014.",
        "* [[occultic;nine]] - Paranormal Science story spanning novels, anime, manga and a 2017 game.",
        "* [[anonymous;code]] - Meta Science Adventure centered on hackers and simulated reality. Released in 2022.",
        "[h2]Major sequels and side works[/h2]",
        "* [[steins;gate_0]] - interquel/alternate-world-line story essential to Steins;Gate's larger narrative.",
        "* [[robotics;notes_dash]] - sequel to Robotics;Notes.",
        "* [[chaos;head_love_chu_chu!]] and [[chaos;child_love_chu_chu!!]] - direct character-focused follow-ups whose comedic presentation still continues their respective stories.",
        "* [[steins;gate:_linear_bounded_phenogram]] and [[steins;gate:_my_darling's_embrace]] - Steins;Gate side games.",
        "[h2]Media[/h2]",
        "The franchise began with visual novels but also includes television anime, films, manga, light novels, audio dramas and stage works. Shared Gelbooru copyright tags normally cover both a game and its anime adaptation unless a separately named work has an established tag.",
        "[h2]Tagging notes[/h2]",
        "Use [[science_adventure]] for cross-series material, franchise-wide promotional art or works combining multiple SciADV branches. For material belonging to only one branch, use its specific copyright tag rather than automatically treating the portal tag as mandatory.",
        "[h2]External links[/h2]",
        "* Official Science Adventure portal and title lineup: https://www.kagaku-adv.com/",
    ]
    return compact("\n".join(lines))


def steins_source() -> str:
    lines = [
        "[b]Steins;Gate[/b] is the second main [[science_adventure]] visual novel, originally released in 2009 and developed by 5pb. and Nitroplus. It follows the Future Gadget Laboratory in Akihabara after a modified microwave is discovered to send messages into the past. Changes to history move the characters between world lines, drawing them into conflict with SERN and a struggle to reach the Steins Gate world line.",
        "[h2]Future Gadget Laboratory[/h2]", *bullets(STEINS),
        "[h2]Games[/h2]",
        "* Steins;Gate - original visual novel.",
        "* [[steins;gate:_my_darling's_embrace]] - romantic comedy spin-off.",
        "* [[steins;gate:_linear_bounded_phenogram]] - side stories told from multiple viewpoints.",
        "* Steins;Gate 8-bit / Variant Space Octet - retro-styled text adventure.",
        "* [[steins;gate_0]] - alternate-world-line interquel.",
        "* Steins;Gate Elite - animated remake of the original visual novel.",
        "* [[steins;gate_re:boot]] - expanded remake with redrawn art and an additional Gamma world-line route, scheduled for 2026.",
        "[h2]Anime and other media[/h2]",
        "The 2011 television anime adapts the original visual novel. It was followed by the OVA Egoistic Poriomania and the 2013 film [[steins;gate:_fuka_ryouiki_no_dejavu]]. The franchise also includes manga, audio dramas, novels and stage productions. [[steins;gate:_the_committee_of_antimatter]] identifies a cancelled novel project and should not be presented as completed canon.",
        "[h2]Relationship to Steins;Gate 0[/h2]",
        "Steins;Gate 0 explores the Beta world-line future of an Okabe who fails to save Kurisu. It is not a conventional sequel set after the original ending; its events supply part of the path that makes that ending possible.",
        *common_notes("steins;gate"),
        "[h2]External links[/h2]",
        "* Official Science Adventure portal: https://www.kagaku-adv.com/",
        "* Official Steins;Gate Re:Boot website: https://steinsgate.jp/reboot/en-us/",
    ]
    return compact("\n".join(lines))


def zero_source() -> str:
    lines = [
        "[b]Steins;Gate 0[/b] is an alternate-world-line interquel to [[steins;gate]], released as a visual novel in 2015 and adapted as a television anime in 2018. It follows Rintarou Okabe after he gives up trying to save Kurisu and abandons the Hououin Kyouma persona.",
        "",
        "The story develops the Beta world line, the Amadeus artificial-intelligence project, competing time-machine research and the future leading toward World War III. The game uses branching routes; the anime is not merely a route-by-route copy and incorporates material that complements the visual novel.",
        "[h2]Returning characters[/h2]", *bullets(STEINS),
        "[h2]Characters introduced or expanded in 0[/h2]", *bullets(ZERO),
        "[h2]Amadeus[/h2]",
        "Amadeus is an artificial-intelligence system built from digitized human memories. Amadeus Kurisu resembles and speaks as Kurisu but is a software reconstruction, not the living Kurisu from the Alpha or Steins Gate world lines.",
        "[h2]Relationship to the original[/h2]",
        "Steins;Gate 0 begins from the failed rescue shown around the original story's Beta-world-line events. Its outcome contributes to the plan used by the original Okabe to reach Steins Gate. It should therefore be tagged separately when its costumes, Amadeus material, new characters or Beta-future context are identifiable.",
        *common_notes("steins;gate_0"),
        "[h2]External links[/h2]",
        "* Official Spike Chunsoft overview: https://www.spike-chunsoft.com/games/steinsgate-0/",
        "* Official Science Adventure portal: https://www.kagaku-adv.com/",
    ]
    return compact("\n".join(lines))


def branch_source(tag: str, title: str, intro: str, characters: list[str], works: list[str]) -> str:
    lines = [
        f"[b]{title}[/b] {intro}",
        "[h2]Principal characters[/h2]", *bullets(characters),
        "[h2]Games and adaptations[/h2]", *works,
        *common_notes(tag),
        "[h2]Related tags[/h2]",
        "* [[science_adventure]] - parent franchise portal.",
        "[h2]External links[/h2]",
        "* Official Science Adventure portal: https://www.kagaku-adv.com/",
    ]
    return compact("\n".join(lines))


def character_source(tag: str, branch: str, description: str) -> str:
    copyright_tag = "steins;gate_0" if branch == "steins_gate_0" else branch.replace("_", ";", 1) if branch in {"chaos_head", "chaos_child", "robotics_notes", "occultic_nine", "anonymous_code"} else "steins;gate"
    # Folder names are filesystem-safe; explicit correction keeps the actual Gelbooru tags exact.
    copyright_tag = {
        "chaos_head": "chaos;head", "chaos_child": "chaos;child",
        "robotics_notes": "robotics;notes", "occultic_nine": "occultic;nine",
        "anonymous_code": "anonymous;code", "steins_gate": "steins;gate",
        "steins_gate_0": "steins;gate_0",
    }[branch]
    related = [f"[[{copyright_tag}]]", "[[science_adventure]]"]
    if branch == "steins_gate_0":
        related.insert(1, "[[steins;gate]]")
    return "\n".join([
        "[b]Description:[/b]",
        description,
        "",
        "[b]Copyright:[/b]",
        *related,
        "",
        "[b]Tagging notes:[/b]",
        f"Use [[{tag}]] when this character is depicted. Add the relevant game copyright tag above; Gelbooru uses it for both the visual novel and its direct anime adaptation.",
        "",
        "[b]External source:[/b]",
        "https://www.kagaku-adv.com/",
    ])


def write_character_drafts(connection: sqlite3.Connection) -> tuple[int, int]:
    characters_root = OUT / "characters"
    uploaded_names = {
        path.name.casefold()
        for uploaded in [OUT / "uploaded", characters_root / "uploaded"]
        if uploaded.exists()
        for path in uploaded.rglob("*.json")
    }
    written = skipped = 0
    for branch, entries in CHARACTER_DESCRIPTIONS.items():
        branch_out = characters_root / branch
        branch_out.mkdir(parents=True, exist_ok=True)
        branch_uploaded = branch_out / "uploaded"
        local_uploaded = ({p.name.casefold() for p in branch_uploaded.rglob("*.json")}
                          if branch_uploaded.exists() else set())
        for tag, description in entries.items():
            if not connection.execute("SELECT 1 FROM tags WHERE name=?", (tag,)).fetchone():
                raise SystemExit(f"Missing local character tag: {tag}")
            filename = re.sub(r"[^0-9A-Za-z._()-]+", "_", tag).strip("_") + ".json"
            if filename.casefold() in uploaded_names or filename.casefold() in local_uploaded:
                skipped += 1
                continue
            payload = {
                "tag": tag,
                "template": "character",
                "source": character_source(tag, branch, description),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            (branch_out / filename).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            written += 1
    return written, skipped


def main() -> None:
    drafts = {
        "science_adventure": ("science_adventure.json", [*BRANCHES, *DERIVATIVES], science_source()),
        "steins;gate": ("steins_gate.json", ["science_adventure", *STEINS, *DERIVATIVES[:5], "steins;gate_re:boot"], steins_source()),
        "steins;gate_0": ("steins_gate_0.json", ["science_adventure", "steins;gate", *STEINS, *ZERO], zero_source()),
        "chaos;head": ("chaos_head.json", ["science_adventure", *CHAOS_HEAD, "chaos;head_love_chu_chu!"], branch_source(
            "chaos;head", "Chaos;Head", "is the first [[science_adventure]] visual novel, released in 2008. Takumi Nishijou becomes entangled in Shibuya's New Generation Madness murders as delusions, Gigalomaniacs and reality-altering DI-swords blur what is real.", CHAOS_HEAD,
            ["* Chaos;Head - original game.", "* Chaos;Head NoAH - expanded definitive version with heroine routes.", "* [[chaos;head_love_chu_chu!]] - direct follow-up.", "* The 2008 television anime adapts the original story under the same Gelbooru copyright tag."],
        )),
        "robotics;notes": ("robotics_notes.json", ["science_adventure", *ROBOTICS, "robotics;notes_dash"], branch_source(
            "robotics;notes", "Robotics;Notes", "is the third main [[science_adventure]] visual novel, released in 2012. Members of Tanegashima Central High's Robot Research Club attempt to build a giant robot while uncovering the Kimijima Reports and a conspiracy involving augmented reality and solar activity.", ROBOTICS,
            ["* Robotics;Notes - original game and 2012 television anime.", "* Robotics;Notes Elite - revised and expanded game edition.", "* [[robotics;notes_dash]] - sequel featuring returning SciADV character Daru."],
        )),
        "chaos;child": ("chaos_child.json", ["science_adventure", "chaos;head", *CHAOS_CHILD, "chaos;child_love_chu_chu!!", "chaos;child:_children's_collapse", "chaos;child:_children's_revive"], branch_source(
            "chaos;child", "Chaos;Child", "is the fourth main [[science_adventure]] visual novel, released in 2014 and set in a rebuilt Shibuya after the events of [[chaos;head]]. Newspaper-club president Takuru Miyashiro investigates a new series of murders whose dates mirror the New Generation Madness case.", CHAOS_CHILD,
            ["* Chaos;Child - original visual novel and 2017 television anime.", "* [[chaos;child_love_chu_chu!!]] - direct follow-up.", "* [[chaos;child:_children's_collapse]] - manga centered on Mio Kunosato.", "* [[chaos;child:_children's_revive]] - prose epilogue."],
        )),
        "occultic;nine": ("occultic_nine.json", ["science_adventure", *OCCULTIC], branch_source(
            "occultic;nine", "Occultic;Nine", "is a paranormal science story by Chiyomaru Shikura and an official branch of [[science_adventure]]. It began as a light-novel series in 2014, received manga and television-anime adaptations, and became a visual novel in 2017. Nine people connected through Yuuta Gamon's occult blog become involved in a mass-death mystery.", OCCULTIC,
            ["* Occultic;Nine light novels - original version.", "* Occultic;Nine television anime - 2016 adaptation.", "* Occultic;Nine visual novel - released in 2017.", "* Gelbooru uses [[occultic;nine]] across these media."],
        )),
        "anonymous;code": ("anonymous_code.json", ["science_adventure", "pollon"], branch_source(
            "anonymous;code", "Anonymous;Code", "is the sixth main [[science_adventure]] game, released in 2022. In 2037, hacker Pollon Takaoka gains the ability to Save and Load reality and becomes involved with Momo Aizaki, world-simulating supercomputers and a crisis extending across layers of simulated existence.", ["pollon"],
            ["* Anonymous;Code - original visual novel. An international edition followed in 2023.", "* Principal cast without established local character tags includes Momo Aizaki, Cross Yumikawa, Wind Maki, Tengen Ozutani and Bambi Kurashina."],
        )),
    }
    connection = sqlite3.connect(DB)
    OUT.mkdir(parents=True, exist_ok=True)
    for tag, (filename, links, source) in drafts.items():
        missing = [name for name in [tag, *links] if not connection.execute(
            "SELECT 1 FROM tags WHERE name=?", (name,)
        ).fetchone()]
        if missing:
            raise SystemExit(f"Missing local tags for {tag}: {missing}")
        payload = {"tag": tag, "template": "copyright", "source": source,
                   "updated_at": datetime.now(timezone.utc).isoformat()}
        destination = OUT / filename
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {destination} | validated tags {len(set(links))}")
    written, skipped = write_character_drafts(connection)
    print(f"character drafts written {written} | uploaded exclusions {skipped}")


if __name__ == "__main__":
    main()
