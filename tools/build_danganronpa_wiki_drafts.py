from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "databases" / "g_tags_260810.db"
OUT = ROOT / "var" / "wiki_drafts" / "Danganronpa"


DR1 = ["naegi_makoto", "kirigiri_kyouko", "togami_byakuya", "maizono_sayaka", "kuwata_leon", "fujisaki_chihiro", "owada_mondo", "ishimaru_kiyotaka", "yamada_hifumi", "celestia_ludenberg", "ogami_sakura", "asahina_aoi", "hagakure_yasuhiro", "fukawa_touko", "ikusaba_mukuro", "enoshima_junko", "monokuma"]
DR2 = ["hinata_hajime", "komaeda_nagito", "nanami_chiaki", "souda_kazuichi", "tanaka_gundam", "sonia_nevermind", "kuzuryuu_fuyuhiko", "pekoyama_peko", "mioda_ibuki", "tsumiki_mikan", "koizumi_mahiru", "saionji_hiyoko", "owari_akane", "nidai_nekomaru", "hanamura_teruteru", "togami_byakuya_(danganronpa_2)", "monomi_(danganronpa)"]
UDG = ["naegi_komaru", "fukawa_touko", "utsugi_kotoko", "kemuri_jataro", "shingetsu_nagisa", "daimon_masaru_(danganronpa)", "monaca", "servant_(danganronpa)", "shirokuma_(danganronpa)", "kurokuma_(danganronpa)"]
V3 = ["akamatsu_kaede", "saihara_shuuichi", "ouma_kokichi", "momota_kaito", "harukawa_maki", "shirogane_tsumugi", "chabashira_tenko", "yonaga_angie", "iruma_miu", "gokuhara_gonta", "hoshi_ryouma", "toujou_kirumi", "yumeno_himiko", "amami_rantarou", "shinguuji_korekiyo", "monokid", "monodam", "monophanie", "monotaro_(danganronpa)"]


def bullets(tags: list[str]) -> str:
    return "\n".join(f"* [[{tag}]]" for tag in tags)


