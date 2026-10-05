# Cache WD14 sparse — rapport du 24 septembre 2026

## Résultat et incident de validation

Implémentation sur `refactor/pyside6-mvc`, sans commit, push ni packaging.
Les modifications Alpha déjà présentes ont été conservées.

**La consigne de ne pas modifier la vraie base n'a pas été respectée pendant la
première exécution de la suite complète.** Des tests Qt existants construisaient
`MainWindow` sans `project_root` temporaire. Ils ouvraient donc le dossier de
travail réel et ont déclenché la migration automatique v25 → v26 de
`D:\python\BooruFlow\var\state\image_analysis.sqlite`.

Conséquences confirmées en lecture seule :

- l'ancien cache `scores_json` a été supprimé ; le nouveau cache et ses mappings
  sont vides ; les scores seront reconstruits à la demande ;
- la base est maintenant en v26 : un ancien exécutable limité à v25 refusera de
  l'ouvrir ; aucun ancien exécutable n'a été lancé pour essayer ;
- le fichier fait toujours 2 463 514 624 octets ; 578 062 pages sont libres ;
- aucun `VACUUM`, aucune restauration, aucune autre intervention corrective sur
  la base active n'ont été effectués ;
- 13 923 `model_runs`, 82 533 observations, 3 867 embeddings, 385 100 tags source,
  8 387 entrées de review, 16 132 provenances et 25 entrées de métadonnées sont
  présents après l'incident. Les 12 runs et 286 observations de plus que dans le
  diagnostic fourni datent au plus tard du 23 septembre à 21:51:06 UTC, avant
  cette intervention. Il n'existe pas de snapshot complet avant/après permettant
  d'affirmer l'identité de toutes les autres lignes de la base active.

Le processus de la première suite, resté actif après les résultats, a été
interrompu. Les fenêtres de test concernées utilisent désormais des dossiers
temporaires et n'amorcent pas le worker réel. `tests/conftest.py` interdit à
`sqlite3.connect` et `sqlite3.dbapi2.connect` l'ouverture de la vraie base ; une
tentative fait aussi échouer le teardown, même si l'interface a capturé l'erreur.
Cette protection concerne le processus pytest ; les tests ne doivent pas lancer
de sous-processus contre un espace de travail réel.

## Audit réalisé avant l'implémentation

Recherche des tables, JSON, lectures/écritures, identités, prédictions, appels
directs et indirects dans `src`, `tests`, `tools` et `docs`.

| Élément | Rôle / dépendance |
|---|---|
| `infrastructure/wd14.py` | Charge `selected_tags.csv` dans l'ordre du modèle. Les sorties sont des float32 ONNX convertis en floats Python. Avec `store_threshold=0.0`, le résultat en RAM reste complet. |
| `wd14_config_identity` | Identité existante : modèle, SHA du modèle, prétraitement, seuil d'inférence et stratégie d'entrée. Conservée. |
| `application/targeted_wd14.py::analyze_many` | Seul producteur applicatif de `wd14_score_vectors`, et seul appelant applicatif de la lecture des vecteurs. |
| `ImageAnalysisRepository.save_wd14_score_vector` | Ancienne sérialisation JSON complète ; remplacée par l'encodage sparse. |
| `ImageAnalysisRepository.wd14_vectors_for_remote_posts` | Lecture multi-images utilisée par le WD14 ciblé multi-tags. Retourne désormais seulement les scores conservés. |
| `ImageAnalysisRepository.wd14_scores_for_remote_posts` | Autre API de lecture, sans appelant applicatif trouvé. Adaptée au même contrat et testée. |
| `presentation/pyside6/tagging_controller.py` | Lance le worker ciblé, fournit la session persistante, reçoit scores et progression. Aucune lecture SQLite directe de ces vecteurs. |
| `presentation/pyside6/tagging_page.py` | Classement, buckets et affichage des scores reçus. Pas d'accès au cache disque. |
| `worker/image_analysis.py` | Produit les observations via `save_tag_predictions`, séparément du cache ciblé. Aucun accès à `wd14_score_vectors`. |
| Image Analysis / Tagging individuel | Lisent les observations. La refonte ne change pas ces tables ni leurs seuils. |
| `application/similar_artists.py` | Utilise embeddings, tags source et observations WD14 acceptées ; pas les vecteurs persistants ciblés. |
| Publication Gelbooru/e621 | Utilise les décisions et deltas de review ; aucune dépendance au cache de scores. |
| `image_analysis_v23.sql` | Création historique du cache JSON. La dernière migration existante était v25 ; la nouvelle est v26. |
| Tests existants | Producteurs de fixtures dans `test_targeted_wd14.py` ; version de schéma dans `test_image_analysis_repository.py`. Adaptés. |

