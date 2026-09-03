from __future__ import annotations

import csv
import sqlite3
from pathlib import Path


PROJECT = Path(__file__).absolute().parents[2]
DATABASE = PROJECT / "data" / "databases" / "g_tags_260810.db"
RESERVE = Path(r"D:\Réserve d'avatar v4")
ROOTS = (RESERVE / "Garçons (Gelbooru)", RESERVE / "Garçons (Gelbooru) s&l")
OUTPUT = PROJECT / "outputs" / "listes_garcons"
MIN_COUNT = 50


# Thèmes de phase 5. Un tag ne figure que dans un seul thème afin que les listes
# restent faciles à relire et à importer. ``direct`` signifie que le tag apporte
# lui-même un fort signal masculin ; ``mixed`` exige un indicateur de garçon.
THEMES: dict[str, dict[str, tuple[str, ...]]] = {
    "01_presentation": {
        "direct": ("male_focus", "trap", "otoko_no_ko", "crossdressing", "androgynous", "male_maid"),
        "mixed": (),
    },
    "02_apparence_et_pilosite": {
        "direct": ("dark-skinned_male", "muscular_male", "topless_male", "male_pubic_hair"),
        "mixed": ("facial_hair", "beard", "mustache", "body_hair", "chest_hair", "leg_hair", "armpit_hair"),
    },
    "03_vetements_masculins": {
        "direct": ("male_underwear", "male_swimwear"),
        "mixed": ("fundoshi", "swim_trunks", "briefs", "boxers", "jockstrap", "loincloth"),
    },
    "04_anatomie_et_exposition": {
        "direct": (
            "male_masturbation", "erection_under_clothes", "penis_peek", "testicle_peek",
            "penis_focus", "looking_at_penis",
        ),
        "mixed": (),
    },
    "05_objets_et_chastete": {
        "direct": (
            "chastity_cage", "flat_chastity_cage", "small_chastity_cage", "tube_chastity_cage",
            "padlocked_chastity_cage", "revealing_chastity_cage", "nub_chastity_cage",
            "cock_ring", "penis_ring", "penis_sheath", "prostate_massager",
        ),
        "mixed": (),
    },
    "06_stimulation_et_pratiques": {
        "direct": (
            "urethral_insertion", "sounding", "cock_and_ball_torture", "small_penis_humiliation",
            "prostate_milking", "prostate_massage", "prostate_orgasm",
        ),
        "mixed": (
            "pegging", "caressing_testicles", "fondling_testicles", "testicle_sucking",
            "sucking_testicles", "licking_testicle",
        ),
    },
    "07_soumission_et_scenarios": {
        "direct": (),
        "mixed": (
            "femdom", "humiliation", "public_use", "slave", "restrained", "gagged",
            "pet_play", "spanking", "leash", "body_writing",
        ),
    },
    "08_homo_et_bisexuel": {
        "direct": ("yaoi", "bara", "bisexual_(male)", "josou_seme"),
        "mixed": (),
    },
    "09_tenues_feminines_mixtes": {
        "direct": (),
        "mixed": ("maid", "high_heels", "lingerie", "panties", "dress", "skirt"),
    },
}


DIRECT = {
    # Présentation ou composition explicitement masculine.
    "male_focus": "centrage explicite sur un personnage masculin",
    "otoko_no_ko": "présentation masculine féminine canonique",
    "male_maid": "rôle et tenue explicitement masculins",
    "male_masturbation": "action explicitement masculine",
    "yaoi": "relation homme-homme sur Gelbooru",
    "trap": "branche historique de la galerie Garçons",
    "crossdressing": "fort potentiel pour la présentation recherchée",
    "androgynous": "fort potentiel de personnage masculin androgyne",
    # Objets et états anatomiques masculins.
    "chastity_cage": "objet porté sur l'anatomie masculine",
    "flat_chastity_cage": "variante de cage de chasteté",
    "small_chastity_cage": "variante de cage de chasteté",
    "tube_chastity_cage": "variante de cage de chasteté",
    "padlocked_chastity_cage": "variante de cage de chasteté",
    "revealing_chastity_cage": "variante de cage de chasteté",
    "nub_chastity_cage": "variante de cage de chasteté",
    "cock_ring": "accessoire anatomiquement masculin",
    "penis_ring": "accessoire anatomiquement masculin",
    "prostate_massager": "objet à fort potentiel masculin",
    "sounding": "pratique urétrale masculine dans ce contexte",
    "urethral_insertion": "pratique à fort potentiel masculin",
    "cock_and_ball_torture": "pratique anatomiquement masculine",
    "small_penis_humiliation": "humiliation explicitement masculine",
    "erection_under_clothes": "état anatomiquement masculin",
    "penis_peek": "exposition masculine",
    "testicle_peek": "exposition masculine",
    "penis_sheath": "accessoire anatomiquement masculin",
}


