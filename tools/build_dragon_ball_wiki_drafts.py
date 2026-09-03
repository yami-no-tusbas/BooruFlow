from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "databases" / "g_tags_260810.db"
OUT = ROOT / "var" / "wiki_drafts" / "Dragon_Ball"


CORE = ["son_gokuu", "bulma_(dragon_ball)", "vegeta", "piccolo", "kuririn", "chi-chi_(dragon_ball)", "son_gohan", "trunks_(dragon_ball)", "son_goten", "android_18", "kame_sennin", "yamcha", "tenshinhan", "monokuma"]


def links(tags: list[str]) -> str:
    return "\n".join(f"* [[{tag}]]" for tag in tags)


PAGES: dict[str, list[str]] = {
    "dragon_ball": [
        "[b]Dragon Ball[/b] is a Japanese manga and multimedia franchise created by [[toriyama_akira]]. The original manga was serialized by [[shueisha]] from 1984 to 1995 and was adapted and expanded through anime produced by [[toei_animation]], theatrical films, video games and later manga projects.",
        "",
        "On Gelbooru, [[dragon_ball]] is the central franchise copyright. [[dragon_ball_(series)]] is an alias of this tag; do not create a parallel franchise root.",
        "[h2]Manga and television series[/h2]",
        "* [[dragon_ball_(classic)]] - the childhood adventures of Goku through the defeat of Piccolo Jr.; corresponds to the first Dragon Ball television series.",
        "* [[dragon_ball_z]] - adult Goku, the Saiyan and Namek conflicts, the Android/Cell story and Majin Buu.",
        "* [[dragon_ball_kai]] - recut and remastered version of Dragon Ball Z with reduced filler.",
        "* [[dragon_ball_gt]] - anime-original sequel continuity produced after Dragon Ball Z.",
        "* [[dragon_ball_super]] - later series set after Majin Buu and before the original manga epilogue.",
        "* [[dragon_ball_daima]] - 2024-2025 adventure set after Majin Buu and before Dragon Ball Super.",
        "[h2]Major films and specials[/h2]",
        "* [[dragon_ball_z_kami_to_kami]] - Battle of Gods.",
        "* [[dragon_ball_z_fukkatsu_no_f]] - Resurrection 'F'.",
        "* [[dragon_ball_super_broly]]",
        "* [[dragon_ball_super_super_hero]]",
        "* [[dragon_ball_z:_dead_zone]]",
        "* [[dragon_ball_z:_the_world's_strongest]]",
        "* [[dragon_ball_z:_the_tree_of_might]]",
        "* [[dragon_ball:_the_path_to_power]]",
        "[h2]Games and promotional continuities[/h2]",
        "* [[dragon_ball_fighterz]]",
        "* [[dragon_ball_xenoverse]]",
        "* [[dragon_ball_z:_kakarot]]",
        "* [[dragon_ball:_sparking!]] and [[dragon_ball_sparking_zero]]",
        "* [[dragon_ball_legends]]",
        "* [[dragon_ball_z_dokkan_battle]]",
        "* [[dragon_ball_fusions]]",
        "* [[dragon_ball_online]]",
        "* [[dragon_ball_heroes]] and [[super_dragon_ball_heroes]]",
        "[h2]Unofficial derivative works[/h2]",
        "* [[dragon_ball_multiverse]]",
        "* [[dragon_ball_af]]",
        "These are fan-created continuities and should not be attributed to Toriyama, Shueisha or Toei as official installments.",
        "[h2]Core characters[/h2]" + links(["son_gokuu", "bulma_(dragon_ball)", "vegeta", "piccolo", "kuririn", "chi-chi_(dragon_ball)", "son_gohan", "trunks_(dragon_ball)", "son_goten", "android_18", "frieza", "cell_(dragon_ball)", "majin_buu", "shenron_(dragon_ball)"]),
        "[h2]External sources[/h2]",
        "* Official Dragon Ball site: https://en.dragon-ball-official.com/",
        "* Toei Animation Dragon Ball portal: https://www.toei-anim.co.jp/tv/dragon/",
    ],
    "bird_studio": [
        "[b]Bird Studio[/b] is the creative studio associated with manga artist [[toriyama_akira]]. It is credited in connection with Dragon Ball and Toriyama's other works and rights management.",
        "[h2]Related tags[/h2]", "* [[dragon_ball]]", "* [[toriyama_akira]]", "* [[shueisha]]", "* [[toei_animation]]",
    ],
    "dragon_ball_(classic)": [
        "[b]Dragon Ball[/b] (often called Classic Dragon Ball in tags) covers the first television anime and the early portion of Toriyama's manga, following Son Goku from childhood through the 23rd Tenkaichi Budokai and his battle with Piccolo Jr.",
        "[h2]Notable characters[/h2]" + links(["son_gokuu", "bulma_(dragon_ball)", "lunch_(dragon_ball)", "chi-chi_(dragon_ball)", "kuririn", "muten_roushi", "yamcha", "tenshinhan", "piccolo", "tao_pai_pai", "pilaf", "mai_(dragon_ball)", "shenron_(dragon_ball)"]),
        "[h2]Tagging notes[/h2]", "* Use this copyright for designs and scenes specific to Goku's childhood-era adventures.", "* Use [[dragon_ball_z]] for the later Saiyan-through-Majin-Buu television era.",
    ],
    "dragon_ball_z": [
        "[b]Dragon Ball Z[/b] is the sequel television anime to the original [[dragon_ball_(classic)]], adapting the latter portion of Toriyama's Dragon Ball manga. It introduces Goku's Saiyan origin, Gohan, the Namekians, Super Saiyan transformations and the major Saiyan, Frieza, Android/Cell and Majin Buu conflicts.",
        "[h2]Heroes and allies[/h2]" + links(["son_gokuu", "son_gohan", "vegeta", "piccolo", "kuririn", "trunks_(dragon_ball)", "trunks_(future)_(dragon_ball)", "son_goten", "android_18", "videl", "mr._satan", "dende"]),
        "[h2]Major antagonists[/h2]" + links(["raditz", "nappa", "frieza", "captain_ginyu", "android_16", "android_17", "android_18", "cell_(dragon_ball)", "majin_buu", "dabura"]),
        "[h2]Related adaptations[/h2]", "* [[dragon_ball_kai]]", "* [[dragon_ball_z:_kakarot]]", "* [[dragon_ball_z_kami_to_kami]]", "* [[dragon_ball_z_fukkatsu_no_f]]",
        "[h2]External source[/h2]", "* Official Dragon Ball site: https://en.dragon-ball-official.com/",
    ],
    "dragon_ball_gt": [
        "[b]Dragon Ball GT[/b] is an anime-original sequel to [[dragon_ball_z]] produced by Toei Animation. It follows Goku after he is transformed into a child and travels through space with Pan and Trunks, later confronting Baby, Super 17 and the Shadow Dragons.",
        "",
        "GT belongs to a separate continuation from [[dragon_ball_super]] and [[dragon_ball_daima]]. Shared characters or transformations do not make their events interchangeable.",
        "[h2]Notable characters and forms[/h2]" + links(["son_gokuu", "pan_(dragon_ball)", "trunks_(dragon_ball)", "vegeta", "giru_(dragon_ball)", "baby_(dragon_ball_gt)", "super_17", "omega_shenron", "super_saiyan_4"]),
    ],
    "dragon_ball_super": [
        "[b]Dragon Ball Super[/b] is a manga and television anime continuation set after the defeat of Majin Buu and before the epilogue of the original Dragon Ball manga. Akira Toriyama supplied original story and character concepts, with the anime produced by Toei Animation and the manga illustrated by Toyotarou.",
        "",
        "The series covers the encounters with Beerus and Golden Frieza, the Universe 6 tournament, Future Trunks and Goku Black, and the Tournament of Power. The manga later continues with additional arcs including Galactic Patrol Prisoner and Granolah the Survivor.",
        "[h2]Notable characters[/h2]" + links(["son_gokuu", "vegeta", "beerus", "whis", "champa_(dragon_ball)", "vados_(dragon_ball)", "hit_(dragon_ball)", "kale_(dragon_ball)", "kefla_(dragon_ball)", "jiren", "zeno_(dragon_ball)", "goku_black", "moro_(dragon_ball)", "granolah_(dragon_ball)"]),
        "[h2]Films[/h2]", "* [[dragon_ball_super_broly]]", "* [[dragon_ball_super_super_hero]]",
        "[h2]External source[/h2]", "* Official Dragon Ball Super page: https://www.toei-anim.co.jp/tv/dragon_s/",
    ],
    "dragon_ball_daima": [
        "[b]Dragon Ball Daima[/b] is a twenty-episode anime television series broadcast from October 2024 to February 2025. Set shortly after the Majin Buu arc and before [[dragon_ball_super]], it follows Goku and his companions after King Gomah uses the Dragon Balls to transform them into children.",
        "",
        "The heroes travel into the Demon Realm with Glorio to restore their bodies, rescue Dende and oppose Gomah. The series returns to the adventurous tone of early Dragon Ball. Akira Toriyama devised its story and new character designs and was more directly involved than with the preceding Dragon Ball television productions; it was his final major Dragon Ball project before his death in 2024.",
        "[h2]Introduced characters[/h2]" + links(["glorio_(dragon_ball)", "pansy_(dragon_ball_daima)", "gomah_(dragon_ball)", "dr._arinsu", "tama_(dragon_ball)"]),
        "[h2]Continuity[/h2]", "* Follows the Majin Buu arc of [[dragon_ball_z]].", "* Precedes [[dragon_ball_super]].", "* Its child-transformation premise resembles [[dragon_ball_gt]], but the two are separate continuities.",
        "[h2]External source[/h2]", "* Official site: https://dragonballdaima.com/",
    ],
    "dragon_ball_super_broly": [
        "[b]Dragon Ball Super: Broly[/b] is a 2018 animated film produced by Toei Animation, directed by Tatsuya Nagamine and written by [[toriyama_akira]]. It follows the Tournament of Power portion of [[dragon_ball_super]] and introduces a reworked Broly and Paragus into that continuity.",
        "",
        "The film retains the previous page's important distinction: [[broly_(dragon_ball_super)]] is not the same continuity-specific version as [[broly_(dragon_ball_z)]], although both originate from the same character concept.",
        "[h2]Characters introduced or reintroduced[/h2]" + links(["broly_(dragon_ball_super)", "paragus_(dragon_ball_super)", "cheelai", "lemo_(dragon_ball)", "gogeta"]),
        "[h2]Continuity[/h2]", "* Follows [[dragon_ball_super]].", "* Followed by [[dragon_ball_super_super_hero]].",
        "[h2]External source[/h2]", "* Official film site: https://www.20thcenturystudios.com/movies/dragon-ball-super-broly",
    ],
    "dragon_ball_super_super_hero": [
        "[b]Dragon Ball Super: Super Hero[/b] is a 2022 animated film and a continuation of [[dragon_ball_super_broly]]. Piccolo and Gohan confront the revived Red Ribbon Army, its Gamma androids and Cell Max.",
        "[h2]Notable characters and forms[/h2]" + links(["son_gohan", "piccolo", "pan_(dragon_ball)", "gamma_1", "gamma_2", "cell_max", "orange_piccolo"]),
        "[h2]Related tags[/h2]", "* [[dragon_ball_super]]", "* [[dragon_ball]]",
    ],
    "dragon_ball_heroes": [
        "[b]Dragon Ball Heroes[/b] is a Japanese digital card-based arcade game and multimedia project featuring characters and alternate versions from across [[dragon_ball]]. [[super_dragon_ball_heroes]] covers the successor game and its promotional anime/manga material.",
        "[h2]Notable original characters[/h2]" + links(["beat_(dragon_ball)", "note_(dragon_ball)", "towa_(dragon_ball)", "mira_(dragon_ball)", "fu_(dragon_ball)", "hearts_(dragon_ball)", "aeos_(dragon_ball)"]),
        "[h2]Tagging notes[/h2]", "* Add the specific Heroes copyright for Xeno versions, Time Patrol material and promotional-anime designs.", "* Heroes scenarios are not automatically part of the principal manga/Super continuity.",
    ],
    "dragon_ball_fighterz": [
        "[b]Dragon Ball FighterZ[/b] is a 2.5D fighting game developed by Arc System Works and published by Bandai Namco Entertainment. Its original story prominently features [[android_21]].",
        "[h2]Related tags[/h2]", "* [[dragon_ball]]", "* [[android_21]]",
    ],
    "dragon_ball_xenoverse": [
        "[b]Dragon Ball Xenoverse[/b] is an action role-playing fighting-game series centered on customizable Time Patrollers who protect Dragon Ball history from alterations. It draws on the wider franchise and introduces characters such as Towa and Mira.",
        "[h2]Related tags[/h2]", "* [[dragon_ball]]", "* [[time_patrol_(dragon_ball)]]", "* [[towa_(dragon_ball)]]", "* [[mira_(dragon_ball)]]",
    ],
    "dragon_ball_legends": [
        "[b]Dragon Ball Legends[/b] is a mobile action role-playing game published by Bandai Namco Entertainment. Its original storyline centers on the amnesiac Saiyan Shallot and his twin Giblet.",
        "[h2]Original characters[/h2]" + links(["shallot_(dragon_ball)", "giblet_(dragon_ball)"]),
    ],
    "dragon_ball_z_dokkan_battle": [
        "[b]Dragon Ball Z Dokkan Battle[/b] is a mobile puzzle and role-playing game using characters, forms and scenarios from throughout the [[dragon_ball]] franchise.",
        "[h2]Tagging notes[/h2]", "Use this copyright for game-specific cards, promotional art, interfaces and original presentations rather than every depiction of a character who happens to be playable.",
    ],
}


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def main() -> None:
    con = sqlite3.connect(DB)
    rows = {name: (count, category) for name, count, category in con.execute("SELECT name, post_count, category FROM tags")}
    missing_roots = [tag for tag in PAGES if tag not in rows]
    if missing_roots:
        raise SystemExit(f"Missing root tags: {missing_roots}")
    OUT.mkdir(parents=True, exist_ok=True)
    for tag, lines in PAGES.items():
        payload = {"tag": tag, "template": "copyright", "source": compact("\n".join(lines)), "updated_at": datetime.now(timezone.utc).isoformat()}
        safe = tag.replace(":", "").replace("/", "_") + ".json"
        path = OUT / safe
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Wrote", path)


if __name__ == "__main__":
    main()
