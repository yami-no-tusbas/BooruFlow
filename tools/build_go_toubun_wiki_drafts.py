from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/databases/g_tags_260810.db"
OUT = ROOT / "var/wiki_drafts/Go-Toubun no Hanayome"
COPYRIGHT = "go-toubun_no_hanayome"

CHARACTERS = {
    "nakano_ichika": "Ichika Nakano is the eldest of the Nakano quintuplets. She presents herself as a composed and teasing older sister while secretly pursuing a career as an actress, which increasingly competes with her schoolwork and feelings for Fuutarou.",
    "nakano_nino": "Nino Nakano is the second of the Nakano quintuplets. Fashion-conscious, strong-willed and an excellent cook, she is initially the most openly hostile to Fuutarou's tutoring but becomes unusually direct once her feelings change.",
    "nakano_miku": "Miku Nakano is the third of the Nakano quintuplets. Quiet and reserved, she is fascinated by commanders from Japan's Sengoku period and is the first sister to openly cooperate with Fuutarou's lessons and recognize her feelings for him.",
    "nakano_yotsuba": "Yotsuba Nakano is the fourth of the Nakano quintuplets. Energetic, athletic and immediately friendly toward Fuutarou, she habitually puts other people's needs ahead of her own. She is commonly identified by her green ribbon shaped like rabbit ears.",
    "nakano_itsuki": "Itsuki Nakano is the fifth of the Nakano quintuplets. Serious, diligent and fond of food, she frequently argues with Fuutarou despite sharing his concern for the sisters' education. She hopes to follow her mother's path as a teacher.",
    "uesugi_fuutarou": "Fuutarou Uesugi is the academically gifted but socially blunt protagonist. Because his family is in debt, he accepts a well-paid tutoring job and discovers that his five reluctant students are identical quintuplet sisters from his school.",
    "uesugi_raiha": "Raiha Uesugi is Fuutarou's cheerful younger sister. She helps manage the Uesugi household and quickly befriends the Nakano sisters, who are charmed by her warmth and maturity.",
    "uesugi_isanari": "Isanari Uesugi is Fuutarou and Raiha's easygoing father. His friendship with Maruo Nakano helps create the tutoring arrangement between Fuutarou and the quintuplets.",
    "nakano_maruo": "Maruo Nakano is the quintuplets' wealthy stepfather and legal guardian. A physician and former student of their mother Rena, he hires Fuutarou to tutor the sisters but remains emotionally distant and highly protective.",
    "nakano_rena": "Rena Nakano is the deceased mother of the quintuplets and a former teacher. Her principles, death and relationship with Maruo strongly influence the sisters' ambitions and family bonds.",
    "takebayashi_(go-toubun_no_hanayome)": "Takebayashi is Fuutarou's childhood classmate and one of the students who helped him improve academically. Her later reunion with him causes the quintuplets to reflect on their relationship with their tutor.",
    "uesugi_fuutarou's_mother": "Fuutarou and Raiha's deceased mother is a minor background character whose absence forms part of the Uesugi family's history. Use this tag only when she is specifically depicted rather than for an unidentified mother figure.",
    "otori_(go-toubun_no_hanayome)": "Otori is a minor supporting character from The Quintessential Quintuplets. The dedicated Gelbooru tag should be used only when this named character is identifiable.",
    "yamada_(go-toubun_no_hanayome)": "Yamada is a minor supporting character from The Quintessential Quintuplets. The dedicated Gelbooru tag should be used only when this named character is identifiable.",
    "eba_(go-toubun_no_hanayome)": "Eba is a minor supporting character from The Quintessential Quintuplets. The dedicated Gelbooru tag should be used only when this named character is identifiable.",
}


def compact(source: str) -> str:
    source = re.sub(r"\n+(\[h[1-6]\])", r"\1", source)
    return re.sub(r"(\[/h[1-6]\])\n+", r"\1", source)


