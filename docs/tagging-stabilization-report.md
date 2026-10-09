# Stabilisation Tagging — 8 octobre 2026

Travail réalisé dans `D:\python\BooruFlow`, branche `refactor/pyside6-mvc`, en conservant les modifications antérieures. Aucun commit, push, packaging ou lancement sur les bases réelles.

## Causes et corrections

| Point | Cause vérifiée | Correction |
|---|---|---|
| 1. Résidence du cache | Les métadonnées SQLite restaient `resolved` après éviction du BLOB par le LRU. La préparation pouvait produire plusieurs images avant leur consommation ; les références des tâches actives n'étaient pas protégées. | Les clés des tâches demandées en attente/en traitement sont exclues de l'éviction. Si elles occupent la capacité, l'acquisition libère sa réservation et attend un progrès de la file. Un `ImageCacheMissError` invalide la résolution et demande **une seule** réacquisition par la préparation normale ; le compteur est persisté, donc un redémarrage du worker ne renouvelle pas ce droit. Une deuxième perte échoue définitivement jusqu'à une relance explicite. Les autres membres du batch continuent. |
| 2. Focus et apparitions | Chaque reconstruction des propositions appelait `setFocus()`. Le rendu recréait aussi les cellules et relançait le décodage d'un aperçu identique. Certains appels de rafraîchissement pouvaient réafficher Review après un retour à la grille. | Suppression du focus passif, conservation des cellules si les données sont identiques, conservation de l'aperçu par identité, vérification du post et de la vue avant rendu. Les snapshots Batch portent également une génération et un site ; les réponses obsolètes sont ignorées. |
| 3. Batch | Le clic réalisait les lectures/décodages et créait sept `QTableWidgetItem` par entrée sur le thread GUI. Le rafraîchissement pouvait également reconstruire la grille masquée. Le chronométrage existant ne mesurait ni tout le clic ni les lectures/layouts. | Lecture sur une connexion SQLite **read-only propre au worker**, avec snapshot transactionnel et demandes coalescées. Tableau `QTableView` à modèle, cellules formatées uniquement lorsqu'elles sont demandées par Qt. Compteurs avant population ; grille masquée réconciliée au retour, sans fetch supplémentaire. Pas de reset sur un snapshot identique ; sélection et défilement conservés. Instrumentation des lectures/tri SQL, décodage, filtre, modèle, compteur, clic et layout. |
| 4. Filtres actifs | La hauteur réservée était occupée par un éditeur vide avec un simple libellé. | Panneau délimité « Filtres actifs », texte « Aucun filtre actif », hauteur fixe d'une rangée et débordement horizontal. Un signal de présentation suit aussi les changements programmatiques silencieux, sans déclencher de logique métier. |
| 5. Clic Curated | Un simple clic sur un enfant modifiait sa sélection **provisoire**, ce qui produisait le gras et le symbole rempli ; il ne créait pas encore d'observation acceptée persistée. | Le clic sélectionne uniquement. Le double-clic conserve l'ouverture du tag, avec nom sans indentation. Espace/A/R et boutons explicites conservent leur fonction ; le raccourci Espace de la page a été corrigé également. |
| 6. Décisions partagées | La fusion visuelle des sources conservait la décision du premier représentant ; une nouvelle observation WD14 naissait `unreviewed`, indépendamment du choix manuel/Curated/Entity. | État autoritaire persistant `(item_id, site, tag canonique)`, hérité par les nouvelles prédictions et rendu commun aux propositions. Synchronisation des observations existantes et des décisions de retrait. Les alias sont résolus au chargement du catalogue. Confirmation Curated et undo/redo restaurent cet état dans la même transaction que les observations. Les anciens choix servent de fallback jusqu'à une décision explicite. |
| 7. Police | `badge` et `police_hat` étaient déjà présents ; les deux concepts de badge sont des tags réels distincts. | Ajout de `baton_(weapon)` et `police_badge`, en conservant `badge` et `police_hat`. Vérification des bases locales en lecture seule, sans inventer d'alias. |
| 8. Identité | Le titre ne montrait que le nom de l'application ; le fichier généré contenait les composants de version, sans identité explicite. | Le générateur inscrit maintenant l'identité exacte du dossier final dans `BUILD_INFO.txt`, y compris un éventuel suffixe d'unicité. Le runtime lit ce champ, ou reconstruit l'identité des anciennes builds depuis leurs métadonnées. La même valeur apparaît dans le titre et le journal ; elle survit au changement de langue. Le checkout affiche une identité Git de source calculée, sans version codée en dur. |
| 9. Avertissements répétés | Le catalogue est validé au démarrage puis après la préparation des bases/alias ; la page paresseuse peut encore le recharger. Les mêmes chaînes invalides étaient donc journalisées plusieurs fois. | Déduplication des messages Curated pour l'instance de fenêtre/bootstrap, avec un ensemble partagé entre MainWindow et Tagging. La validation reste exécutée et toute nouvelle alerte distincte reste visible. Aucun filtre global des logs. |

La migration **v27** ajoute uniquement le compteur de récupération et la table des décisions canoniques. Elle est transactionnelle et revérifie la version sous verrou. Elle a été exercée sur des bases temporaires, pas sur les données actives.

## Vérification locale de Police

Catalogue `D:\python\BooruFlow\data\databases\g_tags_260810.db`, alias `gelbooru_aliases.db`, ouverts en lecture seule :

| Tag canonique | Publications dans le catalogue | Catégorie |
|---|---:|---|
| `baton_(weapon)` | 1 288 | general |
| `badge` | 14 958 | general |
| `police_badge` | 2 091 | general |
| `police_hat` | 6 672 | general |

