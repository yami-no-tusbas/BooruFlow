# Image Analysis — cache absent, préparation en boucle et fermeture

Corrections locales du 7 octobre 2026 dans `refactor/pyside6-mvc`. Aucun commit, push ou packaging. Les modifications préexistantes, notamment Curated Tag Profiles et les normalisations Gelbooru, sont conservées. Aucun changement de schéma.

## Preuves de la portable

Lecture des journaux sous `D:/BooruFlow-2026.10.07-g62e950e-dirty-1241-gpu/var/logs` et des deux bases de cette portable avec SQLite `mode=ro&immutable=1`. Aucun WAL n'était présent au moment de cette lecture. Aucun constructeur de repository ou migration n'a été exécuté sur ces bases.

Dans `booruflow_2026-10-07_12-54-42.log` :

- 9 338 démarrages et 9 337 fins de préparation de sources ;
- 9 338 aperçus réussis répétés pour l'item 1 / post 19998 ;
- quatre recyclages annoncés après 100 ;
- les quatre hashes signalés correspondent aux jobs ci-dessous, toujours `processing`, avec `source_state=resolved` ; aucun de ces quatre BLOBs n'est présent dans le cache.

| SHA-256 (préfixe) | Item | Post Gelbooru | Tentatives persistées | Échecs visibles dans ce journal |
|---|---:|---:|---:|---:|
| a2555f895d6c | 6240 | 14954528 | 81 | 81 |
| e2a04bda7b44 | 20927 | 14985410 | 107 | 106 |
| f14b82bc9d11 | 20928 | 14985412 | 107 | 106 |
| 8dec5cba8168 | 20929 | 14985417 | 107 | 106 |

Le cache contient 5 585 images et 2 146 962 477 octets logiques, près de sa limite de 2 Gio. Il existe également une source `unresolved/pending` avec `analysis_requested=0` et aucune réservation de résolution.

`booruflow-fatal.log` contient deux erreurs récentes identiques : le 7 octobre à 09:04:24 UTC et 11:24:31 UTC, `_sources_finished` appelle `queue_counts` après fermeture de la connexion. La première session appartient à la portable du 5 octobre ; la seconde à celle du 7 octobre.

## Cause du premier bug

Les chaînes de 64 caractères sont le SHA-256 des octets de l'image acquise, persisté dans `analysis_items.content_sha256` et utilisé comme clé dans le cache SQLite BLOB. Ce ne sont ni des IDs de futures ni des clés d'un dictionnaire de résultats.

`ImageSourceService.materialize_item` appelle `ImageAnalysisCache.materialize`. Cette dernière levait explicitement `KeyError(cache_key)` quand `get` ne trouvait plus un BLOB valide. Le cache est borné et son éviction LRU ne retire pas les métadonnées du job : `source_state=resolved` ne garantit donc pas que ses octets résident encore dans le cache. `get` élimine aussi les BLOBs dont l'intégrité SHA-256 est invalide. L'absence des quatre entrées est vérifiée ; l'éviction passée de chaque entrée ne peut pas être datée avec l'ancien journal, qui ne conservait pas cette provenance.

Le lot réservait tous ses items, mais ajoutait un item à `contexts` seulement après sa matérialisation et l'extraction de ses frames. Si cette étape échouait, l'item fautif et les suivants déjà réservés ne figuraient pas dans le nettoyage d'erreur. Ils restaient `processing`, puis la récupération des traitements périmés les remettait `pending` et les reprenait indéfiniment. Les membres déjà préparés pouvaient être déclarés en échec à cause d'un autre item. L'ancien lot n'entretenait pas non plus de heartbeat pendant une longue inférence.

La boucle de préparation est un défaut distinct, aggravant les rafraîchissements : `_sources_finished` relançait si `queue_counts()['unresolved']` était non nul. Ce compteur inclut les sources visibles sans demande d'analyse, alors que le worker ne réserve que `analysis_requested=1`. Une source annulée/non demandée suffisait donc à faire tourner des workers vides, sans erreur. Leurs fins déclenchaient `_show_current`, qui effaçait puis décodait à nouveau le même aperçu. Un échec de lot émet une notification de changement d'analyse, mais ne lance pas directement la préparation : les deux boucles partageaient le rafraîchissement et la récupération des états, pas un unique mécanisme de retry.