def payload(tag: str, template: str, source: str) -> dict[str, str]:
    return {
        "tag": tag,
        "template": template,
        "source": compact(source),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def uploaded_names() -> set[str]:
    return {
        path.name.casefold()
        for folder in OUT.rglob("uploaded") if folder.is_dir()
        for path in folder.rglob("*.json")
    }


def write(tag: str, template: str, source: str, folder: Path, uploaded: set[str]) -> bool:
    filename = f"{tag.replace('/', '_')}.json"
    if filename.casefold() in uploaded:
        return False
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_text(
        json.dumps(payload(tag, template, source), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def series_source() -> str:
    return "\n".join([
        "[b]The Quintessential Quintuplets[/b] (Japanese: Go-Toubun no Hanayome) is a romantic-comedy manga written and illustrated by Negi Haruba. It was serialized in Weekly Shounen Magazine from 2017 to 2020 and received television-anime, film, game and animated-special adaptations.",
        "",
        "The story follows [[uesugi_fuutarou]], an academically gifted student from a financially struggling family who is hired to tutor the wealthy Nakano quintuplets. [[nakano_ichika]], [[nakano_nino]], [[nakano_miku]], [[nakano_yotsuba]] and [[nakano_itsuki]] initially dislike studying and distrust their new tutor, but their academic progress and growing relationships with him form the central story. A framing device reveals that Fuutarou will eventually marry one of the sisters.",
        "[h2]Principal characters[/h2]",
        "* [[uesugi_fuutarou]]",
        "* [[nakano_ichika]]", "* [[nakano_nino]]", "* [[nakano_miku]]", "* [[nakano_yotsuba]]", "* [[nakano_itsuki]]",
        "[h2]Anime adaptations[/h2]",
        "The first television season aired in 2019. The Quintessential Quintuplets 2 followed in 2021, and the 2022 film concluded the principal adaptation. Later television specials adapt manga material omitted from the earlier series and additional post-story material.",
        "[h2]Character index[/h2]",
        "See [[list_of_go-toubun_no_hanayome_characters]] for the locally validated supporting cast.",
        "[h2]Tagging notes[/h2]",
        "Use [[go-toubun_no_hanayome]] for material from the manga or any of its direct animated and game adaptations unless Gelbooru has a more specific established copyright tag. Tag every depicted sister individually; their similar faces do not make the quintuplets interchangeable tags.",
        "[h2]External links[/h2]",
        "* Official anime portal: https://www.tbs.co.jp/anime/5hanayome/",
        "* Official franchise website: https://go-toubun.com/",
    ])


def styled(tag: str, count: int) -> str:
    link = f"[[{tag}]]"
    if count >= 10_000: return f"[b]{link}[/b]"
    if count >= 1_000: return f"[i]{link}[/i]"
    if count < 25: return f"{link}**"
    if count < 50: return f"{link}*"
    return link


def list_source(counts: dict[str, int]) -> str:
    lines = [
        "This page lists the established Gelbooru character tags for [[go-toubun_no_hanayome]].",
        "",
        "[b]Legend:[/b] [b]bold[/b] = 10,000+ posts; [i]italic[/i] = 1,000+ posts; * = fewer than 50 posts; ** = fewer than 25 posts.",
        "[h2]Nakano quintuplets[/h2]",
    ]
    lines.extend(f"* {styled(tag, counts[tag])}" for tag in [
        "nakano_ichika", "nakano_nino", "nakano_miku", "nakano_yotsuba", "nakano_itsuki",
    ])
    lines += ["[h2]Uesugi family[/h2]"]
    lines.extend(f"* {styled(tag, counts[tag])}" for tag in ["uesugi_fuutarou", "uesugi_raiha", "uesugi_isanari"])
    lines += ["[h2]Nakano family and supporting cast[/h2]"]
    lines.extend(f"* {styled(tag, counts[tag])}" for tag in [
        "nakano_maruo", "nakano_rena", "takebayashi_(go-toubun_no_hanayome)",
        "uesugi_fuutarou's_mother", "otori_(go-toubun_no_hanayome)",
        "yamada_(go-toubun_no_hanayome)", "eba_(go-toubun_no_hanayome)",
    ])
    lines += [
        "[h2]Related pages[/h2]", "* [[go-toubun_no_hanayome]]",
        "[h2]External links[/h2]", "* Official anime portal: https://www.tbs.co.jp/anime/5hanayome/",
    ]
    return "\n".join(lines)


def character_source(tag: str, description: str) -> str:
    return "\n".join([
        "[b]Description:[/b]", description,
        "[h2]Copyright[/h2]", "* [[go-toubun_no_hanayome]]",
        "[h2]Tagging notes[/h2]", f"Use [[{tag}]] when this character is depicted. Do not substitute another quintuplet's tag merely because their facial features are similar.",
        "[h2]External links[/h2]", "* Official anime portal: https://www.tbs.co.jp/anime/5hanayome/",
    ])


def main() -> None:
    uploaded = uploaded_names()
    with sqlite3.connect(DB) as connection:
        rows = connection.execute(
            f"SELECT name, post_count, category FROM tags WHERE name IN ({','.join('?' for _ in CHARACTERS)})",
            list(CHARACTERS),
        ).fetchall()
        found = {name: (count, category) for name, count, category in rows}
        missing = sorted(set(CHARACTERS) - set(found))
        wrong_type = sorted(name for name, (_count, category) in found.items() if category != 4)
        copyright_row = connection.execute("SELECT category FROM tags WHERE name = ?", (COPYRIGHT,)).fetchone()
    if missing or wrong_type or copyright_row != (3,):
        raise SystemExit(f"Invalid local tags: missing={missing}, wrong_type={wrong_type}, copyright={copyright_row}")
    counts = {name: count for name, (count, _category) in found.items()}

    written = int(write(COPYRIGHT, "copyright", series_source(), OUT, uploaded))
    written += int(write("list_of_go-toubun_no_hanayome_characters", "general", list_source(counts), OUT, uploaded))
    for tag, description in CHARACTERS.items():
        written += int(write(tag, "character", character_source(tag, description), OUT / "characters", uploaded))
    print(f"Go-Toubun drafts written: {written}; characters: {len(CHARACTERS)}; uploaded exclusions: {len(uploaded)}")


if __name__ == "__main__":
    main()
