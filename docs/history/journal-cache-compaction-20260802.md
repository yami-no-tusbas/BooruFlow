# Journal de compaction du cache Gelbooru - 2026-08-02

## Périmètre validé

- Cache actif avant migration : `results/artists/gelbooru/cache_gelbooru_artists_v2.sqlite`
- Taille initiale : 2 693 095 424 octets
- Fichiers TXT nommés d'après une recherche : 192 fichiers, 19 888 octets
- Historique ancien dans `D:\0ZGrabber_blacklist\ignore.txt` : 51 lignes `query:`
- Conservation : sauvegarde datée du cache et sauvegarde de `ignore.txt`

## Transformation

- Schéma Gelbooru v1 vers v3 relationnel compact
- Suppression du JSON brut inutilisé dans le cache reconstruit
- Suppression des tables Gelbooru `posts`, `post_tags`, `query_posts`, `query_pages` et `query_candidates`
- Remplacement par `gel_queries`, `gel_query_pages` et `gel_query_page_candidates`
- Migration de l'historique des recherches vers `processed_queries`
- Retrait approuvé des anciens fichiers TXT par recherche et des lignes `query:` après validation

## Contrôles préalables

- Espace libre D: 398 737 498 112 octets
- Aucun processus Python/Artist-by-Tag actif
- Compilation Python et tests existants : OK
- Migration v1 vers v3 sur fixture : OK

## Résultat

- Cache actif v3 : 6 205 440 octets
- Sauvegarde v1 : `results/artists/gelbooru/cache_gelbooru_artists_v2.backup-20260802-094812.sqlite`
- Taille sauvegarde v1 : 2 693 095 424 octets
- Réduction du cache actif : 2 686 889 984 octets (99,7696 %, facteur 433,99)
- Recherches : 194
- Pages réelles : 859
- Posts résumés dans les pages : 68 800
- Occurrences candidat/page : 32 137
- Compteurs totaux : 13 165
- Compteurs recherche/entité : 8 811
- Historique des compteurs : 21 976
- Recherches historiques migrées : 51
- `PRAGMA quick_check` : ok
- `PRAGMA foreign_key_check` : 0 erreur
- Anciennes tables volumineuses présentes dans le cache actif : aucune
- 192 TXT par recherche envoyés dans la Corbeille ; 0 restant
- `D:\0ZGrabber_blacklist\ignore.txt` : 51 lignes `query:` retirées ; 0 restante
- Sauvegarde ignore : `D:\0ZGrabber_blacklist\ignore.backup-20260802-094812.txt`
- Tests et compilation après basculement : OK
