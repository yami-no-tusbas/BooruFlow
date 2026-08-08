"""Translate human-readable output emitted by the temporary legacy engines.

The scanners predate BooruFlow's external catalogs.  Keeping this adapter at
the process boundary prevents their French console text from leaking into an
English GUI while those engines are progressively replaced.
"""

from __future__ import annotations

import re


_EN_REPLACEMENTS = (
    ("Calculs terminés.", "Calculations completed."),
    ("Terminé.", "Completed."),
    ("Interrompu par l'utilisateur.", "Interrupted by the user."),
    ("Application finale des critères...", "Applying final criteria..."),
    ("Total Gelbooru", "Gelbooru total"),
    ("Page Gelbooru", "Gelbooru page"),
    ("Page e621", "e621 page"),
    ("du bloc", "in this block"),
    ("résultat(s)", "result(s)"),
    ("page(s)", "page(s)"),
    ("posts cumulés", "cumulative posts"),
    ("posts, total analysé", "posts, total analyzed"),
    ("post(s) examinés", "post(s) examined"),
    ("Classement détaillé", "Detailed ranking"),
    ("Classement combiné", "Combined ranking"),
    ("Liste unique", "Unique list"),
    ("Prochain départ", "Next start"),
    ("prochain départ", "next start"),
    ("Progression cumulative enregistrée", "Cumulative progress saved"),
    ("Cumul repris", "Cumulative state resumed"),
    ("Cache total lu", "Total-count cache read"),
    ("Cache correspondant lu", "Matching-count cache read"),
    ("Cache compteurs correspondants", "Matching-count cache"),
    ("Cache Gelbooru SQLite", "Gelbooru SQLite cache"),
    ("Cache e621", "e621 cache"),
    ("contrôle global non demandé", "global check not requested"),
    ("intégrité", "integrity"),
    ("Vérification exacte de", "Exact verification of"),
    ("Vérification", "Checking"),
    ("en cours", "in progress"),
    ("compteurs totaux", "total counts"),
    ("Compteurs totaux", "Total counts"),
    ("Compteurs correspondants", "Matching counts"),
    ("Compteurs entree", "Entry counts"),
    ("Compteurs entrée", "Entry counts"),
    ("Compteurs", "Counts"),
    ("Réponses totales reçues", "Total-count responses received"),
    ("Réponses correspondantes reçues", "Matching-count responses received"),
    ("Nouvelle(s) réponse(s)", "new response(s)"),
    ("nouvelle(s) réponse(s)", "new response(s)"),
    ("sans réponse", "without response"),
    ("à enregistrer dans le cache", "to save in the cache"),
    ("Enregistrement dans le cache local", "Saving to the local cache"),
    ("Lecture locale des", "Reading locally cached"),
    ("à demander", "to request"),
    ("requête(s)", "request(s)"),
    ("lots parallèles de", "parallel batches of"),
    ("Filtrage sur le total terminé", "Total-count filtering completed"),
    ("Filtrage", "Filtering"),
    ("sous posts_min", "below posts_min"),
    ("au-dessus posts_max", "above posts_max"),
    ("sous min_hits", "below min_hits"),
    ("sous part_min", "below match threshold"),
    ("retenu(s)", "retained"),
    ("retenus", "retained"),
    ("restent disponibles", "remain available"),
    ("dans la base", "in the database"),
    ("entrée(s)", "entry/entries"),
    ("candidat(s)", "candidate(s)"),
    ("trouvé(s)", "found"),
    ("admissible(s)", "eligible"),
    ("repris depuis le cache", "restored from cache"),
    ("réutilisé(s) depuis", "reused from"),
    ("reevaluation des criteres", "criteria re-evaluation"),
    ("sans relire les pages deja vues", "without rereading cached pages"),
    ("Aucun cumul compatible trouvé pour ce bloc", "No compatible cumulative state found for this block"),
    ("les compteurs repartent à zéro", "counts restart at zero"),
    ("Aucune entrée du cache ne satisfait les critères actuels", "No cached entry meets the current criteria"),
    ("aucune nouvelle page e621 demandée", "no new e621 page requested"),
    ("poursuite à la page", "continuing at page"),
    ("Fin réelle des résultats e621 atteinte", "Actual end of e621 results reached"),
    ("Erreur réseau sur", "Network error for"),
    ("Erreur e621 page", "e621 page error"),
    ("indisponible pour", "unavailable for"),
    ("Blacklist chargée", "Blacklist loaded"),
    ("Blacklist introuvable", "Blacklist not found"),
    ("Ignore list chargée", "Ignore list loaded"),
    ("Ignore list introuvable", "Ignore list not found"),
    ("Impossible de lire", "Could not read"),
    ("déjà traitée(s) seront ignorée(s)", "already processed will be ignored"),
    ("déjà traité(s)", "already processed"),
    ("déjà mémorisée(s)", "already remembered"),
    ("déjà présentes", "already present"),
    ("déjà examinées", "already reviewed"),
    ("Recherches", "Searches"),
    ("recherche(s)", "search(es)"),
    ("mémorisée(s)", "remembered"),
    ("ont été sautées", "were skipped"),
    ("sautées", "skipped"),
    ("ignorées", "ignored"),
    ("aucun artiste existant ne sera exclu", "no existing artist will be excluded"),
    ("aucun artiste ou tag déjà vu ne sera exclu", "no previously seen artist or tag will be excluded"),
    ("aucune exclusion supplémentaire", "no additional exclusion"),
    ("Artiste absent de la base", "Artist missing from the database"),
    ("Bilan", "Summary for"),
    ("observée(s)", "observed"),
    ("dans la plage de posts", "within the post range"),
    ("au-dessus du seuil de", "above the threshold of"),
    ("artistes", "artists"),
    ("personnages", "characters"),
    ("espèces", "species"),
    ("aucun", "none"),
)


def translate_legacy_log(text: str, language: str) -> str:
    """Return legacy output in the active UI language.

    French remains untouched because it is the engines' native output.  The
    replacement list intentionally operates on fragments so values, paths and
    query names remain byte-for-byte recognizable in the journal.
    """

    if language != "en":
        return text
    translated = text
    for source, target in _EN_REPLACEMENTS:
        translated = translated.replace(source, target)
    translated = re.sub(r"\bR[ée]sultat enregistr[ée] dans\b", "Result saved in", translated)
    return translated
