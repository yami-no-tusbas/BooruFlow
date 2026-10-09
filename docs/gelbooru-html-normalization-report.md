# Gelbooru — tags HTML échappés

## Cause et correction commune

`gelbooru_client.normalize_posts()` normalisait uniquement la forme JSON (`list`, `post` seul ou liste de posts). Il laissait `tags`, `tag_string_*` et les clés de `_tag_categories` tels que reçus. JSON ne décode pas les entités HTML. Entity Profiles recherchait donc littéralement `mahou_shoujo_lyrical_nanoha_a&#039;s` dans le catalogue local, qui contient `mahou_shoujo_lyrical_nanoha_a's`, et persistait une catégorie inconnue.

Le chemin Tagging avait son propre `html.unescape()` dans `post_tags()`, limité aux chaînes ; les listes, Image Analysis, les lecteurs de métadonnées et d'autres consommateurs n'utilisaient pas tous cette correction.

Le nouveau module `gelbooru_normalization.py` effectue une seule passe de `html.unescape()` à la frontière des réponses Gelbooru. Il couvre les entités numériques décimales et hexadécimales, apostrophes, ampersands, guillemets et entités HTML standard. Les champs de tags et clés de catégories deviennent Unicode. Les tags identiques sont dédupliqués avant comptage et persistence. Les requêtes, URL, sources et credentials ne sont pas décodés.

Un marqueur interne `_booruflow_unicode_tags=1` accompagne le post normalisé. Il survit aux copies `dict(post)`, aux signaux Qt et à une sérialisation JSON. Le passage transport → client → extracteur ne décode donc jamais une deuxième fois. Par exemple `literal&amp;#039;s` devient une fois `literal&#039;s` et reste ainsi ; aucune boucle de décodage jusqu'à stabilisation n'est utilisée.

`fetch_page()` et `GelbooruDapiClient`, le scanner Tagging, GelbooruPostProvider, le lecteur PostMetadata, Entity Profiles, Wiki Audit, Tag Details, l'inférence d'alias copyright et le cache relationnel Gelbooru utilisent maintenant ce point commun. Les noms renvoyés par le lookup distant de catégories du provider sont également décodés à leur ingestion. Les entrées du catalogue local et les scores WD14 ne sont pas soumis à un décodage global.

## Migration et fusion

Entity Profiles migre de la version 1 à 2 à l'ouverture. Le cache PostMetadata utilise un marqueur de migration distinct et persistant. Le correctif Image Analysis est une réparation explicite de ses métadonnées source, sans changement de sa version de schéma 26 ni migration de ses observations/modèles.

Chaque réparation s'effectue dans une transaction `BEGIN IMMEDIATE` :

- Une seule passe de décodage des anciennes données ; les marqueurs persistants empêchent une deuxième migration, y compris lors de deux ouvertures concurrentes.
- Union des témoins `(profile_id, post_id, tag)` et recalcul des occurrences depuis les posts distincts. Les anciennes occurrences ne sont pas additionnées naïvement.
- Si des profils deviennent identiques sur `(entity_tag, query)`, conservation prioritaire de l'identifiant du profil déjà décodé, sinon du plus petit ID ; union des posts et recalcul de `sample_count`. Des requêtes différentes restent des datasets différents.
- Pour une collision de statuts : **Ignore > Keep > Review**. Un Ignore explicite ne devient donc pas une suggestion conservée. Les décisions originales sont toutes archivées, et un conflit explicite est enregistré.
- Pour les décisions Tagging virtuelles : rejected > accepted > unreviewed ; pour les décisions sur un tag source existant : remove > keep. Les observations référencées ne sont pas fusionnées ni supprimées.
- Fusion des exclusions globales et des catégories connues. Les noms réellement renommés sont enrichis depuis le catalogue local en lecture seule, lorsqu'il est fourni.
- Archivage des lignes originales dans `gelbooru_html_archive`, détail des conflits dans `gelbooru_html_conflicts` et marqueur dans `gelbooru_html_migrations`.
- Refus de la réparation Entity Profiles si les anciens compteurs n'ont plus leurs témoins complets ; une erreur annule aussi les modifications et archives de la transaction.

Le writer PostMetadata reçoit ensuite des noms déjà Unicode et n'ajoute aucun deuxième décodage à la lecture/écriture. Le correctif explicite Image Analysis conserve le site des tags, les dates de récupération et l'état des caches ; e621 est exclu de la réparation.

## Données réelles réparées le 5 octobre 2026

Audit initial en lecture seule, simulation sur sauvegardes SQLite temporaires, puis sauvegardes cohérentes des trois bases avant application :

