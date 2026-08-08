#!/usr/bin/env python3
"""
Classe les artistes Gelbooru associés à un ou plusieurs mots-clés.

Exemples :

Mode interactif — le script demandera les tags :
    python gelbooru_artistes_par_tags_blacklist.py g_tags_260712_blacklist.db \
        --pages 20 --min-hits 3 --min-artist-posts 100

Par défaut, le script lit blacklist.txt pour les artistes refusés et
ignore.txt pour les artistes ou recherches déjà examinés.

Mode classique — recherches fournies directement :
    python gelbooru_artistes_par_tags_interactif.py g_tags_260712_blacklist.db \
        "3d" "ai-generated" "sketch" \
        --pages 20 --min-hits 3 --min-artist-posts 100

Les identifiants peuvent être fournis avec :
    set GELBOORU_USER_ID=12345
    set GELBOORU_API_KEY=xxxxxxxx

Sous Linux :
    export GELBOORU_USER_ID=12345
    export GELBOORU_API_KEY=xxxxxxxx
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from booru_cache import BooruCache
from entity_types import ENTITY_TYPES, entity_type

API_URL = "https://gelbooru.com/index.php"
DEFAULT_USER_AGENT = "ArtistTagScanner/1.0 (personal Gelbooru library tool)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recherche les posts Gelbooru correspondant à des requêtes, "
            "puis classe les artistes trouvés."
        )
    )
    parser.add_argument(
        "db",
        type=Path,
        nargs="?",
        default=Path("g_tags_260712_blacklist.db"),
        help=(
            "Base SQLite contenant la table tags "
            "(défaut : g_tags_260712_blacklist.db)"
        ),
    )
    parser.add_argument(
        "requetes",
        nargs="*",
        help=(
            "Tags ou recherches Gelbooru. Sans valeur, le script ouvre un "
            "mode interactif permettant de coller une liste."
        ),
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=10,
        help="Nombre maximal de pages à analyser par requête (défaut : 10)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Nombre de posts demandé par page, entre 1 et 100 (défaut : 100)",
    )
    parser.add_argument(
        "--page-debut",
        type=int,
        default=1,
        help="Première page Gelbooru à analyser, numérotée à partir de 1.",
    )
    parser.add_argument(
        "--min-hits",
        type=int,
        default=2,
        help="Nombre minimal de posts correspondants pour retenir un artiste (défaut : 2)",
    )
    parser.add_argument(
        "--min-artist-posts",
        type=int,
        default=0,
        help="Nombre total minimal de posts de l'artiste dans la base (défaut : 0)",
    )
    parser.add_argument(
        "--max-artist-posts",
        type=int,
        default=0,
        help="Nombre total maximal de posts de l'artiste (0 = sans limite).",
    )
    parser.add_argument(
        "--min-match-percent",
        type=float,
        default=0,
        help="Pourcentage minimal du catalogue correspondant à la recherche.",
    )
    parser.add_argument(
        "--cache-days",
        type=int,
        default=30,
        help="Durée de validité des compteurs du cache SQLite (défaut : 30 jours).",
    )
    parser.add_argument(
        "--cache-check",
        choices=("none", "quick", "full"),
        default="none",
        help=(
            "Vérification finale du cache : none (défaut, instantané), "
            "quick ou full. Les deux contrôles lisent l'ensemble du cache."
        ),
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.75,
        help="Pause entre deux requêtes HTTP, en secondes (défaut : 0,75)",
    )
    parser.add_argument(
        "--counter-workers",
        type=int,
        default=10,
        help="Compteurs Gelbooru vérifiés en parallèle (défaut : 10).",
    )
    parser.add_argument(
        "--sortie",
        type=Path,
        default=Path("resultats_artistes_gelbooru"),
        help="Dossier de sortie",
    )
    parser.add_argument(
        "--blacklist",
        type=Path,
        default=Path("blacklist.txt"),
        help=(
            "Fichier contenant les tags déjà blacklistés "
            "(défaut : blacklist.txt)"
        ),
    )
    parser.add_argument(
        "--sans-blacklist",
        action="store_true",
        help="Désactive la lecture du fichier blacklist.",
    )
    parser.add_argument(
        "--ignore",
        type=Path,
        default=Path("ignore.txt"),
        help=(
            "Fichier contenant les artistes ou recherches déjà examinés "
            "(défaut : ignore.txt)"
        ),
    )
    parser.add_argument(
        "--sans-ignore",
        action="store_true",
        help="Désactive temporairement la lecture du fichier ignore.",
    )
    parser.add_argument(
        "--autoriser-requetes-ignorees",
        action="store_true",
        help=(
            "Continue à explorer les requêtes déjà notées dans ignore.txt, "
            "tout en excluant les artistes ignorés."
        ),
    )
    parser.add_argument(
        "--memoriser-requetes",
        action="store_true",
        help=(
            "Ajoute dans le fichier ignore les recherches terminées sans "
            "erreur réseau."
        ),
    )
    parser.add_argument(
        "--user-id",
        default=os.getenv("GELBOORU_USER_ID", "358070"),
        help="User ID Gelbooru, ou variable GELBOORU_USER_ID",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("GELBOORU_API_KEY", "358df02abf5a7fec138c3efac83840c93724e4973eff4ea52c395670abbba984"),
        help="Clé API Gelbooru, ou variable GELBOORU_API_KEY",
    )
    parser.add_argument("--entity-type", choices=ENTITY_TYPES, default="artists")
    return parser.parse_args()


def ask_queries() -> list[str]:
    """
    Demande des recherches Gelbooru dans le terminal.

    Une ligne correspond à une recherche indépendante. Une ligne peut contenir
    plusieurs tags, par exemple : comic multiple_panels

    La saisie se termine avec une ligne vide ou le mot FIN.
    """
    print()
    print("Colle les tags ou recherches Gelbooru ci-dessous.")
    print("Une ligne = une recherche indépendante.")
    print('Exemple de recherche combinée : comic multiple_panels')
    print("Termine avec une ligne vide ou en écrivant FIN.")
    print()

    queries: list[str] = []

    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break

        if not line or line.casefold() in {"fin", "end", "stop"}:
            break

        # Autorise aussi plusieurs recherches sur une ligne, séparées par ;
        parts = [part.strip() for part in line.split(";") if part.strip()]
        queries.extend(parts)

    # Supprime les doublons tout en gardant l'ordre d'origine.
    unique_queries: list[str] = []
    seen: set[str] = set()

    for query in queries:
        if query not in seen:
            seen.add(query)
            unique_queries.append(query)

    if not unique_queries:
        raise SystemExit("Aucune recherche fournie.")

    print()
    print(f"{len(unique_queries)} recherche(s) enregistrée(s) :")
    for query in unique_queries:
        print(f"  - {query}")

    return unique_queries


def load_artists(db_path: Path, category: int = 1) -> dict[str, int]:
    if not db_path.is_file():
        raise FileNotFoundError(f"Base introuvable : {db_path}")

    con = sqlite3.connect(db_path)
    try:
        table = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tags'"
        ).fetchone()
        if table is None:
            raise RuntimeError("La base ne contient pas de table 'tags'.")

        columns = {
            str(row[1]).casefold()
            for row in con.execute("PRAGMA table_info(tags)")
        }
        if not {"name", "post_count", "category"}.issubset(columns):
            raise RuntimeError(
                "La table 'tags' doit contenir name, post_count et category."
            )
        ambiguous_filter = " AND ambiguous = 0" if "ambiguous" in columns else ""
        rows = con.execute(
            "SELECT name, post_count FROM tags WHERE category = ?"
            + ambiguous_filter,
            (category,),
        ).fetchall()
    finally:
        con.close()

    artists: dict[str, int] = {}
    for name, post_count in rows:
        decoded = str(name)
        while (unescaped := html.unescape(decoded)) != decoded:
            decoded = unescaped
        artists[decoded] = max(artists.get(decoded, 0), int(post_count))
    return artists


def plural_tag_suggestions(
    db_path: Path, query: str
) -> list[tuple[str, str, int, int]]:
    """Repère un singulier presque vide dont le pluriel est très fréquent."""
    tokens = [
        token
        for token in normalize_query(query).split()
        if ":" not in token and not token.startswith("-") and not token.endswith("s")
    ]
    if not tokens:
        return []
    connection = sqlite3.connect(db_path)
    try:
        suggestions: list[tuple[str, str, int, int]] = []
        for token in tokens:
            plural = token + "s"
            counts = {
                str(name): int(post_count)
                for name, post_count in connection.execute(
                    "SELECT name,post_count FROM tags WHERE name IN (?,?)",
                    (token, plural),
                )
            }
            current = counts.get(token, 0)
            alternative = counts.get(plural, 0)
            if alternative >= 100 and alternative >= max(10, current * 10):
                suggestions.append((token, plural, current, alternative))
        return suggestions
    finally:
        connection.close()


def resolve_optional_file(path: Path) -> Path:
    """
    Cherche d'abord le fichier depuis le dossier courant, puis à côté du script.
    """
    if path.is_absolute() or path.is_file():
        return path

    script_directory = Path(sys.argv[0]).resolve().parent
    beside_script = script_directory / path

    if beside_script.is_file():
        return beside_script

    return path


def load_blacklisted_artists(
    blacklist_path: Path,
    artist_names: set[str],
) -> set[str]:
    """
    Extrait uniquement les artistes présents dans le fichier de blacklist.

    Le fichier peut aussi contenir des règles Grabber, des tags ordinaires ou
    des métatags comme « website:gelbooru.com ». Ils sont ignorés s'ils ne
    correspondent pas à un artiste connu de la base.

    Les lignes simples et les lignes composées sont acceptées :
        artiste_exemple
        website:gelbooru.com artiste_exemple
    """
    blacklist_path = resolve_optional_file(blacklist_path)

    if not blacklist_path.is_file():
        print(
            f"Blacklist introuvable : {blacklist_path} "
            "(aucun artiste existant ne sera exclu)."
        )
        return set()

    try:
        content = blacklist_path.read_text(
            encoding="utf-8-sig",
            errors="replace",
        )
    except OSError as exc:
        print(
            f"Impossible de lire la blacklist {blacklist_path}: {exc} "
            "(aucun artiste existant ne sera exclu).",
            file=sys.stderr,
        )
        return set()

    excluded: set[str] = set()

    for raw_line in content.splitlines():
        line = raw_line.strip()

        if not line or line.startswith(("#", "//")):
            continue

        # Une règle Grabber peut contenir plusieurs éléments séparés par
        # des espaces. On ne conserve que les éléments qui correspondent
        # exactement à un artiste de la base.
        for token in line.split():
            if token in artist_names:
                excluded.add(token)
                continue
            prefix, separator, value = token.partition(":")
            if (
                separator
                and prefix.casefold()
                in {
                    "artist", "artiste", "character", "personnage",
                    "copyright", "species", "espece",
                }
                and value in artist_names
            ):
                excluded.add(value)

    print(
        f"Blacklist chargée : {blacklist_path} — "
        f"{len(excluded)} entrée(s) déjà traitée(s) seront ignorée(s)."
    )
    return excluded


def normalize_query(query: str) -> str:
    """Normalise les espaces et la casse pour comparer deux recherches."""
    return " ".join(query.strip().split()).casefold()


def load_ignore_file(
    ignore_path: Path,
    artist_names: set[str],
) -> tuple[set[str], set[str], Path]:
    """
    Lit une liste séparée des éléments déjà examinés.

    Formats acceptés :
        artiste_deja_vu
        artist:artiste_deja_vu
        artiste:artiste_deja_vu
        query:3d
        recherche:comic multiple_panels

    Une ligne simple correspondant exactement à un artiste de la base est
    classée comme artiste. Toute autre ligne simple est considérée comme une
    recherche déjà traitée.
    """
    ignore_path = resolve_optional_file(ignore_path)

    if not ignore_path.is_file():
        print(
            f"Ignore list introuvable : {ignore_path} "
            "(aucun artiste ou tag déjà vu ne sera exclu)."
        )
        return set(), set(), ignore_path

    try:
        content = ignore_path.read_text(
            encoding="utf-8-sig",
            errors="replace",
        )
    except OSError as exc:
        print(
            f"Impossible de lire l'ignore list {ignore_path}: {exc} "
            "(aucune exclusion supplémentaire).",
            file=sys.stderr,
        )
        return set(), set(), ignore_path

    ignored_artists: set[str] = set()
    ignored_queries: set[str] = set()

    for raw_line in content.splitlines():
        line = raw_line.strip()

        if not line or line.startswith(("#", "//")):
            continue

        prefix, separator, value = line.partition(":")
        prefix_key = prefix.strip().casefold()
        value = value.strip()

        if separator and prefix_key in {
            "artist", "artiste", "character", "personnage",
            "copyright", "species", "espece",
        }:
            if value in artist_names:
                ignored_artists.add(value)
            else:
                print(
                    f"Artiste absent de la base dans ignore.txt : {value}",
                    file=sys.stderr,
                )
            continue

        if separator and prefix_key in {"query", "requete", "recherche", "tag"}:
            if value:
                ignored_queries.add(normalize_query(value))
            continue

        # Une ligne simple correspondant exactement à un artiste est un artiste.
        if line in artist_names:
            ignored_artists.add(line)
        else:
            # Sinon, c'est une recherche, y compris une combinaison de tags.
            ignored_queries.add(normalize_query(line))

    print(
        f"Ignore list chargée : {ignore_path} — "
        f"{len(ignored_artists)} entrée(s) et "
        f"{len(ignored_queries)} recherche(s) déjà traité(s)."
    )
    return ignored_artists, ignored_queries, ignore_path


def normalize_posts(data: Any) -> list[dict[str, Any]]:
    """Accepte les principales formes JSON rencontrées sur les DAPI Gelbooru."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if not isinstance(data, dict):
        return []

    posts = data.get("post", [])
    if isinstance(posts, dict):
        return [posts]
    if isinstance(posts, list):
        return [item for item in posts if isinstance(item, dict)]

    return []