PAGES: dict[str, tuple[str, list[str]]] = {
    "danganronpa_(series)": ("copyright", [
        "[b]Danganronpa[/b] is a Japanese mystery, visual novel and adventure game franchise created by Kazutaka Kodaka and developed and published by [[spike_(company)]], later [[spike_chunsoft]]. The series combines investigation, Class Trials, black comedy and killing-game scenarios centered on students with exceptional talents called Ultimates.",
        "",
        "The franchise expanded from games into television anime, original animation, novels, manga and stage productions. [[monokuma]] is its principal mascot.",
        "[h2]Main games[/h2]",
        "* [[danganronpa:_trigger_happy_havoc]] - the first game, set at Hope's Peak Academy.",
        "* [[danganronpa_2:_goodbye_despair]] - the second game, set around Jabberwock Island.",
        "* [[danganronpa_v3:_killing_harmony]] - a later main game with a new setting and cast.",
        "* [[danganronpa_2x2]] - an expanded reworking of Goodbye Despair with both the original scenario and a new alternate killing game; currently announced for early 2027.",
        "[h2]Game spin-offs and collections[/h2]",
        "* [[danganronpa_another_episode:_ultra_despair_girls]] - action-adventure spin-off connecting the first two Hope's Peak games.",
        "* [[danganronpa_s:_ultimate_summer_camp]] - non-killing-game crossover board-game RPG using characters from the principal games.",
        "* Danganronpa Decadence - Nintendo Switch collection containing the three main games and Danganronpa S; no exact local tag was found.",
        "[h2]Anime and original animation[/h2]",
        "* Danganronpa: The Animation - television adaptation of Trigger Happy Havoc; no separate local copyright tag was found.",
        "* [[danganronpa_3_(anime)]] - anime-original conclusion to the Hope's Peak storyline. It is not the same work as V3.",
        "* [[super_danganronpa_2.5:_komaeda_nagito_to_sekai_no_hakaimono]] - original video animation centered on Nagito Komaeda.",
        "[h2]Novels and manga[/h2]",
        "* [[danganronpa/zero]]",
        "* [[danganronpa_kirigiri]]",
        "* [[danganronpa:_togami]]",
        "* [[danganronpa_gaiden:_killer_killer]]",
        "* [[danganronpa:_trigger_happy_havoc_if]]",
        "[h2]Ambiguous and legacy tags[/h2]",
        "* [[danganronpa_3]] is ambiguous and deprecated: use [[danganronpa_3_(anime)]] or [[danganronpa_v3:_killing_harmony]].",
        "* [[danganronpa_v3]] is an alias of the full Killing Harmony copyright.",
        "* [[danganronpa]] is a smaller legacy copyright tag; use [[danganronpa_(series)]] for franchise-wide works.",
        "[h2]Unofficial fangames[/h2]",
        "* [[danganronpa_another:_another_despair_academy]]",
        "* [[super_danganronpa_another_2]]",
        "These are fan-created works and must not be attributed to Spike Chunsoft merely because they imitate the franchise format.",
        "[h2]External sources[/h2]",
        "* Spike Chunsoft Danganronpa Decadence overview: https://www.spike-chunsoft.com/games/danganronpa-decadence/",
        "* Spike Chunsoft franchise news: https://www.spike-chunsoft.com/news/danganronpa-series-surpasses-10-million-units-shipped-worldwide/",
    ]),
    "spike_chunsoft": ("copyright", [
        "[b]Spike Chunsoft[/b] is a Japanese video game developer and publisher established in 2012 through the merger of [[spike_(company)]] and [[chunsoft]]. Its franchises include [[danganronpa_(series)]] and Mystery Dungeon, alongside numerous developed, localized and published titles.",
        "[h2]Danganronpa relationship[/h2]",
        "The original Danganronpa was developed and published by Spike before the merger. Later releases, ports and international editions are associated with Spike Chunsoft. Use the specific game copyright for ordinary character artwork rather than adding the company tag automatically.",
        "[h2]Related tags[/h2]", "* [[spike_(company)]]", "* [[chunsoft]]", "* [[danganronpa_(series)]]",
        "[h2]External sources[/h2]", "* Official company profile: https://www.spike-chunsoft.com/company/", "* Official site: https://www.spike-chunsoft.com/",
    ]),
    "danganronpa:_trigger_happy_havoc": ("copyright", [
        "[b]Danganronpa: Trigger Happy Havoc[/b] is the first game in the [[danganronpa_(series)]] franchise. Fifteen students are imprisoned inside Hope's Peak Academy by [[monokuma]] and forced into a killing game whose murders are judged through Class Trials.",
        "[h2]Students and principal characters[/h2]" + bullets(DR1),
        "[h2]Adaptations and related works[/h2]", "The game was adapted as Danganronpa: The Animation. [[danganronpa:_trigger_happy_havoc_if]] is an alternate-story novel included with later releases.",
        "[h2]External source[/h2]", "* Official series collection page: https://www.spike-chunsoft.com/games/danganronpa-decadence/",
    ]),
    "danganronpa_2:_goodbye_despair": ("copyright", [
        "[b]Danganronpa 2: Goodbye Despair[/b] is the second main game in the [[danganronpa_(series)]] franchise. Hajime Hinata and his classmates arrive on Jabberwock Island for a school trip that becomes another killing game.",
        "[h2]Students and principal characters[/h2]" + bullets(DR2),
        "[h2]Related works[/h2]", "* [[super_danganronpa_2.5:_komaeda_nagito_to_sekai_no_hakaimono]]", "* [[danganronpa_3_(anime)]]",
        "[h2]External source[/h2]", "* Official series collection page: https://www.spike-chunsoft.com/games/danganronpa-decadence/",
    ]),
    "danganronpa_another_episode:_ultra_despair_girls": ("copyright", [
        "[b]Danganronpa Another Episode: Ultra Despair Girls[/b] is an action-adventure spin-off of [[danganronpa_(series)]], set between Trigger Happy Havoc and Goodbye Despair. Komaru Naegi and Toko Fukawa attempt to survive the Monokuma-controlled city of Towa.",
        "[h2]Characters[/h2]" + bullets(UDG),
        "[h2]External source[/h2]", "* Official game page: https://www.spike-chunsoft.com/games/danganronpa-another-episode-ultra-despair-girls/",
    ]),
    "danganronpa_v3:_killing_harmony": ("copyright", [
        "[b]Danganronpa V3: Killing Harmony[/b] is a main game in the [[danganronpa_(series)]] franchise featuring a new cast at the Ultimate Academy for Gifted Juveniles. It must not be confused with [[danganronpa_3_(anime)]].",
        "[h2]Students and Monokubs[/h2]" + bullets(V3),
        "[h2]Tagging notes[/h2]", "* [[danganronpa_v3]] is an alias; use this full copyright.", "* [[danganronpa_3]] is ambiguous and should not be used.",
        "[h2]External source[/h2]", "* Official game page: https://www.spike-chunsoft.com/games/danganronpa-v3-killing-harmony/",
    ]),
    "danganronpa_s:_ultimate_summer_camp": ("copyright", [
        "[b]Danganronpa S: Ultimate Summer Camp[/b] is a board-game RPG spin-off bringing together characters from the principal [[danganronpa_(series)]] games in a non-killing-game scenario on Jabberwock Island. It expands the Ultimate Talent Development Plan bonus mode from V3.",
        "[h2]Characters[/h2]", "The crossover roster draws primarily from [[danganronpa:_trigger_happy_havoc]], [[danganronpa_2:_goodbye_despair]], [[danganronpa_another_episode:_ultra_despair_girls]] and [[danganronpa_v3:_killing_harmony]]. Add the character's base tag as well as this copyright when the Summer Camp setting or design is identifiable.",
        "[h2]External source[/h2]", "* Official Danganronpa Decadence page: https://www.spike-chunsoft.com/games/danganronpa-decadence/",
    ]),
    "danganronpa_2x2": ("copyright", [
        "[b]Danganronpa 2x2[/b] is an expanded reworking of [[danganronpa_2:_goodbye_despair]] developed for modern platforms. It includes an updated version of the original game and Slayhem Mode, a new full-length alternate scenario using the same setting and cast but different victims, culprits and mysteries.",
        "",
        "The game is developed by Gemdrops in collaboration with Spike Chunsoft and Too Kyo Games, with Kazutaka Kodaka supervising the new scenario. Its release is currently scheduled for early 2027.",
        "[h2]Characters[/h2]" + bullets(DR2),
        "[h2]Tagging notes[/h2]", "* Use [[danganronpa_2x2]] for redesigned promotional material, the new Slayhem scenario and content identifiable as belonging to this release.", "* Continue to use [[danganronpa_2:_goodbye_despair]] for material based only on the original 2012 game.",
        "[h2]External sources[/h2]", "* Official site: https://www.danganronpa.com/pages/2x2/en/", "* Official Spike Chunsoft game page: https://www.spike-chunsoft.com/games/danganronpa-2x2/",
    ]),
    "danganronpa_3_(anime)": ("copyright", [
        "[b]Danganronpa 3: The End of Hope's Peak High School[/b] is a 2016 anime-original entry that concludes the Hope's Peak storyline of [[danganronpa_(series)]]. It is divided into Future Arc, Despair Arc and Hope Arc and uses characters from the first two games alongside a new Future Foundation cast.",
        "[h2]Tagging notes[/h2]", "* Use this copyright for the anime, including its anime-original characters and designs.", "* This is not the third main game. For that work, use [[danganronpa_v3:_killing_harmony]].", "* [[danganronpa_3]] is ambiguous and deprecated.",
        "[h2]Related works[/h2]", "* [[danganronpa:_trigger_happy_havoc]]", "* [[danganronpa_2:_goodbye_despair]]", "* [[super_danganronpa_2.5:_komaeda_nagito_to_sekai_no_hakaimono]]",
        "[h2]External source[/h2]", "* Official Japanese anime site: https://www.nbcuni.co.jp/anime/danganronpa3/",
    ]),
    "danganronpa/zero": ("copyright", [
        "[b]Danganronpa/Zero[/b] is a two-volume light novel written by series creator Kazutaka Kodaka and illustrated by Rui Komatsuzaki. It is a prequel to [[danganronpa:_trigger_happy_havoc]] centered on Ryoko Otonashi and Yasuke Matsuda.",
        "[h2]Related tags[/h2]", "* [[danganronpa_(series)]]", "* [[enoshima_junko]]",
    ]),
    "super_danganronpa_2.5:_komaeda_nagito_to_sekai_no_hakaimono": ("copyright", [
        "[b]Super Danganronpa 2.5: Nagito Komaeda and the World Vanquisher[/b] is an original video animation centered on [[komaeda_nagito]]. It was released with a limited edition of Danganronpa V3 and connects the events of [[danganronpa_2:_goodbye_despair]] with [[danganronpa_3_(anime)]].",
        "[h2]Related tags[/h2]", "* [[danganronpa_(series)]]", "* [[komaeda_nagito]]", "* [[hinata_hajime]]", "* [[nanami_chiaki]]",
    ]),
    "danganronpa:_trigger_happy_havoc_if": ("copyright", [
        "[b]Danganronpa IF[/b] is an official alternate-story light novel unlocked in Danganronpa 2 after completing the game. It explores a non-canonical branch of [[danganronpa:_trigger_happy_havoc]] centered strongly on [[ikusaba_mukuro]].",
        "[h2]Related tags[/h2]", "* [[danganronpa_(series)]]", "* [[ikusaba_mukuro]]", "* [[naegi_makoto]]", "* [[enoshima_junko]]",
    ]),
    "danganronpa_kirigiri": ("copyright", [
        "[b]Danganronpa Kirigiri[/b] is a prequel light-novel series about Kyoko Kirigiri's earlier detective cases, written by Takekuni Kitayama and illustrated by Rui Komatsuzaki.",
        "[h2]Related tags[/h2]", "* [[danganronpa_(series)]]", "* [[kirigiri_kyouko]]",
    ]),
    "danganronpa:_togami": ("copyright", [
        "[b]Danganronpa: Togami[/b] is a three-volume spin-off light-novel series centered on Byakuya Togami, written by Yuya Sato with illustrations by Yun Koga.",
        "[h2]Related tags[/h2]", "* [[danganronpa_(series)]]", "* [[togami_byakuya]]",
    ]),
    "danganronpa_gaiden:_killer_killer": ("copyright", [
        "[b]Danganronpa Gaiden: Killer Killer[/b] is a manga spin-off connected to [[danganronpa_3_(anime)]]. It follows Future Foundation investigators Misaki Asano and Takumi Hijirihara while investigating serial killings.",
        "[h2]Related tags[/h2]", "* [[danganronpa_(series)]]", "* [[asano_misaki_(danganronpa)]]",
    ]),
}


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def main() -> None:
    con = sqlite3.connect(DB)
    rows = {name: (count, category) for name, count, category in con.execute("SELECT name, post_count, category FROM tags")}
    OUT.mkdir(parents=True, exist_ok=True)
    missing_roots = [tag for tag in PAGES if tag not in rows]
    if missing_roots:
        raise SystemExit(f"Missing root tags: {missing_roots}")
    for tag, (template, lines) in PAGES.items():
        payload = {"tag": tag, "template": template, "source": compact("\n".join(lines)), "updated_at": datetime.now(timezone.utc).isoformat()}
        safe = tag.replace(":", "").replace("/", "_") + ".json"
        path = OUT / safe
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Wrote", path)


if __name__ == "__main__":
    main()
