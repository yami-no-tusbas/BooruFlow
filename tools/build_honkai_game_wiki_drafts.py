from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "databases" / "g_tags_260810.db"
OUT = ROOT / "var" / "wiki_drafts"


PAGES: dict[str, list[str]] = {
    "FlyMe2theMoon": [
        "[b]FlyMe2theMoon[/b] is a 2011 iOS puzzle and action game created by the team that would establish [[mihoyo]]. It was the team's first released game and predates the better-known Honkai titles.",
        "",
        "The player guides Kiana Kaslana through stages by controlling her flight and collecting stars. This early Kiana is the conceptual predecessor of later incarnations rather than the same character in the continuity of [[honkai_gakuen]] or [[honkai_impact_3rd]].",
        "[h2]Tagging notes[/h2]",
        "* Use this copyright for material specifically depicting the 2011 game, its interface, stages or original version of Kiana.",
        "* Do not use it for later versions of [[kiana_kaslana]] solely because they share the name and design lineage.",
        "* The current local database contains [[flymetothemoon]] as a very small general tag, but no exact FlyMe2theMoon copyright tag. Check the depicted source before merging or replacing either spelling.",
        "[h2]Related tags[/h2]",
        "* [[mihoyo]]",
        "* [[honkai_(series)]]",
        "* [[kiana_kaslana]]",
        "* [[fly_me_to_the_moon]] - the song title; not the game.",
        "[h2]External sources[/h2]",
        "* miHoYo official company history: https://www.mihoyo.com/en/?page=about",
        "* Honkai franchise overview: https://en.wikipedia.org/wiki/Honkai",
    ],
    "houkai_gakuen": [
        "[b]Houkai Gakuen[/b], also known as Zombiegal Kawaii, is an early single-player mobile side-scrolling shooter developed by [[mihoyo]]. It preceded Houkai Gakuen 2 and established several visual and gameplay ideas later associated with the [[honkai_(series)]] franchise.",
        "",
        "This page concerns the original game. The similarly named live-service sequel was released in China as Houkai Gakuen 2, internationally as Guns GirlZ, and in Japan simply as Houkai Gakuen; use [[honkai_gakuen]] for that sequel and its larger cast.",
        "[h2]Main character[/h2]",
        "* [[kiana_kaslana]] - an early incarnation of Kiana; do not assume continuity with her Honkai Impact 3rd counterpart.",
        "[h2]Tagging notes[/h2]",
        "* Use [[houkai_gakuen]] for material identifiable as the original game.",
        "* Use [[honkai_gakuen]] for Houkai Gakuen 2 / Guns GirlZ material, especially its later stories and game-exclusive characters.",
        "* Because English and Japanese titles are easily confused, confirm the source, date or character design before changing an existing copyright tag.",
        "[h2]See also[/h2]",
        "* [[honkai_gakuen]]",
        "* [[honkai_impact_3rd]]",
        "* [[honkai_(series)]]",
        "[h2]External sources[/h2]",
        "* miHoYo official company page: https://www.mihoyo.com/en/?page=about",
        "* Honkai franchise overview: https://en.wikipedia.org/wiki/Honkai",
    ],
    "honkai_gakuen": [
        "[b]Honkai Gakuen[/b] is the Gelbooru copyright used primarily for [i]Houkai Gakuen 2[/i], a 2D side-scrolling action role-playing game developed by [[mihoyo]]. It was published internationally as [i]Guns GirlZ[/i] and in Japan as [i]Houkai Gakuen[/i].",
        "",
        "The game follows Kiana Kaslana and her companions through multiple large story arcs involving the Honkai. It reuses ideas from the original [[houkai_gakuen]] but is a separate game, and its continuity is also separate from [[honkai_impact_3rd]].",
        "[h2]Main characters[/h2]",
        "* [[kiana_kaslana]]",
        "* [[raiden_mei]]",
        "* [[bronya_zaychik]]",
        "* [[theresa_apocalypse_(honkai_gakuen)]]",
        "* [[sin_mal]]",
        "* [[houraiji_kyuushou]]",
        "* [[jyahnar_(honkai_gakuen)]]",
        "* [[femirins_(honkai_gakuen)]]",
        "* [[chloe_(honkai_gakuen)]]",
        "* [[shion_(honkai_gakuen)]]",
        "* [[silver_(honkai_gakuen)]]",
        "* [[kaguya_(honkai_gakuen)]]",
        "[h2]Tagging notes[/h2]",
        "* Use the qualified character tags when the database provides them, especially for characters unique to this game.",
        "* Kiana, Mei and Bronya have unqualified Gelbooru character tags shared by a large body of Honkai artwork. Add this copyright to identify their Honkai Gakuen incarnations.",
        "* Do not replace this copyright with [[honkai_impact_3rd]] merely because the games share names, designs or concepts.",
        "[h2]See also[/h2]",
        "* [[houkai_gakuen]] - original game and alternate title spelling.",
        "* [[honkai_(series)]]",
        "[h2]External sources[/h2]",
        "* Official Japanese site: https://www.mihoyo.co.jp/",
        "* Official Google Play listing: https://play.google.com/store/apps/details?id=com.miHoYo.HSoDv2JPOriginalEx",
    ],
    "honkai_impact_3rd": [
        "[b]Honkai Impact 3rd[/b] is a 3D action role-playing game developed by [[mihoyo]] and a major entry in the [[honkai_(series)]] franchise. It follows Valkyries and their allies as they confront the Honkai and successive threats to human civilization.",
        "",
        "The game shares names, motifs and alternate incarnations with [[honkai_gakuen]], but it has its own continuity. Its story later expands beyond the original cast, including the Part 2 characters introduced around Mars and the Sea of Data.",
        "[h2]Principal cast[/h2]",
        "* [[kiana_kaslana]]",
        "* [[raiden_mei]]",
        "* [[bronya_zaychik]]",
        "* [[murata_himeko]]",
        "* [[theresa_apocalypse]]",
        "* [[fu_hua]]",
        "* [[seele_vollerei]]",
        "* [[durandal_(honkai_impact)]]",
        "* [[rita_rossweisse]]",
        "* [[kallen_kaslana]]",
        "* [[otto_apocalypse]]",
        "* [[kevin_kaslana]]",
        "[h2]Flame-Chasers and related characters[/h2]",
        "* [[elysia_(honkai_impact)]]",
        "* [[mobius_(honkai_impact)]]",
        "* [[eden_(honkai_impact)]]",
        "* [[kalpas_(honkai_impact)]]",
        "* [[kosma]]",
        "* [[su_(honkai_impact)]]",
        "* [[sakura_(honkai_impact)]]",
        "[h2]Part 2 characters[/h2]",
        "* [[senadina]]",
        "* [[coralie_6626_planck]]",
        "* [[erdos_helia]]",
        "* [[thelema_nutriscu]]",
        "* [[songque]]",
        "* [[lantern_(honkai_impact)]]",
        "* [[vita_(honkai_impact)]]",
        "[h2]Recurring forms and terminology[/h2]",
        "* [[herrscher_of_sentience]]",
        "* [[raiden_mei_(herrscher_of_thunder)]]",
        "* [[kiana_kaslana_(herrscher_of_finality)]]",
        "* [[kiana_kaslana_(herrscher_of_flamescion)]]",
        "* [[bronya_zaychik_(herrscher_of_reason)]]",
        "* [[seele_vollerei_(herrscher_of_rebirth)]]",
        "[h2]Tagging notes[/h2]",
        "* Add this copyright to Honkai Impact 3rd characters, battlesuits, events and official promotional material.",
        "* A battlesuit or Herrscher form supplements the base character tag; it does not replace the character or copyright.",
        "* Do not use [[honkai:_star_rail]] for alternate Star Rail incarnations appearing only by resemblance or shared name.",
        "[h2]External sources[/h2]",
        "* Official site: https://honkaiimpact3.hoyoverse.com/",
        "* Official HoYoverse support: https://support.hoyoverse.com/hc/en-us/categories/48104933140889-Honkai-Impact-3rd",
    ],
    "honkai:_star_rail": [
        "[b]Honkai: Star Rail[/b] is a turn-based role-playing game developed by [[mihoyo]] and an entry in the [[honkai_(series)]] franchise. The player follows the Trailblazer and the Astral Express crew while travelling between worlds affected by Stellaron crises and the influence of the Aeons and their Paths.",
        "",
        "The game contains alternate incarnations and counterparts of characters from other Honkai works, but its worlds and story form a distinct continuity. Use Star Rail-qualified character tags whenever available.",
        "[h2]Astral Express[/h2]",
        "* [[trailblazer_(honkai:_star_rail)]]",
        "* [[stelle_(honkai:_star_rail)]]",
        "* [[caelus_(honkai:_star_rail)]]",
        "* [[march_7th_(honkai:_star_rail)]]",
        "* [[dan_heng_(honkai:_star_rail)]]",
        "* [[himeko_(honkai:_star_rail)]]",
        "* [[welt_yang]]",
        "* [[pom-pom_(honkai:_star_rail)]]",
        "[h2]Other notable characters[/h2]",
        "* [[kafka_(honkai:_star_rail)]]",
        "* [[silver_wolf_(honkai:_star_rail)]]",
        "* [[firefly_(honkai:_star_rail)]]",
        "* [[acheron_(honkai:_star_rail)]]",
        "* [[bronya_rand]]",
        "* [[seele_(honkai:_star_rail)]]",
        "* [[jing_yuan]]",
        "* [[blade_(honkai:_star_rail)]]",
        "* [[ruan_mei_(honkai:_star_rail)]]",
        "* [[robin_(honkai:_star_rail)]]",
        "* [[sparkle_(honkai:_star_rail)]]",
        "* [[phainon_(honkai:_star_rail)]]",
        "* [[cyrene_(honkai:_star_rail)]]",
        "[h2]Tagging notes[/h2]",
        "* Use [[trailblazer_(honkai:_star_rail)]] for the player role and add [[stelle_(honkai:_star_rail)]] or [[caelus_(honkai:_star_rail)]] when the selected body is identifiable.",
        "* Keep Star Rail counterparts distinct from similarly named Honkai Impact 3rd characters, such as [[himeko_(honkai:_star_rail)]] versus [[murata_himeko]].",
        "* Add the specific form or costume tag alongside the base character when one exists.",
        "[h2]External sources[/h2]",
        "* Official site: https://hsr.hoyoverse.com/",
        "* Official HoYoverse support: https://support.hoyoverse.com/hc/en-us/categories/48105027238681-Honkai-Star-Rail",
    ],
    "honkai:_nexus_anima": [
        "[b]Honkai: Nexus Anima[/b] is a creature-collection adventure game in development by [[mihoyo]] and part of the [[honkai_(series)]] franchise. Players take the role of an Animaster and travel with beings called Anima.",
        "",
        "Information and names may change while the game remains in testing. Prefer the most specific qualified tags recorded for official promotional material and test content.",
        "[h2]Characters[/h2]",
        "* [[female_animaster_(honkai:_nexus_anima)]]",
        "* [[kiana_kaslana_(honkai:_nexus_anima)]]",
        "* [[nanafey_(honkai:_nexus_anima)]]",
        "* [[parayaya_(honkai:_nexus_anima)]]",
        "* [[maple_manybell_(honkai:_nexus_anima)]]",
        "* [[kumyo_kyo_(honkai:_nexus_anima)]]",
        "* [[hua_(honkai:_nexus_anima)]]",
        "[h2]Anima[/h2]",
        "* [[puddlipup_(honkai:_nexus_anima)]]",
        "* [[cublade_(honkai:_nexus_anima)]]",
        "* [[taileep_(honkai:_nexus_anima)]]",
        "* [[chimaura_(honkai:_nexus_anima)]]",
        "* [[prabhas_(honkai:_nexus_anima)]]",
        "* [[nomnom_(honkai:_nexus_anima)]]",
        "* [[mushgloomini_(honkai:_nexus_anima)]]",
        "* [[donubi_(honkai:_nexus_anima)]]",
        "* [[bobabirb_(honkai:_nexus_anima)]]",
        "[h2]Tagging notes[/h2]",
        "* Use Nexus Anima-qualified tags to distinguish its incarnations of familiar Honkai characters.",
        "* Do not substitute [[kiana_kaslana]] for [[kiana_kaslana_(honkai:_nexus_anima)]].",
        "* Treat provisional names from tests cautiously and update this page when official spellings change.",
        "[h2]External sources[/h2]",
        "* Official site: https://hna.hoyoverse.com/",
        "* Official HoYoLAB channel: https://www.hoyolab.com/circles/46/39/official?page_type=39&page_sort=official",
    ],
}

SAFE_FILENAMES = {
    "honkai:_star_rail": "honkai_star_rail.json",
    "honkai:_nexus_anima": "honkai_nexus_anima.json",
}


def compact_headings(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def main() -> None:
    con = sqlite3.connect(DB)
    rows = {
        name: (post_count, category)
        for name, post_count, category in con.execute(
            "SELECT name, post_count, category FROM tags"
        )
    }
    OUT.mkdir(parents=True, exist_ok=True)

    for tag, lines in PAGES.items():
        source = compact_headings("\n".join(lines))
        payload = {
            "tag": tag,
            "template": "copyright",
            "source": source,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        path = OUT / SAFE_FILENAMES.get(tag, f"{tag}.json")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {path}")

    expected_copyrights = [
        "honkai_gakuen",
        "houkai_gakuen",
        "honkai_impact_3rd",
        "honkai:_star_rail",
        "honkai:_nexus_anima",
    ]
    invalid = [(tag, rows.get(tag)) for tag in expected_copyrights if not rows.get(tag) or rows[tag][1] != 3]
    if invalid:
        raise SystemExit(f"Invalid copyright tags: {invalid}")


if __name__ == "__main__":
    main()
