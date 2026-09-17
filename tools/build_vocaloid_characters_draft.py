from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


DATABASE = Path(r"D:\python\artist_by_tag\data\databases\g_tags_260810.db")
DRAFT = Path(r"D:\python\artist_by_tag\var\wiki_drafts\List_of_VOCALOID_characters.json")

# A character is filed under the first VOCALOID engine generation with which it
# was commercially introduced. Later voicebanks remain below that character.
GENERATIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("VOCALOID 1 era", [
        ("leon_(vocaloid)", "LEON"), ("lola_(vocaloid)", "LOLA"),
        ("miriam_(vocaloid)", "MIRIAM"), ("meiko_(vocaloid)", "MEIKO"),
        ("kaito_(vocaloid)", "KAITO"),
    ]),
    ("VOCALOID 2 era", [
        ("sweet_ann", "Sweet ANN"), ("hatsune_miku", "Hatsune Miku"),
        ("kagamine_rin", "Kagamine Rin"), ("kagamine_len", "Kagamine Len"),
        ("prima", "Prima"), ("kamui_gakupo", "Gackpoid / Kamui Gakupo"),
        ("megurine_luka", "Megurine Luka"), ("gumi", "Megpoid / GUMI"),
        ("sonika_(vocaloid2)", "SONiKA"),
        ("sf-a2_miki", "SF-A2 miki"), ("kaai_yuki", "Kaai Yuki"),
        ("hiyama_kiyoteru", "Hiyama Kiyoteru"), ("big_al", "BIG AL"),
        ("tonio", "Tonio"), ("lily_(vocaloid)", "Lily"),
        ("vy1", "VY1"), ("ryuuto_(vocaloid)", "Gachapoid / Ryuto"),
        ("nekomura_iroha", "Nekomura Iroha"), ("utatane_piko", "Utatane Piko"),
        ("vy2", "VY2"),
    ]),
    ("VOCALOID 3 era", [
        ("mew_(vocaloid)", "Mew"), ("seeu", "SeeU"),
        ("tone_rion", "Tone Rion"), ("oliver_(vocaloid)", "OLIVER"),
        ("cul", "CUL"), ("yuzuki_yukari", "Yuzuki Yukari"),
        ("bruno_(vocaloid)", "Bruno"), ("clara_(vocaloid)", "Clara"),
        ("ia_(vocaloid)", "IA"), ("aoki_lapis", "Aoki Lapis"),
        ("luo_tianyi", "Luo Tianyi"), ("galaco", "galaco"),
        ("mayu_(vocaloid)", "MAYU"), ("avanna", "AVANNA"),
        ("kyo_(vocaloid)", "KYO / ZOLA PROJECT"),
        ("yuu_(vocaloid)", "YUU / ZOLA PROJECT"),
        ("wil_(vocaloid)", "WIL / ZOLA PROJECT"), ("yanhe", "YANHE"),
        ("yohioloid", "YOHIOloid"), ("maika_(vocaloid)", "MAIKA"),
        ("merli_(vocaloid)", "Merli"), ("macne_nana", "Macne Nana"),
        ("kokone_(vocaloid)", "kokone"), ("anon_(vocaloid)", "Anon"),
        ("kanon_(vocaloid)", "Kanon"), ("flower_(vocaloid)", "flower"),
        ("tohoku_zunko", "Tohoku Zunko"), ("chika_(vocaloid)", "Chika"),
        ("rana_(vocaloid)", "Rana"), ("xin_hua", "Xin Hua"),
    ]),
    ("VOCALOID 4 era", [
        ("cyber_diva", "CYBER DIVA"), ("yuezheng_ling", "Yuezheng Ling"),
        ("ruby_(vocaloid)", "RUBY"), ("dex_(vocaloid)", "DEX"),
        ("daina_(vocaloid)", "DAINA"), ("sachiko_(vocaloid)", "Sachiko"),
        ("arsloid", "ARSLOID"), ("unity-chan", "Unity-chan!"),
        ("fukase", "Fukase"), ("cyber_songman", "CYBER SONGMAN"),
        ("otomachi_una", "Otomachi Una"), ("uni_(vocaloid)", "UNI"),
        ("yumemi_nemu_(vocaloid)", "Yumemi Nemu"),
        ("azuki_(vocaloid4)", "AZUKI"), ("matcha_(vocaloid4)", "MATCHA"),
        ("yuezheng_longya", "Yuezheng Longya"), ("lumi_(vocaloid)", "LUMi"),
    ]),
    ("VOCALOID 5 era", [
        ("amy_(vocaloid)", "Amy"), ("chris_(vocaloid)", "Chris"),
        ("kaori_(vocaloid)", "Kaori"), ("ken_(vocaloid)", "Ken"),
        ("haruno_sora", "Haruno Sora"), ("meika_hime", "Meika Hime"),
        ("meika_mikoto", "Meika Mikoto"),
    ]),
    ("VOCALOID 6 era", [
        ("po-uta", "Po-uta"), ("fuiro", "Fuiro"),
        ("hibiki_koto", "Hibiki Koto"), ("shiki_rowen", "Shiki Rowen"),
        ("otobe_sapphire", "Otobe Sapphire"),
    ]),
]

