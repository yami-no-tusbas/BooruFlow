# Entity Profiles et ouverture Gelbooru — 5 octobre 2026

Implémentation additive dans le checkout `refactor/pyside6-mvc`, avec conservation des modifications préexistantes. Aucun commit, push ou packaging. Les validations utilisent des bases temporaires et des transports simulés. La base réelle `var/state/entity_profiles.sqlite` n’a pas été créée pendant les tests.

## Utilisation

- Dans Tag Browser : clic droit sur un tag Gelbooru, puis **Analyser les tags récurrents…**. Une éventuelle forme canonical est utilisée comme entité.
- Dans Tagging : même action sur les chips de recherche et sur les lignes de tags de la review. L’action est aussi connectée dans le helper autonome.
- Le tag source est prérempli. Pour une catégorie `character`, la requête initiale propose `tag solo`, sans supposer un genre. Les autres catégories proposent simplement le tag. Cette classification reste une aide : un objet nommé classé `character` peut nécessiter de retirer `solo`. La requête est entièrement éditable.
- Choisir entre 1 et 10 000 **nouveaux** posts, puis Analyser / actualiser. Le nombre initial proposé est 100.
- Les datasets existants sont sélectionnables. Une requête différente crée un autre profil pour cette entité ; elle ne change pas rétroactivement la population du dataset précédent.
- Les onglets Attributs / Relations disposent de tri numérique, filtre texte, filtre de statut et boutons Garder / À vérifier / Ignorer. Le menu contextuel permet également d’exclure un tag globalement ou de retirer cette exclusion.
- Afficher les posts correspondants charge au plus 100 témoins échantillonnés, avec leurs miniatures à la demande. Un double-clic ouvre le post dans le navigateur configuré.

## Architecture

`domain/entity_profiles.py` définit les statuts, les exclusions initiales, les catégories relationnelles et les suggestions structurées. `application/entity_profiles.py` orchestre l’échantillonnage et la fusion des provenances pour la review. `infrastructure/entity_profile_repository.py` stocke les datasets dans une base locale dédiée, suivant les conventions SQLite du dépôt : WAL, clés uniques, foreign keys et busy_timeout.

Le réseau utilise le `GelbooruDapiClient` existant, avec une seule requête simultanée, des pages de 100 posts et une pause d’une seconde entre pages. Les erreurs transitoires utilisent des délais 2 / 5 / 10 secondes, cohérents avec Remote Discovery ; les erreurs d’authentification ne sont pas réessayées. Les attentes entre tentatives sont annulables.

Le cache partagé est `PostMetadataCache`, déjà utilisé par Auto Organize, à `var/cache/post_metadata.sqlite`. Les pages de recherche fournissent directement les tags : aucun appel individuel `id:…` n’est ajouté. Les catégories déjà en cache complètent les données disponibles ; la DB locale de tags est consultée en lecture seule via le lookup batch existant. Les métadonnées des nouveaux posts sont remises dans le cache commun. Il faut toujours demander les pages de recherche pour découvrir les IDs actuellement correspondants : le cache ne remplace pas la recherche distante.

Le dialogue utilise `DataTable`, la palette de tags, `PageStatus` et un `QThread` dédié. Les erreurs restent visibles dans le pied global, y compris avec les logs fermés. Seul le type d’exception est affiché, afin de ne pas exposer une URL contenant des identifiants API. Les miniatures réutilisent `ThumbnailLoader` et, lorsqu’elle existe, la mémoire cache de Tagging. Elles ne sont jamais téléchargées pendant le calcul statistique. Aucune image originale n’est acquise par cette analyse.

## Schéma de persistance

Base : `var/state/entity_profiles.sqlite`, version indépendante 1. Le schéma Image Analysis existant n’est pas migré par ce patch.

| Table | Données et garantie |
|---|---|
| `profiles` | ID, entity_tag, query, sample_count, last_run_new, last_run_state, created_at, updated_at. Identité unique `(entity_tag, query)`. |
| `profile_posts` | IDs vus et URL de miniature ; clé primaire `(profile_id, post_id)`. |
| `profile_post_tags` | Tags témoins de chaque post échantillonné ; clé `(profile_id, post_id, tag)`. Permet de retrouver les images ayant produit un attribut ou une relation. |
| `recurring_tags` | Tag, catégorie, kind attribute/relation, occurrences, statut keep/review/ignore ; clé `(profile_id, tag)`. |
| `global_exclusions` | Ensemble des tags exclus de tous les profils, modifiable et initialisé une seule fois. |
| `tagging_profile_decisions` | Décisions de review par `(item_id, tag)` et lien éventuel vers une observation manuelle existante. Les IDs d’items se rapportent à la base Image Analysis de cette installation. |

La fréquence est calculée par `occurrences / sample_count`, sans moyenne entre datasets. Les données témoins ne dépendent pas de l’état ultérieur du cache partagé. Aucune contrainte d’exclusivité entre attributs n’empêche de garder simultanément staff, scythe ou sword. Des tables de groupes/looks pourront ultérieurement se rattacher aux IDs de profils, tags et posts sans changer le principe d’échantillonnage.