**Dépendance fonctionnelle au vecteur complet : oui, pour les demandes ciblées de
scores sous 0,10 et pour le choix du premier nom correspondant dans l'ordre du
modèle.** Ces comportements nécessitent désormais une réanalyse si le score
demandé manque. Aucun consommateur n'a été trouvé qui exige de conserver toutes
les 10 861 valeurs sur disque pour fonctionner.

Les foreign keys vers `model_runs` sont : `wd14_score_vectors`, `tag_observations`,
`embeddings`, `object_detections`, `image_statistics`. Elles utilisent toutes
`ON DELETE CASCADE`. L'expiration ne supprime donc **aucun model_run**.

## Cause racine et architecture

L'ancien cache répétait tous les noms et sérialisait tous les floats en texte,
y compris les scores très faibles. Le vecteur réel lu avant migration confirme
10 861 sorties et 395 639 octets pour une seule image.

Choix retenu : mapping relationnel partagé par identité exacte et vecteurs BLOB
sparse. Un format dense float32 aurait encore coûté 43 444 octets par image.
Compresser le JSON conserverait la répétition et nécessiterait sa décompression
complète. Le uint32 évite de dépendre de la limite uint16 de 65 535 ; les deux
octets supplémentaires par score sont secondaires. Aucun module NumPy ou ONNX
n'est requis pour lire le cache.

Ancien schéma :

```sql
wd14_score_vectors(
    model_run_id INTEGER PRIMARY KEY REFERENCES model_runs(id) ON DELETE CASCADE,
    scores_json TEXT NOT NULL,
    created_at TEXT NOT NULL
)
```

Nouveau schéma, détaillé dans `image_analysis_v26.sql` :

```text
wd14_label_sets
  id PK, model_name, model_version, configuration_hash, labels_hash, label_count
  UNIQUE(model_name, model_version, configuration_hash, labels_hash)

wd14_labels WITHOUT ROWID
  label_set_id FK, tag_index uint32, tag_name
  PK(label_set_id, tag_index), UNIQUE(label_set_id, tag_name)

wd14_score_vectors
  model_run_id PK/FK model_runs ON DELETE CASCADE
  label_set_id FK wd14_label_sets
  scores_blob BLOB, created_at TEXT UTC
  INDEX(created_at)
```

Les labels sont ceux de la session chargée ou, pour une lecture à froid, du CSV
local. Un mapping suffit pour plusieurs images. Le SHA-256 porte sur les noms
UTF-8 exacts, dans leur ordre, chacun préfixé par sa longueur uint32 little endian.
Les noms doivent être uniques et non vides. L'ordre du CSV installé a été comparé
au vecteur réel : correspondance exacte.

Hash constaté pour les 10 861 labels :
`7582621b3b566e70bc4e38c6d9fb8329b7048ce004f67a973a0be26571dc2fa0`.

La lecture exige modèle, version, configuration, hash et nombre de labels
compatibles. Elle vérifie également le mapping stocké, pas seulement son hash.
Les mappings minuscules sont conservés après expiration des vecteurs ; aucune
maintenance supplémentaire ni limite complexe de taille n'a été ajoutée.

