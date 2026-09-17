#!/usr/bin/env python3
"""Découvre et classe les artistes e621 associés à des recherches de tags."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from booruflow.application.entity_types import ENTITY_TYPES, entity_type
from booruflow.cli.gelbooru_scan import (
    load_blacklisted_artists,
    load_ignore_file,
    normalize_query,
)
from booruflow.infrastructure.booru_cache import BooruCache

API_URL = "https://e621.net/posts.json"
USER_AGENT = os.getenv(
    "E621_USER_AGENT",
    "ArtistByTag/1.0 (personal local artist review tool)",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recherche des artistes sur e621.")
    parser.add_argument("db", type=Path)
    parser.add_argument("requetes", nargs="+")
    parser.add_argument("--pages", type=int, default=10)
    parser.add_argument("--page-debut", type=int, default=1)
    parser.add_argument("--limit", type=int, default=320)
    parser.add_argument("--min-artist-posts", type=int, default=0)
    parser.add_argument("--max-artist-posts", type=int, default=0)
    parser.add_argument("--min-match-percent", type=float, default=0)
    parser.add_argument("--sortie", type=Path, default=Path("resultats_e621"))
    parser.add_argument("--blacklist", type=Path, default=Path("blacklist.txt"))
    parser.add_argument("--ignore", type=Path, default=Path("ignore.txt"))
    parser.add_argument("--delay", type=float, default=0.6)
    parser.add_argument("--cache-days", type=int, default=30)
    parser.add_argument(
        "--cache-check",
        choices=("none", "quick", "full"),
        default="none",
        help=(
            "Vérification finale du cache : none (défaut), quick ou full. "
            "Les deux contrôles lisent l'ensemble du cache."
        ),
    )
    parser.add_argument("--autoriser-requetes-ignorees", action="store_true")
    parser.add_argument("--entity-type", choices=ENTITY_TYPES, default="artists")
    return parser.parse_args()


def load_e621_artists(path: Path, category: int = 1) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT name,post_count FROM tags WHERE category=?", (category,)
        ).fetchall()
    finally:
        connection.close()
    return {str(name): int(count) for name, count in rows}


def fetch_posts(query: str, page: int, limit: int) -> list[dict[str, Any]]:
    params = {"tags": query, "page": str(page), "limit": str(limit)}
    request = urllib.request.Request(
        f"{API_URL}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    posts = data.get("posts", []) if isinstance(data, dict) else []
    return [post for post in posts if isinstance(post, dict)]


def flatten_post(post: dict[str, Any]) -> dict[str, Any]:
    categories = post.get("tags", {})
    tags: list[str] = []
    tag_categories: dict[str, str] = {}
    if isinstance(categories, dict):
        for category, values in categories.items():
            if isinstance(values, list):
                tags.extend(str(value) for value in values)
                for value in values:
                    tag_categories[str(value)] = str(category)
    file_data = post.get("file", {})
    return {
        **post,
        "site_tags": categories,
        "tags": " ".join(tags),
        "_tag_categories": tag_categories,
        "md5": file_data.get("md5") if isinstance(file_data, dict) else None,
    }


def artist_passes(
    artist: str,
    matching: int,
    totals: dict[str, int],
    minimum_posts: int,
    maximum_posts: int,
    minimum_percent: float,
) -> bool:
    total = totals.get(artist, 0)
    if total < minimum_posts:
        return False
    if maximum_posts and total > maximum_posts:
        return False
    percent = matching * 100 / total if total else 0
    return percent >= minimum_percent


def main() -> int:
    args = parse_args()
    entity = entity_type(args.entity_type)
    if args.pages < 1 or args.page_debut < 1:
        raise SystemExit("Les pages doivent être positives.")
    if not 1 <= args.limit <= 320:
        raise SystemExit("--limit doit être compris entre 1 et 320.")
    artists = load_e621_artists(args.db, entity.e621_category)
    artist_names = set(artists)
    blacklisted = load_blacklisted_artists(args.blacklist, artist_names)
    ignored_artists, _ignored_queries, _ignore_path = load_ignore_file(args.ignore, artist_names)
    available = artist_names - blacklisted - ignored_artists
    args.sortie.mkdir(parents=True, exist_ok=True)
    cache = BooruCache(args.sortie / f"cache_e621_{entity.key}.sqlite", f"e621:{entity.key}")
    cumulative_path = args.sortie / f"progression_cumulative_e621_{entity.key}.json"
    state: dict[str, Any] = {
        "queries": args.requetes,
        "entity_type": entity.key,
        "next_page": 1,
        "data": {},
    }
    if args.page_debut > 1 and cumulative_path.is_file():
        try:
            loaded = json.loads(cumulative_path.read_text(encoding="utf-8"))
            if (
                loaded.get("queries") == args.requetes
                and loaded.get("entity_type", "artists") == entity.key
                and loaded.get("next_page") == args.page_debut
            ):
                state = loaded
                print(f"Cumul e621 repris à la page {args.page_debut}.")
        except (OSError, ValueError, TypeError):
            pass

    combined: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    reached_end = False
    fetched_any_page = False
    for query in args.requetes:
        print(f"\n=== e621 : {query} ===")
        prior = state.get("data", {}).get(query, {})
        counts = Counter({k: int(v) for k, v in prior.get("counts", {}).items()})
        cached_counts = cache.observed_artist_counts(normalize_query(query), entity.e621_post_key)
        for artist, matching in cached_counts.items():
            counts[artist] = max(counts.get(artist, 0), matching)
        if cached_counts:
            print(
                f"{len(cached_counts)} entrée(s) et leurs occurrences restaurées "
                "depuis les posts du cache SQLite."
            )
        cached_matches = [
            artist
            for artist, matching in counts.items()
            if artist in available
            and artist_passes(
                artist,
                matching,
                artists,
                args.min_artist_posts,
                args.max_artist_posts,
                args.min_match_percent,
            )
        ]
        if cached_matches:
            print(
                f"{len(cached_matches)} entrée(s) satisfont déjà les nouveaux "
                "critères dans le cache ; aucune nouvelle page e621 demandée."
            )
        else:
            print(
                "Aucune entrée du cache ne satisfait les critères actuels ; "
                f"poursuite à la page {args.page_debut}."
            )
            for block_page in range(args.pages):
                human_page = args.page_debut + block_page
                try:
                    posts = fetch_posts(query, human_page, args.limit)
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    print(f"Erreur e621 page {human_page}: {exc}", file=sys.stderr)
                    cache.close()
                    return 1
                normalized = [flatten_post(post) for post in posts]
                fetched_any_page = True
                cache.store_posts(
                    normalized,
                    normalize_query(query),
                    human_page,
                    0,
                    available,
                )
                for post in posts:
                    categories = post.get("tags", {})
                    post_artists = (
                        categories.get(entity.e621_post_key, [])
                        if isinstance(categories, dict)
                        else []
                    )
                    for artist in set(post_artists).intersection(available):
                        counts[artist] += 1
                print(
                    f"Page e621 {human_page} "
                    f"({block_page + 1}/{args.pages} du bloc) : {len(posts)} posts."
                )
                if len(posts) < args.limit:
                    reached_end = True
                    print("Fin réelle des résultats e621 atteinte.")
                    break
                if args.delay and block_page + 1 < args.pages:
                    time.sleep(args.delay)
        state.setdefault("data", {})[query] = {"counts": dict(counts)}
        candidates = cache.candidates_from_same_or_stricter_queries(normalize_query(query))
        for artist in candidates:
            counts.setdefault(artist, 0)
        in_total_range = 0
        passing_percentage = 0
        for artist, matching in counts.items():
            if artist not in available:
                continue
            total = artists.get(artist, 0)
            percent = matching * 100 / total if total else 0
            if total >= args.min_artist_posts and (
                not args.max_artist_posts or total <= args.max_artist_posts
            ):
                in_total_range += 1
                if percent >= args.min_match_percent:
                    passing_percentage += 1
            if not artist_passes(
                artist,
                matching,
                artists,
                args.min_artist_posts,
                args.max_artist_posts,
                args.min_match_percent,
            ):
                continue
            combined[artist] += matching
            rows.append(
                {
                    "requete": query,
                    "artiste": artist,
                    "posts_correspondants_observes": matching,
                    "posts_totaux_export_e621": total,
                    "part_observee_pct": round(percent, 3),
                }
            )
        print(
            f"Bilan {query!r} : {len(counts)} entrée(s) observée(s), "
            f"{in_total_range} dans la plage de posts, "
            f"{passing_percentage} au-dessus du seuil de "
            f"{args.min_match_percent:g} %."
        )

    state["next_page"] = args.page_debut + args.pages if fetched_any_page else args.page_debut
    state["reached_end"] = reached_end
    temporary = cumulative_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, cumulative_path)
    ranked = sorted(combined, key=lambda artist: (-combined[artist], artist))
    (args.sortie / entity.candidate_filename).write_text(
        "\n".join(ranked) + ("\n" if ranked else ""), encoding="utf-8"
    )
    with (args.sortie / f"classement_{entity.plural}.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=list(rows[0])
            if rows
            else [
                "requete",
                "artiste",
                "posts_correspondants_observes",
                "posts_totaux_export_e621",
                "part_observee_pct",
            ],
            delimiter=";",
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(ranked)} {entity.label.lower()} e621 retenus.")
    print(f"Prochain départ e621 : page {state['next_page']}.")
    if args.cache_check == "none":
        print(f"Cache e621 : {cache.path} (contrôle global non demandé)")
    else:
        print(
            f"Vérification {args.cache_check} du cache SQLite en cours...",
            flush=True,
        )
        print(f"Cache e621 : {cache.path} (intégrité : {cache.check(args.cache_check)})")
    cache.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