| Base / table | Lignes échappées avant | Après |
|---|---:|---:|
| Entity Profiles — profile_post_tags | 30 | 0 |
| Entity Profiles — recurring_tags | 9 | 0 |
| PostMetadataCache — remote_post_tags Gelbooru | 326 | 0 |
| Image Analysis — source_tags Gelbooru | 1 484 | 0 |
| Image Analysis — post_metadata_cache JSON Gelbooru | 6 | 0 |

Les noms/requêtes des trois profils étaient déjà décodés. Aucun conflit de décision ni collision de lignes n'a été trouvé dans ces données réelles ; les tests couvrent ces cas.

Les échantillons restent exactement **200, 100 et 100** posts. Les tables réelles conservent 16 402 témoins de tags, 3 040 lignes statistiques, 84 011 tags dans le cache partagé et 412 312 tags source Gelbooru dans Image Analysis.

L'exemple utilisateur est désormais stocké comme **`mahou_shoujo_lyrical_nanoha_a's`**, catégorie **copyright**, type **relation**, **13 occurrences**, statut **Review**. Son affichage natif Qt Windows montre **6,50 %** sur le profil de 200 posts. Vérification visuelle sur une copie temporaire de la base réparée, sans réseau ni mutation des décisions réelles.

Une comparaison avant/après vérifie l'identité des profils, posts, statuts et témoins après décodage canonique, ainsi que des tables analysis_items, tag_observations, wd14_score_vectors, tagging_review_batch_entries, image_provenances, item_artists et des source_tags e621. Les tables Image Analysis sont comparées par empreintes SHA-256 de leurs lignes ; leurs IDs, observations, scores WD14 et lots de publication restent inchangés.

Sauvegardes avant réparation : `var/backups/gelbooru-html/20261005T202844Z-fe1b59a4/` (timestamp UTC). Rapports détaillés : `var/tmp/gelbooru-html-repair-applied.json` et `var/tmp/gelbooru-html-verified.json`. Capture : `var/tmp/gelbooru-html-native-fixed.png`.

## Outil réutilisable

`python tools/repair_gelbooru_html.py` simule uniquement, sur copies cohérentes des bases présentes. `--root` permet de préciser un autre checkout. L'outil lit le catalogue explicitement configuré dans booruflow_settings.json, ou gelbooru_tagging_settings.json pour le helper autonome.

`python tools/repair_gelbooru_html.py --apply` simule d'abord, crée ensuite un nouveau répertoire de sauvegardes sans écrasement, puis applique les transactions. Fermer l'application et utiliser le code corrigé avant une réparation explicite évite qu'un ancien writer réintroduise des noms échappés. Aucun accès API ni téléchargement d'image ne fait partie de l'outil.

## Fichiers de ce correctif

- Nouveaux : `infrastructure/gelbooru_normalization.py`, `infrastructure/gelbooru_html_migration.py`, `tests/unit/test_gelbooru_html.py`, `tools/repair_gelbooru_html.py`, ce rapport.
- Ingestion/consommateurs sous `src/booruflow/` : `infrastructure/gelbooru_client.py`, `gelbooru_tagging.py`, `image_sources.py`, `post_metadata_client.py`, `post_metadata_cache.py`, `entity_profile_repository.py`, `booru_cache.py`, `tag_details.py`, `wiki_audit.py` ; `application/entity_profiles.py`, `application/wiki_aliases.py` ; `presentation/pyside6/entity_profiles.py`, `presentation/pyside6/tagging_controller.py`.
- Tests adaptés : `tests/unit/test_tagging.py`, `tests/unit/test_analysis_database_guard.py`, `tests/conftest.py`. Les guards couvrent maintenant aussi les bases actives Entity Profiles et PostMetadata, et les contrôleurs de test sans root explicite utilisent un root temporaire.

## Validation

Tests ciblés des lecteurs, provider, profils, Qt, cache, Tagging, Wiki et Auto Organize : **243 réussis** avant l'ajout des huit cas de réouverture supplémentaires. Régressions HTML finales : apostrophes décimales/hexadécimales, ampersand, guillemet, caractères accentués et autres entités standard, champs catégorisés, copie/sérialisation sans double décodage, lookup catégorie, stockage/réouverture sans double comptage, fusion de tags/profils, conflits archivés, rollback, concurrence, préservation e621 et observations, outil dry-run/backup/application.

Fichier final de régressions HTML : **43 réussis**. Suite complète finale : **1 395 réussis, 2 ignorés, 14 sous-tests réussis**, en **98,02 s**. La suite complète passe également après la réparation des bases réelles ; les guards garantissent son utilisation de bases temporaires.

Ruff ciblé sur tous les fichiers Python modifiés, compileall et `git diff --check` passent. Une requête contenant une apostrophe réelle est vérifiée intacte jusqu'aux paramètres HTTP et au client ; les échanges API des tests sont simulés.

Aucun commit, push ou packaging.