## BLOB et contrat du cache

```text
octets 0..3 : ASCII WDS1 — magic et version de codec 1
octets 4..7 : nombre N de paires, uint32 little endian
puis N paires de 8 octets :
    tag_index uint32 little endian
    score IEEE-754 float32 little endian
```

Taille exacte : `8 + 8*N` octets. Indices strictement croissants, sans padding.
Les tailles incohérentes, versions inconnues, indices invalides/dupliqués et
scores non finis/hors intervalle invalident le cache au lieu de fabriquer un
résultat. Une liste vide de scores conservés est un BLOB valide de 8 octets.

`WD14_CACHE_MIN_SCORE = 0.10` est centralisé dans `wd14_score_cache.py`.
La comparaison a lieu **avant** l'arrondi float32 : 0,10 est conservé ;
0,099999999 ne l'est pas, même si un arrondi float32 pourrait le rapprocher du
seuil. Les scores de 0,10 à 0,50 sont conservés normalement. Les ratings suivent
le même contrat. Aucune normalisation et aucune hypothèse de somme égale à 1.

Une absence signifie « inconnu dans ce cache », jamais zéro. Le WD14 ciblé ne
réutilise une image que si toutes ses cibles demandées sont présentes. Il
conserve aussi le premier nom correspondant dans l'ordre du vecteur complet :
si ce premier score manque, un autre nom conservé ne le remplace pas.

En cas de miss, le chemin existant d'acquisition et d'inférence est repris. Le
vecteur complet reste en RAM, et les scores exacts, même sous 0,10, alimentent
classement, buckets et résultats. Ils ne sont simplement pas écrits sur disque.
Un échec ou une annulation continue à suivre les états existants ; pas de faux
score de secours. Une demande ultérieure du même score faible peut refaire
l'inférence, conformément au caractère reconstructible du cache.

Session persistante, instance CUDA unique, inférences séquentielles, quatre
acquisitions au maximum, deux lots préparés au maximum, préparation CPU et ordre
final des résultats : mécanismes existants conservés. Le moteur d'inférence
`wd14.py` n'a pas été modifié par cette intervention.

## TTL et migration

`WD14_CACHE_TTL_DAYS = 30`, âge absolu depuis l'écriture en UTC. Une entrée âgée
d'exactement 30 jours est expirée ; un hit ne prolonge pas sa durée de vie.
Les entrées expirées sont ignorées à la lecture. Après une opération ciblée,
`prune_wd14_score_vectors` en supprime au plus 256, via l'index temporel.
Aucun timer, thread ou parcours complet au démarrage. Si aucune opération WD14
n'a lieu, les anciennes lignes peuvent rester sur disque mais ne sont plus
réutilisées.

La migration v26 abandonne uniquement l'ancienne table de cache et crée le
nouveau schéma vide. Pas de `SELECT scores_json`, pas de conversion JSON et pas
de deuxième table contenant les anciens vecteurs. Les autres tables et les runs
sont conservés. SQLite doit néanmoins libérer ses pages : le coût disque d'un
DROP volumineux n'est pas nul, et sa durée n'est pas garantie constante.

La migration est atomique avec `BEGIN IMMEDIATE` et relecture de `user_version`
sous verrou : deux ouvertures concurrentes ne peuvent pas recréer et vider le
nouveau cache. Une réouverture en v26 ne rejoue aucune instruction v26 et
conserve les vecteurs déjà écrits ; les tests de réouverture et d'ouvertures
concurrentes vérifient cette idempotence normale. Le fichier SQL brut n'est pas
un script à rejouer manuellement sur une base v26.
Un test injecte une erreur au milieu du DDL et vérifie le rollback
de la table ancienne et de la version. WAL, `busy_timeout=30000` et les réglages
existants sont conservés. Aucune refonte générale de SQLite.

## Mesures de stockage