## Refresh incrémental exact

1. Charger les IDs déjà vus et démarrer un nouveau run ; `last_run_new` repart à zéro, `sample_count` demeure cumulatif.
2. Repartir de la page zéro de la requête persistée.
3. Dédupliquer les IDs dans la page et ignorer ceux déjà présents dans ce profil.
4. Retenir au maximum les nouveaux IDs encore nécessaires au quota demandé.
5. Enregistrer, dans une transaction, les IDs retenus, leurs tags témoins, les occurrences et les deux compteurs. La contrainte unique protège aussi contre un double traitement ou deux connexions concurrentes.
6. Continuer la pagination tant que le quota n’est pas atteint. Une page vide ou une dernière page courte indique une recherche épuisée ; une page complète répétée à l’identique arrête le run en erreur, sans prétendre que la recherche est épuisée.

Un post ne compte qu’une fois par profil, et un tag qu’une fois par post. Un même post peut compter une fois dans chacun de plusieurs profils, avec ses métadonnées partagées en cache.

Une panne au milieu de l’écriture annule toute la page. Les pages déjà commitées survivent à l’annulation, à l’erreur et à la fermeture. Relancer un refresh recherche à nouveau X nouveaux posts : le run précédent reste dans le total, sans recompter ses IDs. Le patch ne reprend donc pas un curseur de page périmé et ne promet pas de reprendre automatiquement le quota résiduel d’un run interrompu.

L’annulation est coopérative : elle interrompt les pauses et empêche la prochaine écriture/requête. Une requête DAPI déjà en cours peut attendre le timeout du client existant, au maximum 45 secondes. Fermer le dialogue pendant ce temps demande l’annulation et attend la fin du worker avant de le détruire.

## Exclusions et décisions

Les exclusions initiales sont volontairement limitées : smile, open_mouth, looking_at_viewer, blush, standing, sitting, outdoors, indoors, sky, cloud, tree, grass, simple_background. Hairclip, hair_ornament, hairband, ribbon, brooch, tattoo, scar, glasses, vêtements, chaussures et armes ne sont pas exclus automatiquement.

Les statistiques restent conservées pour les tags globalement exclus. Ils sont masqués des vues ordinaires, accessibles via le filtre « Exclus globalement », et toujours retirés des suggestions, même si leur statut de profil est Keep. Retirer une exclusion rétablit la décision de profil précédente. Un Ignore spécifique n’affecte aucun autre profil. Un refresh ne modifie jamais les statuts Keep/Ignore/Review existants. La fréquence seule n’accepte aucun tag.

## Relations directionnelles

Les catégories character et copyright, ainsi que artist/species/lore lorsqu’elles sont connues, vont dans Relations. Les autres tags vont dans Attributes. Une catégorie absente reste unknown : aucune catégorie n’est inventée à partir du nom du tag.

Chaque occurrence mesure la relation depuis le dataset source uniquement. `P(Fate | Bardiche)` ne crée pas une relation inverse dans le profil Fate. Les profils de copyright peuvent suggérer des personnages, et les profils de personnages peuvent suggérer des copyrights, à condition que l’utilisateur les ait marqués Keep.

## Tagging et WD14

Les seuls seeds sont les tags observés/source conservés et les observations explicitement acceptées par l’utilisateur, y compris les ajouts manuels. Les tags simplement suggérés ne deviennent jamais des seeds. Fate peut donc suggérer Bardiche sans charger le profil Bardiche ; accepter Bardiche rend ses propres suggestions disponibles. Undo retire cette confirmation et désactive les suggestions qui en dépendent ; Redo la restaure.

Les propositions des profils sont des lignes virtuelles `entity:<tag>` tant qu’elles ne sont pas acceptées. La confirmation réutilise `ImageAnalysisWorkflow.add_manual_tag()` et ses observations manuelles persistantes. Le rejet ne crée aucune observation manuelle. Les décisions peuvent être revues et suivent le mécanisme d’undo/redo existant. Les ajouts confirmés entrent dans le résumé et la validation locale du Batch existants ; aucune publication ni ajout automatique à Gelbooru n’est déclenché.

Lorsqu’un post sans analyse a déjà des suggestions de profil, Tagging peut créer uniquement son item de métadonnées via le repository existant, avec `request_analysis=False`, pour permettre une review sans téléchargement ni inférence WD14. Une nouvelle analyse WD14 reste disponible par l’action existante. Le helper autonome conserve ses limites actuelles de review, mais son action d’analyse ouvre bien le même dialogue statistique.

Pour un tag proposé par plusieurs sources, la ligne reste unique et affiche les provenances. Le score WD14 d’origine reste affiché ; les fréquences de chaque profil figurent séparément dans le texte et les données `profile_scores`. Le repository WD14 et ses scores persistants sont inchangés. Aucun score combiné ni moyenne arithmétique n’est introduit. Les confirmations depuis un profil sont distinguées des ajouts manuels ordinaires.

## Bug navigateur et audit