MIXED = {
    # Ces tags peuvent contenir des personnages féminins seuls ou avoir un autre centre visuel.
    "femdom": "exiger la présence d'un garçon sans exclure les filles",
    "pegging": "exiger la présence d'un garçon",
    "caressing_testicles": "l'action peut être centrée sur l'autre personnage",
    "fondling_testicles": "l'action peut être centrée sur l'autre personnage",
    "testicle_sucking": "l'action peut être centrée sur l'autre personnage",
    "sucking_testicles": "l'action peut être centrée sur l'autre personnage",
    "licking_testicle": "l'action peut être centrée sur l'autre personnage",
    "humiliation": "thème mixte",
    "public_use": "thème mixte",
    "slave": "rôle mixte",
    "restrained": "thème mixte",
    "gagged": "thème mixte",
    "pet_play": "thème mixte",
    "spanking": "thème mixte",
    "maid": "tenue ou rôle mixte",
    "high_heels": "vêtement mixte dans la base",
    "lingerie": "vêtement mixte dans la base",
    "panties": "vêtement mixte dans la base",
    "dress": "vêtement mixte dans la base",
    "skirt": "vêtement mixte dans la base",
    "collar": "accessoire mixte et non automatiquement BDSM",
    "leash": "accessoire ou pratique mixte",
    "body_writing": "thème mixte",
}


def load_counts() -> dict[str, tuple[int, int]]:
    connection = sqlite3.connect(f"file:{DATABASE.as_posix()}?mode=ro", uri=True)
    try:
        return {
            name: (post_count, category)
            for name, post_count, category in connection.execute(
                "SELECT name, post_count, category FROM tags"
            )
        }
    finally:
        connection.close()


def existing_folder_tokens() -> set[str]:
    found: set[str] = set()
    for root in ROOTS:
        for folder in (p for p in root.rglob("*") if p.is_dir()):
            found.update(folder.name.split())
    return found


def validated(source: dict[str, str], counts: dict[str, tuple[int, int]]) -> list[dict[str, object]]:
    existing = existing_folder_tokens()
    rows: list[dict[str, object]] = []
    for tag, reason in source.items():
        count, category = counts.get(tag, (0, -1))
        if category != 0 or count < MIN_COUNT:
            continue
        rows.append({
            "tag": tag,
            "post_count": count,
            "already_in_garcons": "yes" if tag in existing else "no",
            "reason": reason,
        })
    return sorted(rows, key=lambda row: (-int(row["post_count"]), str(row["tag"])))