def fetch_page(
    query: str,
    page: int,
    limit: int,
    user_id: str,
    api_key: str,
) -> tuple[list[dict[str, Any]], int]:
    params = {
        "page": "dapi",
        "s": "post",
        "q": "index",
        "json": "1",
        "tags": query,
        "limit": str(limit),
        "pid": str(page),
    }

    if user_id and api_key:
        params["user_id"] = user_id
        params["api_key"] = api_key

    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
        response_text = raw.decode(charset, errors="replace")
        data = json.loads(response_text)
    except json.JSONDecodeError as exc:
        preview = response_text[:300].replace("\n", " ")
        raise RuntimeError(
            f"Réponse non JSON pour la requête {query!r}, page {page}: {preview}"
        ) from exc

    total = 0
    if isinstance(data, dict):
        try:
            total = int(data.get("@attributes", {}).get("count", 0))
        except (TypeError, ValueError):
            total = 0
    return normalize_posts(data), total


def fetch_result_count(
    query: str,
    user_id: str,
    api_key: str,
) -> tuple[int, list[dict[str, Any]]]:
    posts, total = fetch_page(
        query=query,
        page=0,
        limit=1,
        user_id=user_id,
        api_key=api_key,
    )
    return total, posts


def fetch_counts_parallel(
    queries: dict[str, str],
    user_id: str,
    api_key: str,
    workers: int,
    progress_label: str,
) -> tuple[
    dict[str, tuple[int, list[dict[str, Any]]]],
    dict[str, Exception],
]:
    """Récupère des compteurs en parallèle, sans écrire dans SQLite."""
    results: dict[str, tuple[int, list[dict[str, Any]]]] = {}
    errors: dict[str, Exception] = {}
    if not queries:
        return results, errors
    with ThreadPoolExecutor(max_workers=min(workers, len(queries))) as executor:
        futures = {
            executor.submit(fetch_result_count, query, user_id, api_key): key
            for key, query in queries.items()
        }
        total = len(futures)
        for completed, future in enumerate(as_completed(futures), start=1):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as exc:
                errors[key] = exc
            print(
                f"Compteurs {progress_label} {completed}/{total} : {key}",
                flush=True,
            )
    return results, errors