Cause : `TagBrowserPage._open_row()`, utilisé à la fois par Open tag et Open canonical tag, appelait directement `QDesktopServices.openUrl()`. Le constructeur et le builder de page ne transmettaient aucun BrowserLauncher, si bien que les paramètres System / Dedicated / Custom n’étaient jamais consultés.

Correction : la page construit l’URL avec `BooruSiteDefinition.search_url()` puis délègue les actions Gelbooru au BrowserLauncher commun. MainWindow injecte son launcher configuré, qui est également mis à jour par Options. La variante canonical utilise bien le canonical. Le fallback pour une page isolée reste un BrowserLauncher en mode System ; il ne contourne pas ce service.

L’audit a aussi corrigé l’ouverture d’une source Gelbooru dans Image Finder et transmis le launcher configuré au helper Tagging autonome. Pixiv, e621, les fichiers locaux et les liens d’installation conservent leur chemin adapté. Les pages explicitement reliées au navigateur interne gardent cette navigation interne ; les transports de publication/CDP ne sont pas modifiés. La copie historique non importée `similar_artists_controller - Copie.py` n’a pas été modifiée.

## Fichiers de ce patch

Créés :

- `src/booruflow/domain/entity_profiles.py`
- `src/booruflow/application/entity_profiles.py`
- `src/booruflow/infrastructure/entity_profile_repository.py`
- `src/booruflow/infrastructure/schema/entity_profiles_v1.sql`
- `src/booruflow/presentation/pyside6/entity_profiles.py`
- `tests/unit/test_entity_profiles.py`
- `tests/unit/test_entity_profiles_ui.py`
- `docs/entity-profiles-report.md`

Ajouts ciblés dans des fichiers existants, dont certains étaient déjà modifiés avant cette tâche :

- `src/booruflow/application/tagging.py` : token de review entity.
- `src/booruflow/presentation/pyside6/tag_browser_page.py` : launcher et action de profil.
- `src/booruflow/presentation/pyside6/tagging_page.py` : menus des tags, signal de profil, mapping désactivé pour les lignes virtuelles.
- `src/booruflow/presentation/pyside6/tagging_controller.py` : injection, confirmation, undo/redo, review de métadonnées seule.
- `src/booruflow/presentation/pyside6/tagging_suggestions.py` : tooltip de provenance.
- `src/booruflow/presentation/pyside6/main_window.py` : wiring et fermeture sûre du dialogue.
- `src/booruflow/presentation/pyside6/image_finder_page.py` : délégation Gelbooru.
- `src/booruflow/presentation/pyside6/tagging_standalone.py` : launcher configuré et ouverture du dialogue.
- `resources/i18n/en.json` et `resources/i18n/fr.json` : 46 clés Entity Profiles, avec parité EN/FR.

## Validation

- Suite complète : **1 262 réussis, 2 ignorés, 14 sous-tests réussis**, en 82,22 secondes.
- Dernière validation ciblée, après les derniers ajustements de wiring/cache et le test supplémentaire du helper autonome : **48 réussis**. Elle couvre les deux nouveaux fichiers, BrowserLauncher et le helper autonome.
- Tests nouveaux : création/réouverture, premier lot et compteurs, doublons internes/entre runs, changement d’ordre distant, totaux cumulatifs, Keep/Ignore persistants, exclusions et restauration, relations et direction, formes multiples, panne transactionnelle, reprise après erreur, annulation, page répétée, initialisation/écriture concurrentes, catégories locales en lecture seule et cache partagé.
- Tests Qt : provenance et scores séparés, seeds non récursifs, confirmation/undo/redo, rejet sans ajout, review sans WD14, requêtes et vues EN/FR, miniatures uniquement à l’inspection, fermeture pendant une requête, wiring MainWindow/helper, menus raw/canonical avec launcher System/Dedicated/Custom, source Gelbooru d’Image Finder.
- Ruff passe sur tous les fichiers de ce patch ; compileall et `git diff --check` passent. Le Ruff global signale encore trois écarts préexistants hors périmètre : un import dans `icons.py`, et un import/with imbriqué dans `test_gelbooru_tag_importer.py`.
- Rendu final sous le plugin Qt **windows**, 1000 × 700, EN/FR et Attributes/Relations, avec données temporaires. Captures d’inspection dans `var/tmp/entity-profile-render/`. Les tests automatisés de workflow sont offscreen.
- Les bases actives Image Analysis/cache sont protégées par les guards de tests existants. Aucun appel réel Gelbooru, lancement réel de navigateur dans les tests, publication, commit, push ou packaging.

## Limites conservées pour une prochaine itération

Pas de clustering de looks/forms, lift/spécificité ou score fusionné. Pas de profil e621 dans ce MVP. L’inspection est limitée aux 100 premiers témoins sélectionnés, sans pagination supplémentaire. Pas d’enrichissement distant des catégories absentes du catalogue local/cache, ni de recalcul rétroactif automatique de catégories pour les anciens posts. Les échanges API réels et le comportement d’un vrai navigateur dédié restent à valider en usage connecté ; les tests de transport utilisent des réponses simulées et des launchers à exécution capturée.