## Traitement, arrêt et aperçu

- Chaque claim possède désormais son contexte et une frontière d'erreur par item dès la matérialisation. L'item fautif passe à `failed`, y compris s'il échoue avant la création de ses runs. Les autres membres continuent et leurs résultats sont conservés.
- Aucun retry automatique d'une source absente/invalide. L'état `failed` est persistant et exclu de la récupération des jobs `processing`. Il faut restaurer/acquérir les octets avant de relancer manuellement une analyse ; aucun retéléchargement automatique n'est ajouté.
- Une erreur d'inférence du lot autorise un seul fallback par frame. Si une frame échoue encore, seul son item échoue. La cardinalité des résultats est vérifiée ; la consommation des frames reste liée au contexte de leur item, même si un voisin échoue ou réutilise un run en cache.
- Les ressources matérialisées et frames temporaires sont libérées par `ExitStack`. Un heartbeat de lot entretient les claims encore en traitement ; les membres terminés n'empêchent pas le heartbeat de leurs voisins.
- `ImageCacheMissError`, compatible avec `KeyError`, explique l'absence ou l'invalidité du BLOB. Les erreurs incluent type, message, traceback, IDs de lot/item, site/post, source path/blob, SHA-256 complet, phase et absence de retry automatique. Cette journalisation couvre aussi le chemin d'analyse unitaire et les erreurs fatales du processus.
- `has_sources_to_resolve` utilise les mêmes conditions de demande/réservation/capacité que `claim_sources_to_resolve`. Le contrôleur ne démarre plus un worker vide et ne relance plus sur le compteur d'affichage `unresolved`.
- Un aperçu décodé est identifié par item, SHA-256, chemin et MIME. Les mises à jour de tags, observations et état conservent ce bitmap sans lecture de cache, effacement ou décodage. Un changement réel de source/item charge un nouvel aperçu. Une éviction ultérieure ne détruit plus le bitmap déjà affiché.

## Recyclage

Le code de sortie normal `75` et le redémarrage Qt restent utilisés. L'ancien compteur augmentait d'une unité par appel de lot, malgré le libellé « items ». Il compte maintenant les images prises en charge et limite le dernier lot à la capacité restante : avec des lots de 16, le recyclage arrive exactement à 100 images. Les contextes sont terminés avant la sortie ; les backends et connexions du worker sont fermés également lors du recyclage.

Les associations source/post/SHA et runs restent persistées en SQLite. Une nouvelle instance reprend les jobs `pending`, sans mapping éphémère hérité du processus précédent. Les tests traitent 100 images, rouvrent repository/cache/worker, puis vérifient les cinq restantes et les résultats des 105 posts. Un second test vérifie la sortie effective de `main` avec `RECYCLE 100`, code 75, puis la programmation d'un seul redémarrage Qt.

## Cause et correction de la fermeture

`shutdown` demandait une interruption au thread de préparation mais son `run` ne consultait jamais cette demande. Après une attente de 3 secondes non vérifiée, la connexion du contrôleur était fermée. Le signal `completed` pouvait arriver plus tard ou être déjà en attente dans Qt ; `_sources_finished` n'avait aucune garde et appelait `queue_counts` sur la connexion fermée.

Le cycle est désormais représenté explicitement par `_shutting_down` et `_closed` : actif, fermeture engagée, fermé. Dès la fermeture engagée, les résultats tardifs et les entrées asynchrones concernées sortent avant tout accès au repository, rafraîchissement ou nouvelle opération. Aucun `except ProgrammingError` n'est utilisé.