def load_cumulative_progress(
    path: Path,
    queries: list[str],
    start_page: int,
) -> dict[str, Any]:
    """Charge uniquement une continuation exacte, sans risquer de compter deux fois."""
    if start_page <= 1 or not path.is_file():
        return {"version": 1, "queries": queries, "next_page": 1, "data": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"version": 1, "queries": queries, "next_page": 1, "data": {}}
    if (
        state.get("version") != 1
        or state.get("queries") != queries
        or state.get("next_page") != start_page
        or not isinstance(state.get("data"), dict)
    ):
        return {"version": 1, "queries": queries, "next_page": 1, "data": {}}
    return state


def save_cumulative_progress(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    args = parse_args()
    entity = entity_type(args.entity_type)
    if entity.gelbooru_category is None:
        raise SystemExit("Le mode species est disponible uniquement pour e621.")

    if not args.requetes:
        args.requetes = ask_queries()

    if args.pages < 1:
        raise SystemExit("--pages doit être supérieur ou égal à 1.")
    if args.page_debut < 1:
        raise SystemExit("--page-debut doit être supérieur ou égal à 1.")
    if not 1 <= args.limit <= 100:
        raise SystemExit("--limit doit être compris entre 1 et 100.")
    if args.min_hits < 1:
        raise SystemExit("--min-hits doit être supérieur ou égal à 1.")
    if args.max_artist_posts < 0:
        raise SystemExit("--max-artist-posts ne peut pas être négatif.")
    if not 0 <= args.min_match_percent <= 100:
        raise SystemExit("--min-match-percent doit être compris entre 0 et 100.")
    if args.cache_days < 0:
        raise SystemExit("--cache-days ne peut pas être négatif.")
    if args.delay < 0:
        raise SystemExit("--delay ne peut pas être négatif.")
    if args.counter_workers < 1:
        raise SystemExit("--counter-workers doit être supérieur ou égal à 1.")

    artists = load_artists(args.db, entity.gelbooru_category)
    all_artist_names = set(artists)

    if not all_artist_names:
        raise SystemExit(f"Aucune entrée {entity.label.lower()} trouvée dans la base.")

    blacklisted_artists: set[str] = set()
    if not args.sans_blacklist:
        blacklisted_artists = load_blacklisted_artists(
            args.blacklist,
            all_artist_names,
        )

    ignored_artists: set[str] = set()
    ignored_queries: set[str] = set()
    ignore_path = resolve_optional_file(args.ignore)

    if not args.sans_ignore:
        ignored_artists, ignored_queries, ignore_path = load_ignore_file(
            args.ignore,
            all_artist_names,
        )

    artist_names = all_artist_names - blacklisted_artists - ignored_artists

    if not artist_names:
        raise SystemExit(
            f"Toutes les entrées {entity.label.lower()} sont déjà blacklistées ou ignorées."
        )

    args.sortie.mkdir(parents=True, exist_ok=True)
    cache_db = BooruCache(
        args.sortie / f"cache_gelbooru_{entity.key}_v2.sqlite",
        f"gelbooru:{entity.key}",
    )
    if not args.sans_ignore:
        migrated_queries = cache_db.mark_processed_queries(ignored_queries)
        if migrated_queries:
            print(
                f"{migrated_queries} ancienne(s) recherche(s) de l'ignore list "
                "migrée(s) dans le cache SQLite."
            )
        ignored_queries.update(cache_db.processed_queries())

    initial_query_count = len(args.requetes)
    if not args.autoriser_requetes_ignorees:
        args.requetes = [
            query
            for query in args.requetes
            if normalize_query(query) not in ignored_queries
        ]
    skipped_query_count = initial_query_count - len(args.requetes)

    if skipped_query_count:
        print(
            f"{skipped_query_count} recherche(s) déjà mémorisée(s) "
            "ont été sautées."
        )

    if not args.requetes:
        cache_db.close()
        raise SystemExit(
            "Toutes les recherches fournies ont déjà été traitées "
            "selon l'historique."
        )

    print(
        f"{len(artist_names)} {entity.label.lower()} restent disponibles "
        f"sur {len(all_artist_names)} dans la base."
    )
    print(
        "Criteres : "
        f"min_hits={args.min_hits}, "
        f"posts_min={args.min_artist_posts}, "
        f"posts_max={args.max_artist_posts or 'aucun'}, "
        f"part_min={args.min_match_percent:g}%."
    )

    cumulative_path = args.sortie / f"progression_cumulative_{entity.key}_v2.json"
    cumulative_state = load_cumulative_progress(
        cumulative_path,
        args.requetes,
        args.page_debut,
    )
    cumulative_enabled = (
        args.page_debut > 1
        and cumulative_state.get("next_page") == args.page_debut
    )
    if cumulative_enabled:
        print(
            f"Cumul repris jusqu'à la page {args.page_debut - 1} "
            f"depuis {cumulative_path}."
        )
    elif args.page_debut > 1:
        print(
            "Aucun cumul compatible trouvé pour ce bloc : "
            "les compteurs repartent à zéro."
        )

    summary_rows: list[dict[str, Any]] = []
    combined_hits: Counter[str] = Counter()
    query_membership: dict[str, set[str]] = defaultdict(set)
    processed_queries: list[str] = []
    fetched_new_pages = False

    for query in args.requetes:
        print(f"\n=== {query} ===")
        counts: Counter[str] = Counter()
        scanned_posts = 0
        query_failed = False
        query_total_pages: int | None = None
        query_key = normalize_query(query)
        previous = cumulative_state.get("data", {}).get(query, {})
        previous_counts = Counter(
            {
                str(artist): int(hits)
                for artist, hits in previous.get("counts", {}).items()
            }
        )
        previous_scanned_posts = int(previous.get("scanned_posts", 0))
        cached_counts = Counter(cache_db.candidate_counts(query_key))
        use_cached_pages = (
            args.page_debut == 1
            and bool(cached_counts)
            and cache_db.has_query_pages(query_key, 1, args.pages)
        )
        if use_cached_pages:
            counts.update(cached_counts)
            scanned_posts = cache_db.query_post_count(query_key, 1, args.pages)
            cached_total = cache_db.query_total_count(query_key)
            if cached_total is not None:
                query_total_pages = math.ceil(cached_total / args.limit)
                print(
                    f"Total Gelbooru : {cached_total} résultat(s), "
                    f"{query_total_pages} page(s) (cache SQLite)."
                )
            print(
                f"{len(cached_counts)} candidat(s) repris depuis le cache ; "
                "reevaluation des criteres sans relire les pages deja vues."
            )

        for block_page in range(0 if use_cached_pages else args.pages):
            page = args.page_debut - 1 + block_page
            if query_total_pages is not None and page >= query_total_pages:
                break
            try:
                posts, query_total = fetch_page(
                    query=query,
                    page=page,
                    limit=args.limit,
                    user_id=args.user_id,
                    api_key=args.api_key,
                )
                if query_total_pages is None:
                    query_total_pages = math.ceil(query_total / args.limit)
                    print(
                        f"Total Gelbooru : {query_total} résultat(s), "
                        f"{query_total_pages} page(s)."
                    )
                    if query_total == 0:
                        for token, alternative, current, alternative_count in (
                            plural_tag_suggestions(args.db, query)
                        ):
                            corrected = " ".join(
                                alternative if part == token else part
                                for part in normalize_query(query).split()
                            )
                            print(
                                "Suggestion : le tag "
                                f"{token!r} ne compte que {current} post(s) dans "
                                f"la base locale, contre {alternative_count} pour "
                                f"{alternative!r}. Essaie : {corrected}",
                                flush=True,
                            )
                cache_db.store_posts(
                    posts,
                    normalize_query(query),
                    page + 1,
                    query_total,
                    artist_names,
                )
                fetched_new_pages = fetched_new_pages or bool(posts)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                print(
                    f"Erreur réseau sur {query!r}, page {page + 1}: {exc}",
                    file=sys.stderr,
                )
                query_failed = True
                break
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                query_failed = True
                break

            if not posts:
                break

            for post in posts:
                scanned_posts += 1
                raw_tags = post.get("tags", "")
                if not isinstance(raw_tags, str):
                    continue

                post_artists = artist_names.intersection(raw_tags.split())
                for artist in post_artists:
                    counts[artist] += 1

            print(
                f"Page Gelbooru {page + 1} "
                f"({block_page + 1}/{args.pages} du bloc) : "
                f"{len(posts)} posts, total analysé {scanned_posts}"
            )

            if len(posts) < args.limit:
                break

            if block_page + 1 < args.pages and args.delay:
                time.sleep(args.delay)

        if not query_failed:
            processed_queries.append(query)

        cumulative_counts = previous_counts + counts
        reused_candidates = cache_db.candidates_from_same_or_stricter_queries(
            normalize_query(query)
        )
        for artist in reused_candidates:
            cumulative_counts.setdefault(artist, 0)
        if reused_candidates:
            print(
                f"{len(reused_candidates)} candidat(s) réutilisé(s) depuis "
                "des recherches identiques ou plus restrictives."
            )
        cumulative_scanned_posts = previous_scanned_posts + scanned_posts
        if not query_failed:
            cumulative_state.setdefault("data", {})[query] = {
                "counts": dict(cumulative_counts),
                "scanned_posts": cumulative_scanned_posts,
            }

        ranked: list[tuple[str, int]] = []
        exact_totals: dict[str, int] = {}
        exact_percents: dict[str, float] = {}
        rejected_min_posts = 0
        rejected_max_posts = 0
        rejected_hits = 0
        rejected_percent = 0
        artists_to_check = [
            artist for artist in cumulative_counts if artist in artist_names
        ]
        print(
            f"Vérification exacte de {len(artists_to_check)} entrée(s) candidate(s)."
        )
        phase_started = time.perf_counter()
        print(
            f"Lecture locale des {len(artists_to_check)} compteurs totaux en cache...",
            flush=True,
        )
        total_by_artist = {
            artist: cache_db.get_artist_total(artist, args.cache_days)
            for artist in artists_to_check
        }
        cached_total_count = sum(
            total is not None for total in total_by_artist.values()
        )
        print(
            f"Cache total lu : {cached_total_count} trouvé(s), "
            f"{len(artists_to_check) - cached_total_count} à demander "
            f"({time.perf_counter() - phase_started:.1f} s).",
            flush=True,
        )
        missing_totals = {
            artist: artist
            for artist, total in total_by_artist.items()
            if total is None
        }
        if missing_totals:
            print(
                f"Compteurs totaux : {len(missing_totals)} requête(s), "
                f"lots parallèles de {args.counter_workers}."
            )
            fetched, errors = fetch_counts_parallel(
                missing_totals,
                args.user_id,
                args.api_key,
                args.counter_workers,
                "totaux",
            )
            print(
                f"Réponses totales reçues : {len(fetched)}/{len(missing_totals)}. "
                f"{len(fetched)} nouvelle(s) réponse(s) à enregistrer dans le cache "
                f"({len(errors)} sans réponse)...",
                flush=True,
            )
            fetched_items = list(fetched.items())
            for saved_index, (artist, (total, posts_data)) in enumerate(
                fetched_items, start=1
            ):
                total_by_artist[artist] = total
                cache_db.set_artist_total(artist, total)
                if saved_index == 1 or saved_index % 10 == 0 or saved_index == len(
                    fetched_items
                ):
                    print(
                        "Nouvelles réponses mises en cache — compteurs totaux "
                        f"{saved_index}/{len(fetched_items)}",
                        flush=True,
                    )
            if errors:
                artist, exc = next(iter(errors.items()))
                print(
                    f"Compteur Gelbooru indisponible pour {artist}: {exc}",
                    file=sys.stderr,
                )
                query_failed = True

        eligible = [
            artist
            for artist in artists_to_check
            if total_by_artist.get(artist) is not None
            and int(total_by_artist[artist]) >= args.min_artist_posts
            and (
                not args.max_artist_posts
                or int(total_by_artist[artist]) <= args.max_artist_posts
            )
        ]
        print(
            f"Filtrage sur le total terminé : {len(eligible)}/"
            f"{len(artists_to_check)} entrée(s) admissible(s).",
            flush=True,
        )
        print(
            f"Lecture locale des {len(eligible)} compteurs correspondants en cache...",
            flush=True,
        )
        matching_by_artist = {
            artist: cache_db.get_query_count(query_key, artist, args.cache_days)
            for artist in eligible
        }
        cached_matching_count = sum(
            count is not None for count in matching_by_artist.values()
        )
        print(
            f"Cache correspondant lu : {cached_matching_count} trouvé(s), "
            f"{len(eligible) - cached_matching_count} à demander.",
            flush=True,
        )
        missing_matches = {
            artist: f"{query} {artist}".strip()
            for artist, count in matching_by_artist.items()
            if count is None
        }
        if missing_matches and not query_failed:
            print(
                f"Compteurs correspondants : {len(missing_matches)} requête(s), "
                f"lots parallèles de {args.counter_workers}."
            )
            fetched, errors = fetch_counts_parallel(
                missing_matches,
                args.user_id,
                args.api_key,
                args.counter_workers,
                "correspondants",
            )
            print(
                f"Réponses correspondantes reçues : "
                f"{len(fetched)}/{len(missing_matches)}. "
                "Enregistrement dans le cache local...",
                flush=True,
            )
            fetched_items = list(fetched.items())
            for saved_index, (artist, (count, posts_data)) in enumerate(
                fetched_items, start=1
            ):
                matching_by_artist[artist] = count
                cache_db.set_query_count(query_key, artist, count)
                if saved_index == 1 or saved_index % 10 == 0 or saved_index == len(
                    fetched_items
                ):
                    print(
                        "Cache compteurs correspondants "
                        f"{saved_index}/{len(fetched_items)}",
                        flush=True,
                    )
            if errors:
                artist, exc = next(iter(errors.items()))
                print(
                    f"Compteur Gelbooru indisponible pour {artist}: {exc}",
                    file=sys.stderr,
                )
                query_failed = True

        print("Application finale des critères...", flush=True)
        for check_index, artist in enumerate(artists_to_check, start=1):
            print(
                f"Compteurs entree {check_index}/{len(artists_to_check)} : {artist}"
            )
            total_posts = total_by_artist.get(artist)
            if total_posts is None:
                continue
            if total_posts < args.min_artist_posts:
                rejected_min_posts += 1
                continue
            if args.max_artist_posts and total_posts > args.max_artist_posts:
                rejected_max_posts += 1
                continue
            matching_posts = matching_by_artist.get(artist)
            if matching_posts is None:
                continue
            percent = matching_posts * 100 / total_posts if total_posts else 0
            exact_totals[artist] = total_posts
            exact_percents[artist] = percent
            if matching_posts < args.min_hits:
                rejected_hits += 1
                continue
            if percent < args.min_match_percent:
                rejected_percent += 1
                continue
            ranked.append((artist, matching_posts))
        ranked.sort(
            key=lambda item: (
                -item[1],
                -artists.get(item[0], 0),
                item[0],
            )
        )

        cache_db.replace_query_results(query, ranked, cumulative_scanned_posts)
        print(
            f"{len(ranked)} {entity.label.lower()} retenus sur "
            f"{cumulative_scanned_posts} posts cumulés. "
            "Résultat enregistré dans le cache SQLite."
        )

        print(
            "Filtrage : "
            f"{rejected_min_posts} sous posts_min, "
            f"{rejected_max_posts} au-dessus posts_max, "
            f"{rejected_hits} sous min_hits, "
            f"{rejected_percent} sous part_min, "
            f"{len(ranked)} retenu(s)."
        )

        for artist, hits in ranked:
            combined_hits[artist] += hits
            query_membership[artist].add(query)

            summary_rows.append(
                {
                    "requete": query,
                    "artiste": artist,
                    "posts_correspondants": hits,
                    "posts_analyses": cumulative_scanned_posts,
                    "part_echantillon_pct": (
                        round(hits * 100 / cumulative_scanned_posts, 3)
                        if cumulative_scanned_posts
                        else 0
                    ),
                    "posts_totaux_artiste_db": artists.get(artist, 0),
                    "posts_totaux_gelbooru": exact_totals.get(artist, 0),
                    "part_catalogue_pct": round(exact_percents.get(artist, 0), 3),
                }
            )

    csv_path = args.sortie / "classement_artistes.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "requete",
                "artiste",
                "posts_correspondants",
                "posts_analyses",
                "part_echantillon_pct",
                "posts_totaux_artiste_db",
                "posts_totaux_gelbooru",
                "part_catalogue_pct",
            ],
            delimiter=";",
        )
        writer.writeheader()
        writer.writerows(summary_rows)

    combined_ranked = sorted(
        combined_hits,
        key=lambda artist: (
            -len(query_membership[artist]),
            -combined_hits[artist],
            -artists.get(artist, 0),
            artist,
        ),
    )

    combined_path = args.sortie / entity.candidate_filename
    combined_path.write_text(
        "\n".join(combined_ranked) + ("\n" if combined_ranked else ""),
        encoding="utf-8",
    )

    if len(processed_queries) == len(args.requetes):
        cumulative_state["version"] = 1
        cumulative_state["queries"] = args.requetes
        if fetched_new_pages:
            cumulative_state["next_page"] = args.page_debut + args.pages
        else:
            cumulative_state["next_page"] = min(
                cache_db.next_missing_page(normalize_query(query), args.page_debut)
                for query in args.requetes
            )
        save_cumulative_progress(cumulative_path, cumulative_state)
        print(
            f"Progression cumulative enregistrée : prochain départ page "
            f"{cumulative_state['next_page']}."
        )

    combined_csv = args.sortie / "classement_combine.csv"
    with combined_csv.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "artiste",
                "nombre_de_requetes",
                "requetes",
                "total_occurrences",
                "posts_totaux_artiste_db",
            ],
            delimiter=";",
        )
        writer.writeheader()
        for artist in combined_ranked:
            writer.writerow(
                {
                    "artiste": artist,
                    "nombre_de_requetes": len(query_membership[artist]),
                    "requetes": " | ".join(sorted(query_membership[artist])),
                    "total_occurrences": combined_hits[artist],
                    "posts_totaux_artiste_db": artists.get(artist, 0),
                }
            )

    print("\nCalculs terminés.")
    print(f"Classement détaillé : {csv_path}")
    print(f"Classement combiné : {combined_csv}")
    print(f"Liste unique : {combined_path}")
    if not args.sans_blacklist:
        print(
            f"Entrées déjà présentes dans la blacklist et ignorées : "
            f"{len(blacklisted_artists)}"
        )
    if not args.sans_ignore:
        print(
            f"Entrées déjà examinées via ignore.txt et ignorées : "
            f"{len(ignored_artists)}"
        )
        print(
            f"Recherches déjà traitées et sautées : "
            f"{skipped_query_count}"
        )

    if args.memoriser_requetes:
        if args.sans_ignore:
            print(
                "--memoriser-requetes est ignoré car --sans-ignore est actif.",
                file=sys.stderr,
            )
        else:
            added = cache_db.mark_processed_queries(processed_queries)
            print(
                f"{added} recherche(s) terminée(s) mémorisée(s) "
                "dans le cache SQLite."
            )

    if args.cache_check == "none":
        print(
            f"Cache Gelbooru SQLite : {cache_db.path} "
            "(contrôle global non demandé)"
        )
    else:
        print(
            f"Vérification {args.cache_check} du cache SQLite en cours...",
            flush=True,
        )
        integrity = cache_db.check(args.cache_check)
        print(
            f"Cache Gelbooru SQLite : {cache_db.path} "
            f"(intégrité : {integrity})"
        )
    cache_db.close()
    print("Terminé.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrompu par l'utilisateur.", file=sys.stderr)
        raise SystemExit(130)