| Mesure | Vecteur réel | Fixture synthétique reproductible |
|---|---:|---:|
| Labels | 10 861 | 10 861 |
| Scores ≥ 0,10 | 24 | 24 |
| Ancien JSON par image | 395 639 octets | 542 562 octets |
| Nouveau BLOB par image | 200 octets | 200 octets |
| Réduction du vecteur | ×1 978,195 | ×2 712,81 |
| Mapping partagé, payload estimé | 186 939 octets | 390 747 octets |

Le round-trip du vecteur réel restitue **exactement** les 24 floats conservés :
ils provenaient déjà de float32 ONNX. Des fixtures Python décimales arbitraires
sont, elles, comparées avec une tolérance float32.

Pour **5 999 analyses** avec 24 valeurs retenues chacune :

- BLOBs : **1,144 MiB** ;
- BLOBs et un mapping réel partagé : **1,322 MiB de payload estimé** ;
- référence fournie : **2 251,44 MiB de JSON**, soit environ **×1 702** de moins
  en incluant ce mapping estimé ;
- extrapolation du seul JSON échantillonné : 2 263,487 MiB, proche de la mesure
  globale fournie.

Ces valeurs ne prédisent pas la taille physique SQLite : pages, index, tables
saines, WAL et espace déjà libre s'y ajoutent. Un seul vecteur réel a été
échantillonné ; le nombre de scores retenus varie selon l'image. La réduction de
trois ordres de grandeur est démontrée pour le cas sparse mesuré, pas garantie
pour tous les vecteurs.

## Fichiers concernés et validation

Fichiers de production modifiés par cette intervention :

- `src/booruflow/infrastructure/wd14_score_cache.py` : nouveau codec et constantes ;
- `src/booruflow/infrastructure/schema/image_analysis_v26.sql` : nouvelle migration ;
- `src/booruflow/infrastructure/image_analysis_repository.py` : persistance,
  lecture, expiration et branche de migration ;
- `src/booruflow/application/targeted_wd14.py` : labels explicites, misses et purge.

Tests, benchmark et documentation :

- `tests/unit/test_wd14_score_cache.py` : mapping/hash/versions, codec, seuils,
  corruption, uint32 > uint16, taille, TTL, restart, foreign keys, rollback,
  migration sans lecture JSON et concurrence ;
- `tests/unit/test_targeted_wd14.py` : fixtures avec labels, précision float32,
  réanalyse sparse/TTL/ordre modifié, scores faibles exacts en RAM, session réutilisée ;
- `tests/unit/test_image_analysis_repository.py` : schéma v26 et fixture historique ;
- `tests/conftest.py` : protection de la base active pendant les tests ;
- `tests/unit/test_analysis_database_guard.py` : 8 contrôles de la protection,
  avec un délégué simulé qui ne peut jamais ouvrir la base active ;
- `tests/unit/test_pyside6_shell.py`, `test_pyside6_workflows.py` et
  `test_gelbooru_backend_routing.py` : isolation
  des fenêtres de test qui utilisaient le dossier courant ;
- `tools/benchmark_wd14_score_cache.py` : benchmark sans accès à une base ;
- `docs/wd14-sparse-cache-report.md` : ce rapport.

Les recherches ont utilisé `rg` sur les symboles cités dans l'audit, la lecture
des migrations et `git status`, `git branch --show-current`, `git diff`.
Mesures réelles : scripts Python ponctuels utilisant exclusivement une connexion
SQLite URI `mode=ro&immutable=1`, après vérification de l'absence de WAL. Aucun
appel de repository n'a été utilisé pour ces mesures. C'est la première suite
Qt, distincte de ces mesures, qui a déclenché la migration indésirable.