Les timers sont arrêtés, les signaux des workers de sources/scan/migration sont déconnectés et les opérations différées de maintenance sont retirées avant fermeture. Les gardes restent nécessaires pour les signaux et timers déjà en attente. Le worker de sources consulte l'interruption entre les fichiers et les posts, libère les réservations qu'il n'a pas traitées, termine sa requête en cours sur sa propre connexion, puis ferme son service/cache et sa connexion.

`shutdown` est idempotent. Il renvoie `False` si un thread actif n'a pas encore terminé : la fenêtre ignore alors sa fermeture et conserve le contrôleur et sa base. Un appel suivant termine la fermeture après l'arrêt des threads. Les producteurs sont arrêtés avant `sources.close` et `repository.close`, appelés une seule fois. Le thread de sources terminé est rejoint avant suppression de son objet Qt et lancement de son successeur.

## Fichiers modifiés pour cette correction

| Fichier | Rôle |
|---|---|
| `src/booruflow/worker/image_analysis.py` | Isolation par claim, frames/runs, heartbeat, diagnostics, compteur/recyclage et fermeture des ressources |
| `src/booruflow/infrastructure/image_analysis_cache.py` | Exception explicite de BLOB absent/invalide |
| `src/booruflow/infrastructure/image_analysis_repository.py` | Lecture de l'éligibilité de préparation, sans migration |
| `src/booruflow/presentation/pyside6/image_analysis_controller.py` | Préparation éligible, aperçu stable, interruption et lifecycle des callbacks |
| `src/booruflow/presentation/pyside6/image_analysis_page.py` | Conservation du bitmap pendant une mise à jour de revue |
| `src/booruflow/presentation/pyside6/main_window.py` | Respect d'une fermeture différée du contrôleur |
| `tests/unit/test_image_analysis_regressions.py` | 16 cas de régression |
| `docs/image-analysis-cache-loop-and-shutdown.md` | Ce rapport |

Les modifications précédentes de ces fichiers sont conservées ; notamment `apply_review_observation_bundle` dans le repository et les changements précédents de la fenêtre principale. Les fichiers de packaging n'ont pas été modifiés par cette correction.

## Validation

- Quatre reproductions exécutées avec les anciens modules worker/contrôleur sauvegardés : échec du membre valide d'un lot, effacement de l'aperçu, relancement de préparation vide, et `sqlite3.ProgrammingError` après fermeture. Les quatre échouent aux assertions attendues.
- 16 nouveaux cas : BLOB évincé/corrompu aux trois positions du lot ; fallback d'inférence borné et mapping conservé ; résultats trop courts ; heartbeat après échec d'un voisin ; source non demandée/capacité nulle ou occupée ; aperçu stable après éviction ; vrai thread de préparation en vol et libération de ses claims ; callbacks directs et différés après fermeture ; idempotence ; reprise de 105 posts après frontière à 100 ; protocole 75/redémarrage ; résultat modèle déjà en cache.
- Suite ciblée : **133 réussis** en 19,42 s.
- Suite complète : **1 438 réussis, 2 ignorés, 14 sous-tests réussis** en 130,71 s. Les tests ignorés sont les intégrations de modèles réels déjà conditionnées à `BOORUFLOW_RUN_MODEL_TESTS=1`.
- Nouveaux cas sous Qt Windows (`QT_QPA_PLATFORM=windows`) : **16 réussis** en 2,88 s.
- Ruff sur les fichiers Python concernés, compilation et `git diff --check` : réussis.
- Empreintes SHA-256 des quatre bases actives du checkout vérifiées avant/après : inchangées. Tests sur bases/cache/images temporaires et backends synthétiques ; aucune inférence CUDA ou requête réseau de production pour ces nouvelles reproductions.

Les preuves et sauvegardes locales sont sous `var/tmp/analysis-bugs-baseline` (`red-tests.txt`, `targeted-tests.txt`, `full-tests.txt`, `native-tests.txt`, `data-hashes.json` et sources avant correction). La portable existante a seulement été diagnostiquée. Elle n'a été ni reconstruite ni corrigée sur place ; la validation du futur binaire GPU reste à faire lors d'une reconstruction autorisée.