FAN_DERIVATIVES = {
    "akaito", "kaiko_(vocaloid)", "meito_(vocaloid)", "hatsune_mikuo",
    "kagamine_rinto", "kagamine_lenka", "sakine_meiko", "taito_(vocaloid)",
    "kikaito", "sakerune_meiko", "neko_hatsune_miku", "yuki_rin_(vocaloid)",
}


def decorate(tag: str, count: int) -> str:
    link = f"[[{tag}]]"
    if count >= 10_000:
        return f"[b]{link}[/b]"
    if count >= 1_000:
        return f"[i]{link}[/i]"
    if count < 25:
        return link + "**"
    if count < 50:
        return link + "*"
    return link


def variant_owner(name: str, roots: set[str]) -> str | None:
    for root in ("hatsune_miku", "kagamine_rin", "kagamine_len", "megurine_luka"):
        if name.startswith(root + "_") or name.startswith(root + "("):
            return root
    if name.startswith(("magical_mirai_kaito", "symphony_kaito")) or re.match(
        r"(?:25-ji|vivid_bad_squad|leo/need|more_more_jump!|wonderlands_x_showtime)_kaito", name
    ):
        return "kaito_(vocaloid)"
    if name.startswith(("magical_mirai_meiko", "symphony_meiko")) or re.match(
        r"(?:25-ji|vivid_bad_squad|leo/need|more_more_jump!|wonderlands_x_showtime)_meiko", name
    ):
        return "meiko_(vocaloid)"
    if name.startswith("kaito_(") and "_(vocaloid)" in name:
        return "kaito_(vocaloid)"
    if name.startswith("meiko_(") and "_(vocaloid)" in name:
        return "meiko_(vocaloid)"
    if re.fullmatch(r"kaito_\(vocaloid[1-6]\)", name):
        return "kaito_(vocaloid)"
    if re.fullmatch(r"meiko_\(vocaloid[1-6]\)", name):
        return "meiko_(vocaloid)"
    aliases = {
        "megpoid_": "gumi", "gumi_": "gumi", "gackpoid_": "kamui_gakupo",
        "luo_tianyi_": "luo_tianyi", "yuzuki_yukari_": "yuzuki_yukari",
        "otomachi_una_": "otomachi_una", "nekomura_iroha_": "nekomura_iroha",
        "sf-a2_miki_": "sf-a2_miki", "kaai_yuki_": "kaai_yuki",
        "xin_hua_": "xin_hua", "yanhe_": "yanhe", "yuezheng_ling_": "yuezheng_ling",
        "tone_rion_": "tone_rion", "flower_": "flower_(vocaloid)",
        "ia_": "ia_(vocaloid)", "rana_": "rana_(vocaloid)",
    }
    for prefix, owner in aliases.items():
        if name.startswith(prefix) and owner in roots:
            return owner
    return None