Commandes de validation depuis `D:\python\BooruFlow` :

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_analysis_database_guard.py tests/unit/test_wd14_score_cache.py tests/unit/test_targeted_wd14.py tests/unit/test_image_analysis_repository.py -q --disable-warnings --tb=short
.venv/Scripts/python.exe -m pytest tests/unit/test_pyside6_shell.py tests/unit/test_pyside6_workflows.py tests/unit/test_wd14_score_cache.py -q --disable-warnings --tb=short
.venv/Scripts/python.exe -m pytest -q --disable-warnings --tb=short
.venv/Scripts/python.exe -m pytest -q --disable-warnings --tb=short --ignore=tests/unit/test_gelbooru_backend_routing.py
.venv/Scripts/python.exe tools/benchmark_wd14_score_cache.py
.venv/Scripts/python.exe -m ruff check src/booruflow/infrastructure/wd14_score_cache.py src/booruflow/infrastructure/image_analysis_repository.py src/booruflow/application/targeted_wd14.py tests/conftest.py tests/unit/test_analysis_database_guard.py tests/unit/test_wd14_score_cache.py tests/unit/test_targeted_wd14.py tests/unit/test_image_analysis_repository.py tests/unit/test_gelbooru_backend_routing.py tests/unit/test_pyside6_shell.py tests/unit/test_pyside6_workflows.py tools/benchmark_wd14_score_cache.py
git diff --check
```

Résultats après reprise et vérification de l'isolation :

- **95 passed**, code natif **0** : protection, codec, repository/migrations,
  targeted WD14 ;
- **242 passed, 9 subtests passed** pour cache + protection + tests UI concernés ;
- **1 102 passed, 2 skipped, 11 subtests passed**, retour de `pytest.main` **0**,
  pour la suite complète protégée, en 43,27 s ;
- **réserve de validation :** le processus de cette suite complète a ensuite
  terminé avec le code 1 à la fermeture, malgré le retour 0 de pytest. Seul le
  thread Python principal restait alors présent. Le bon déroulement des
  assertions ne suffit donc pas à déclarer une exécution complète propre ;
- les 13 assertions des tests de routage WebEngine passent également isolément,
  mais leur fermeture de processus reste problématique dans cet environnement ;
- une tentative de destruction Qt explicite des fenêtres de test a déclenché
  une access violation native et a été retirée. Aucun changement de cycle de vie
  du navigateur de production n'est inclus ;
- **1 089 passed, 2 skipped, 11 subtests passed**, code natif **0**, en 40,94 s
  pour le contrôle complet excluant uniquement
  `tests/unit/test_gelbooru_backend_routing.py` ; cette exécution circonscrit la
  réserve de fermeture aux scénarios de routage WebEngine ;
- Ruff ciblé, benchmark et `git diff --check` : réussis.

Les logs d'assertions sont dans `var/test-results/wd14-sparse-final-focused.txt`
et `var/test-results/wd14-sparse-final-full.txt`. L'exécution se terminant
normalement sans les 13 tests de routage est conservée dans
`var/test-results/wd14-sparse-without-webengine-routing.txt`. La trace de la tentative de
fermeture forcée, qui ne correspond pas au code final, est dans
`var/test-results/wd14-sparse-routing-final.txt`.
Les premiers tests ont révélé une fixture qui simulait v16 en conservant les
tables v26 et une course lors de la première activation simultanée de WAL sur
une DB de test en journal DELETE. La fixture historique a été corrigée et le
test de concurrence v26 utilise désormais WAL, comme la base de production.
Le réglage général de journalisation n'a pas été modifié.

Les tests réels des backends d'embeddings restent désactivés par leur condition
habituelle. Pas d'inférence GPU réelle ni de publication distante pendant cette
validation ; les comportements du pipeline et de publication sont vérifiés par
les tests locaux avec doubles. Pas de packaging ni de validation d'exécutable.

## Récupération physique facultative — non exécutée

Le DROP libère les pages pour réutilisation par SQLite mais ne réduit pas le
fichier. Une récupération physique est facultative :

1. Fermer BooruFlow et tous les workers/outils ayant accès à la base.
2. Sauvegarder la base et, s'ils existent encore, ses fichiers WAL/SHM cohérents.
   Prévoir assez d'espace pour la sauvegarde et le fichier temporaire de VACUUM.
3. Avec un outil SQLite, ouvrir explicitement la bonne base puis exécuter,
   uniquement sur décision manuelle :

   ```sql
   PRAGMA wal_checkpoint(TRUNCATE);
   VACUUM;
   PRAGMA integrity_check;
   PRAGMA foreign_key_check;
   ```

4. Vérifier `integrity_check = ok` et aucune ligne de `foreign_key_check`, puis
   rouvrir avec une version de BooruFlow prenant en charge le schéma v26.

Ne pas lancer cette procédure avec l'application ouverte. Aucun appel automatique
de VACUUM n'a été ajouté au démarrage, à la migration, au TTL ou à la fermeture.

## Inventaire exact des tests UI anciennement reliés au workspace réel

Ces tests construisaient une fenêtre avec un `project_root` implicite égal au
dossier courant. Tous ne lisaient pas forcément la base : cela dépendait des
pages activées. Le helper de chaque classe est maintenant isolé.

**test_gelbooru_backend_routing.py**

- `test_internal_browser_tabs_are_separate_from_hidden_publisher_page`
- `test_backend_selection_routes_options_and_tagging_to_the_same_factory`
- `test_publisher_and_preparation_use_only_the_queued_gui_log_signal`
- `test_tagging_search_settings_are_persisted_restored_and_safely_clamped`
- `test_alias_database_actions_live_only_in_options_and_keep_existing_routing`
- `test_embedded_dialog_diagnostic_toggle_updates_future_publishers`
- `test_http_diagnostic_toggle_keeps_real_submit_path_and_arms_transport`
- `test_http_diagnostic_ui_refuses_to_arm_without_startup_mode`
- `test_cdp_open_and_session_test_never_call_embedded_backend`
- `test_embedded_open_remains_embedded_and_disabled_is_inert`
- `test_cdp_selection_is_persisted_and_used_after_save`

**test_pyside6_shell.py**

- `test_main_window_exposes_top_level_navigation`
- `test_options_wd14_install_does_not_wait_for_worker_readiness`
- `test_sidebar_rows_map_to_pages_by_key_and_width_stays_fixed`
- `test_status_bar_replaces_messages_and_manages_scoped_progress`
- `test_status_priority_keeps_errors_until_explicit_clear`
- `test_status_progress_can_be_replaced_by_success`
- `test_page_activation_uses_central_debug_log_convention`
- `test_tagging_and_tag_browser_publish_to_global_status_bar`
- `test_image_finder_publishes_results_to_global_status_bar`
- `test_status_bar_stays_responsive_with_long_messages`
- `test_taxonomy_and_options_inventory_load_only_on_first_navigation`
- `test_embedded_webengine_profile_is_lazy_and_created_once_on_gui_navigation`
- `test_log_can_be_toggled_and_cleared`
- `test_log_ingestion_strips_ansi_without_damaging_unicode`
- `test_debug_logs_are_hidden_by_default_but_remain_accessible`
- `test_dashboard_cards_navigate_by_stable_key`
- `test_grabber_tools_reuses_review_and_launcher_pages`
- `test_options_install_actions_route_to_existing_analysis_controller`
- `test_lazy_page_is_constructed_once_then_reused`
- `test_lazy_navigation_shows_existing_overlay_before_prepare_and_hides_it_once`
- `test_failed_lazy_page_is_isolated_and_retry_rebuilds_it`
- `test_dashboard_and_sidebar_share_the_same_lifecycle_entry`
- `test_close_with_unloaded_and_ready_lazy_features`
- `test_standard_window_keeps_every_page_horizontally_accessible`
- `test_image_analysis_action_bar_stays_inside_main_viewport`
- `test_tagging_review_actions_have_nonzero_geometry_in_main_viewport`
- `test_task_center_refreshes_when_a_task_changes`
- `test_language_change_retranslates_existing_widgets`
- `test_organization_can_prepare_a_wiki_draft`
- `test_taxonomy_preview_requires_explicit_confirmation`

**test_pyside6_workflows.py**

- `test_review_engine_summary_is_captured_for_visible_status`
- `test_e621_candidate_file_is_exposed_as_review_results`

Les deux fenêtres directes reçoivent désormais un `project_root` temporaire et
`start_image_worker=False`. Les ressources de lecture restent explicitement
pointées vers le dépôt. Dans `test_pyside6_shell.py`, le helper crée un
`TemporaryDirectory` uniquement lorsque le test ne fournit pas déjà son propre
répertoire. Dans `test_gelbooru_backend_routing.py`, chaque fenêtre reçoit un
sous-dossier de `tmp_path` ; pytest gère le nettoyage différé des profils WebEngine.

Les quatre accès SQLite effectivement interceptés par la première suite protégée
étaient dans `GelbooruBackendRoutingTests` :

- `test_backend_selection_routes_options_and_tagging_to_the_same_factory`
- `test_embedded_dialog_diagnostic_toggle_updates_future_publishers`
- `test_http_diagnostic_toggle_keeps_real_submit_path_and_arms_transport`
- `test_publisher_and_preparation_use_only_the_queued_gui_log_signal`

Ils ont échoué avant l'appel SQLite, puis leur isolation a été corrigée.

## Vérification de la base active après reprise

Aucune connexion SQLite à la base active n'a été utilisée après la demande de
reprise. Seules ses métadonnées de fichier ont été consultées :

- taille : **2 463 514 624 octets** ;
- dernière écriture UTC : **2026-09-23 22:10:31** (incident déjà signalé) ;
- ces valeurs sont inchangées entre la reprise et les validations protégées.

La protection compare des chemins puis lève une exception **avant** l'appel au
connecteur original. Ses propres 8 tests utilisent un délégué simulé pour tester
Path, chaîne, bytes, chemin avec `..`, chemin relatif et URI SQLite, dont
`mode=ro&immutable=1`. Ils ne peuvent donc pas ouvrir la vraie base même si une
régression rendait le contrôle défaillant. Aucun accès bloqué n'a été signalé
pendant la suite complète finale ni le contrôle complémentaire. Aucun VACUUM ni rollback de version.

## État des modifications à la livraison

14 fichiers relèvent de cette intervention :

| État Git | Fichiers |
|---|---|
| Modifiés, déjà modifiés avant cette tâche | `src/booruflow/application/targeted_wd14.py`, `src/booruflow/infrastructure/image_analysis_repository.py`, `tests/unit/test_targeted_wd14.py`, `tests/unit/test_pyside6_workflows.py` |
| Modifiés par cette tâche | `tests/unit/test_image_analysis_repository.py`, `tests/unit/test_pyside6_shell.py`, `tests/unit/test_gelbooru_backend_routing.py` |
| Nouveaux, non suivis | `src/booruflow/infrastructure/wd14_score_cache.py`, `src/booruflow/infrastructure/schema/image_analysis_v26.sql`, `tests/conftest.py`, `tests/unit/test_analysis_database_guard.py`, `tests/unit/test_wd14_score_cache.py`, `tools/benchmark_wd14_score_cache.py`, `docs/wd14-sparse-cache-report.md` |

Les autres différences Alpha présentes à l'arrivée, y compris les deux CSV déjà
supprimés dans `retro_cleanup`, n'ont pas été annulées. Aucun commit, push ou
packaging. Le moteur d'inférence, ses optimisations récentes et les chemins de
publication n'ont reçu aucune modification supplémentaire de production.

À la reprise, l'implémentation du cache n'a pas été recommencée : seules la
protection des tests, sa validation et la documentation ont été finalisées.