L'alias actif trouvé pour le bâton est `truncheon → baton_(weapon)`. `badge` et `police_badge` ne sont pas des alias l'un de l'autre dans ce catalogue.

## Validation

- **Suite complète : 1 452 passed, 2 skipped, 14 subtests passed**, 111,73 s. Journal : [full-tests-final.txt](D:/python/BooruFlow/var/tmp/tagging-stabilization-baseline/full-tests-final.txt).
- **Qt ciblé Windows natif : 57 passed**, 15,26 s. Clics réels QTest, focus, langue/thème, Batch, navigation, récupération et arrêt. Journal : [native-tests.txt](D:/python/BooruFlow/var/tmp/tagging-stabilization-baseline/native-tests.txt).
- **Stress natif renforcé et autres nouvelles régressions : 14 passed**, après la suite complète. [Résultats du stress](D:/python/BooruFlow/var/tmp/tagging-stabilization-baseline/native/stress-results.json) : grille de **500 résultats**, **40 cycles** (ouverture arbitraire, Next/Previous, grille, réouverture), préparation/analyse sur un thread indépendant, **64 analyses terminées**, cache limité à **160 octets**, **154 octets** utilisés à la fin, **0 récupération automatique et 0 ImageCacheMissError inattendu**.
- Les suppressions/corruptions **injectées intentionnellement** produisent les diagnostics attendus : première perte récupérable, deuxième perte terminale sans nouvelle réclamation automatique. Les six cas couvrent première, centrale et dernière image du batch ; une autre régression vérifie la réussite de la récupération après redémarrage du worker.
- Batch de **10 000 entrées** : comparaison isolée du code sauvegardé avant/après, même machine et données synthétiques. Population + compteur **186,1 → 10,9 ms** ; clic complet **191,6 → 15,9 ms** ; clic + layout **209,0 → 28,4 ms**. Cette comparaison exclut les lectures réelles ; un test indépendant bloque volontairement le reader et vérifie que les événements GUI continuent pendant ce blocage.
- **Ruff : OK sur tous les fichiers Python de ce correctif.** `ruff check src tests` relève 3 problèmes préexistants : import de `icons.py`, import et `with` imbriqués de `test_gelbooru_tag_importer.py`. Le balayage de tout le dépôt relève 124 problèmes existants, notamment dans `legacy` et les outils. Ces fichiers hors périmètre n'ont pas été modifiés.
- `compileall src tests`, imports des nouveaux modules, parseur PowerShell de `build_portable.ps1` et `git diff --check` : OK.
- Empreintes SHA-256 identiques avant/après pour `var/state/image_analysis.sqlite`, `var/cache/image_analysis_cache.db`, `var/state/entity_profiles.sqlite`, `var/cache/post_metadata.sqlite`. Les garde-fous de tests interdisent leur ouverture par les fixtures.

## Limites de reproduction

Le gel prolongé et le retard exact du compteur de la build portable n'ont pas été reproduits à l'identique sur ses données. Le chemin synchrone bloquant a été identifié dans le code et mesuré sur une grande file synthétique ; la nouvelle lecture bloquée et la population du modèle ont été testées séparément. Le stress utilise le cache et le worker réels, avec un backend WD14 synthétique et des images générées : il ne constitue pas un test CUDA, réseau Gelbooru ou redémarrage du portable.

Le dossier nommé `BooruFlow-2026.10.07-g62e950e-dirty-1241-gpu` contient actuellement un `BUILD_INFO.txt` daté du **8 octobre 11:36**. Le lecteur reconstruit donc `2026.10.08-g62e950e-dirty-1136-gpu`. Cela distingue le contenu de l'exécutable du nom devenu historique de son dossier. Aucune modification n'a été faite au portable.

## Fichiers de ce passage

Les modifications déjà présentes avant ce passage ne sont pas incluses dans cette liste.

| Domaine | Fichiers sous `D:\python\BooruFlow` |
|---|---|
| Cache / récupération / décisions | `src/booruflow/infrastructure/image_analysis_cache.py`, `image_analysis_repository.py`, `image_sources.py`, `remote_pixels.py`, nouveau `schema/image_analysis_v27.sql` ; `src/booruflow/worker/image_analysis.py` |
| Contrôleurs / fenêtre | `src/booruflow/presentation/pyside6/image_analysis_controller.py`, `tagging_controller.py`, `curated_tagging.py`, `main_window.py` |
| Présentation | `src/booruflow/presentation/pyside6/tagging_page.py`, `tagging_legacy_page.py`, `tagging_suggestions.py`, nouveau `tagging_batch_table.py` |
| Identité / packs / traductions | `src/booruflow/runtime.py`, `tools/build_portable.ps1`, `config/curated_tag_profiles/roles_professions.json`, `resources/i18n/en.json`, `resources/i18n/fr.json` |
| Tests / rapport | nouveau `tests/unit/test_tagging_stabilization.py` (**14 cas**) ; adaptations de `test_image_analysis_regressions.py`, `test_image_analysis_repository.py`, `test_wd14_score_cache.py`, `test_pyside6_workflows.py`, `test_gelbooru_html.py` ; ce rapport |

Les adaptations des fixtures historiques restaurent réellement les anciennes colonnes/tables avant de rejouer les migrations ; elles ne contournent pas les erreurs de schéma. Le test des actions Batch synchronise son snapshot et attend l'arrêt de son worker avant de fermer sa base temporaire.

Captures et mesures inspectables : `D:\python\BooruFlow\var\tmp\tagging-stabilization-baseline` (état avant, comparaison Batch, panneau avec filtres, stress natif final). Aucun commit, push ou build portable n'a été produit.