def main() -> None:
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    rows = connection.execute(
        "SELECT name, post_count FROM tags WHERE category=4 AND post_count>=5"
    ).fetchall()
    connection.close()
    counts = dict(rows)

    existing_generations: list[tuple[str, list[tuple[str, str]]]] = []
    for generation, entries in GENERATIONS:
        existing = [(tag, label) for tag, label in entries if tag in counts]
        existing_generations.append((generation, existing))
    roots = {tag for _, entries in existing_generations for tag, _ in entries}

    candidates: set[str] = set()
    for name, _ in rows:
        if re.search(r"\(vocaloid(?:[1-6])?\)", name):
            candidates.add(name)
        if name.startswith(("hatsune_miku_", "kagamine_rin_", "kagamine_len_", "megurine_luka_")):
            candidates.add(name)
        if variant_owner(name, roots):
            candidates.add(name)
    candidates.update(tag for tag in FAN_DERIVATIVES if tag in counts)

    variants: dict[str, list[str]] = defaultdict(list)
    ungrouped: list[str] = []
    for tag in candidates - roots - FAN_DERIVATIVES:
        owner = variant_owner(tag, roots)
        if owner:
            variants[owner].append(tag)
        else:
            ungrouped.append(tag)

    lines = [
        "[b]About this list:[/b]",
        "This page lists VOCALOID characters and their Gelbooru character tags. Characters are grouped under the engine generation of their first release; later software versions remain below the original character.",
        "",
        "Song and music-video designs, Project DIVA modules, concert and anniversary costumes, Project SEKAI variants, and community derivatives are intentionally retained when they have a dedicated character tag.",
        "Only character tags (ttype/category 4) with at least 5 posts in the validated local Gelbooru database snapshot are included.",
        "",
        "[b]Popularity legend:[/b]",
        "* [b][[tag]][/b]: 10,000 images or more",
        "* [i][[tag]][/i]: 1,000 to 9,999 images",
        "* [[tag]]: 50 to 999 images",
        "* [[tag]]*: 25 to 49 images",
        "* [[tag]]**: 5 to 24 images",
        "",
        "The asterisks indicate a low image count, not a less valid character or design.",
        "",
    ]

    used: set[str] = set()
    for generation, entries in existing_generations:
        if not entries:
            continue
        lines.append(f"[h2]{generation}[/h2]")
        for root, label in entries:
            used.add(root)
            lines.append(f"[h3]{label}[/h3]")
            lines.append(f"* {decorate(root, counts[root])}")
            owned = sorted(variants.get(root, []), key=lambda tag: (-counts[tag], tag))
            software = [tag for tag in owned if re.search(r"vocaloid[1-6]|append|\(nt\)|\(act1\)", tag)]
            designs = [tag for tag in owned if tag not in software]
            if software:
                lines.append("[b]Software and voicebank versions:[/b]")
                lines.extend(f"** {decorate(tag, counts[tag])}" for tag in software)
            if designs:
                lines.append("[b]Song, game, concert and event designs:[/b]")
                lines.extend(f"** {decorate(tag, counts[tag])}" for tag in designs)
            lines.append("")
            used.update(owned)

    derivatives = sorted((FAN_DERIVATIVES & counts.keys()), key=lambda tag: (-counts[tag], tag))
    if derivatives:
        lines.append("[h2]Community derivatives and alternate characters[/h2]")
        lines.append("These characters are derived from, or closely associated with, established VOCALOID characters.")
        lines.extend(f"* {decorate(tag, counts[tag])}" for tag in derivatives)
        lines.append("")
        used.update(derivatives)

    remaining = sorted(set(ungrouped) - used, key=lambda tag: (-counts[tag], tag))
    if remaining:
        lines.append("[h2]Other VOCALOID-related character and design tags[/h2]")
        lines.append("These locally validated VOCALOID-qualified tags could not be assigned safely to one parent character from their tag name alone.")
        lines.extend(f"* {decorate(tag, counts[tag])}" for tag in remaining)
        lines.append("")
        used.update(remaining)

    lines.extend(["[h2]See also[/h2]", "* [[vocaloid]]", "* [[project_diva]]", "* [[project_sekai]]"])
    source = "\n".join(lines)
    # Gelbooru already inserts spacing after headings. Keeping the following
    # content on the same source line avoids an oversized visual gap.
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    source = re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)
    document = {
        "tag": "List_of_VOCALOID_characters",
        "template": "general",
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    DRAFT.parent.mkdir(parents=True, exist_ok=True)
    DRAFT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {DRAFT}")
    print(f"Included {len(used)} unique character tags")
    print(f"Main characters: {sum(len(entries) for _, entries in existing_generations)}")
    print(f"Parented variants: {sum(len(value) for value in variants.values())}")
    print(f"Community derivatives: {len(derivatives)}")
    print(f"Other qualified tags: {len(remaining)}")


if __name__ == "__main__":
    main()