def write_tsv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("tag", "post_count", "already_in_garcons", "reason"),
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    counts = load_counts()
    direct = validated(DIRECT, counts)
    mixed = validated(MIXED, counts)
    write_tsv(OUTPUT / "tags_directs.tsv", direct)
    write_tsv(OUTPUT / "tags_mixtes.tsv", mixed)

    (OUTPUT / "tags_directs.txt").write_text(
        "\n".join(str(row["tag"]) for row in direct) + "\n", encoding="utf-8"
    )
    (OUTPUT / "requetes_mixtes_1boy.txt").write_text(
        "\n".join(f"{row['tag']} 1boy" for row in mixed) + "\n", encoding="utf-8"
    )
    (OUTPUT / "requetes_mixtes_2boys.txt").write_text(
        "\n".join(f"{row['tag']} 2boys" for row in mixed) + "\n", encoding="utf-8"
    )
    new_direct = [row for row in direct if row["already_in_garcons"] == "no"]
    (OUTPUT / "nouveaux_tags_directs.txt").write_text(
        "\n".join(str(row["tag"]) for row in new_direct) + "\n", encoding="utf-8"
    )

    # Listes thématiques enrichies.
    thematic_rows: list[dict[str, object]] = []
    excluded_rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for theme, modes in THEMES.items():
        for mode, tags in modes.items():
            accepted: list[tuple[str, int]] = []
            for tag in tags:
                if tag in seen:
                    raise RuntimeError(f"Tag dupliqué entre thèmes: {tag}")
                seen.add(tag)
                count, category = counts.get(tag, (0, -1))
                if category != 0 or count < MIN_COUNT:
                    excluded_rows.append({
                        "theme": theme,
                        "tag": tag,
                        "post_count": count,
                        "category": category,
                        "reason": "wrong_category" if category != 0 else "below_threshold",
                    })
                    continue
                accepted.append((tag, count))
                thematic_rows.append({
                    "theme": theme,
                    "mode": mode,
                    "tag": tag,
                    "post_count": count,
                    "already_in_garcons": "yes" if tag in existing_folder_tokens() else "no",
                })
            accepted.sort(key=lambda item: (-item[1], item[0]))
            suffix = "directs" if mode == "direct" else "mixtes"
            (OUTPUT / f"{theme}_{suffix}.txt").write_text(
                "\n".join(tag for tag, _ in accepted) + ("\n" if accepted else ""), encoding="utf-8"
            )
            if mode == "mixed":
                (OUTPUT / f"{theme}_1boy.txt").write_text(
                    "\n".join(f"{tag} 1boy" for tag, _ in accepted) + ("\n" if accepted else ""),
                    encoding="utf-8",
                )
                (OUTPUT / f"{theme}_2boys.txt").write_text(
                    "\n".join(f"{tag} 2boys" for tag, _ in accepted) + ("\n" if accepted else ""),
                    encoding="utf-8",
                )

    with (OUTPUT / "INDEX_THEMATIQUE.tsv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("theme", "mode", "tag", "post_count", "already_in_garcons"),
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(thematic_rows)
    with (OUTPUT / "CANDIDATS_EXCLUS.tsv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("theme", "tag", "post_count", "category", "reason"),
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(excluded_rows)

    report = [
        "# Listes de recherche pour la galerie Garçons",
        "",
        f"- Base : `{DATABASE.name}`",
        f"- Seuil : tags généraux (`category=0`) avec au moins {MIN_COUNT} posts",
        f"- Tags directs validés : {len(direct)}",
        f"- Dont nouveaux dans l'arborescence Garçons : {len(new_direct)}",
        f"- Tags mixtes validés : {len(mixed)}",
        "",
        "Les requêtes `1boy` et `2boys` n'excluent aucun tag féminin : elles conservent donc les",
        "scènes hétérosexuelles et homosexuelles. Les fichiers sont des listes de revue, pas",
        "des instructions d'ajout automatique aux moniteurs.",
        "",
        "`condom_belt` n'est pas classé comme objet masculin : il reste un accessoire mixte déjà surveillé.",
        "Les tags de catégorie obsolète 6 et ceux sous le seuil sont exclus.",
        "",
        "## Index thématique enrichi",
        "",
        f"- Thèmes : {len(THEMES)}",
        f"- Entrées validées : {len(thematic_rows)}",
        f"- Candidats exclus et documentés : {len(excluded_rows)}",
        "- Chaque tag n'apparaît que dans un seul thème.",
        "- Pour chaque thème mixte, deux variantes sont fournies : `1boy` et `2boys`.",
    ]
    (OUTPUT / "README.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print({
        "direct": len(direct), "new_direct": len(new_direct), "mixed": len(mixed),
        "themes": len(THEMES), "thematic_entries": len(thematic_rows),
        "excluded": len(excluded_rows), "output": str(OUTPUT),
    })


if __name__ == "__main__":
    main()
